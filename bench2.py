"""
HW7 benchmark: 6 shifts x 9 case patterns x 3 configurations.

  A "capa":      planning agent with the full layer (all incidents, loans in force, haulage, travel times)
  B "por_falla": layer, but one focused planner per incident, each blind to the others
  C "archivos":  one agent with the raw files (plan, map, dispatch, radio, CMMS), no layer

At every decision moment of a case, the configuration decides; the evaluator (eval/clave_v2.py)
measures the expected ounces of that decision against the best possible one with the same
information. Results go to runs2/<run>/, one JSON per case, so the run can be resumed.

  .venv/bin/python bench2.py --run nombre [--turnos 0 1 2] [--patrones 1 3] [--configs A B C]
"""

import argparse
import csv
import io
import json
import re
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "eval"))

import mina_v2
from agents import prompts2
from agents.runner import run_agent
from clave_v2 import PATRONES, clave, hhmm
from mina_v2 import FIN, MODELOS, OZ, Red, generar

MODELO = "sonnet"

REGLAS = [
    {"id": "LOADER-SOURCE-01", "text": "Only a stope in phase 'carguio' (loading) or a development heading can lend its loader. Stopes in drilling or fill have no loader to lend."},
    {"id": "DEV-COST-01", "text": "Taking a loader from a development heading costs, per hour away, the heading's value_oz_per_loader_hour (critical-path headings are expensive, infrastructure headings cheap)."},
    {"id": "RESERVE-01", "text": "A reserve stope can only be activated with the loader and crew of a stopped stope, never by taking a loader from a producing place."},
    {"id": "CONFIRM-01", "text": "If the radio and the CMMS disagree about a loader's status, do not move anything for that place: ask the area chief to confirm."},
    {"id": "RETURN-01", "text": "When a repair ends, the layer decides whether the loan goes home or the repaired loader takes the lender's place. You do not need to plan that."},
    {"id": "PM-01", "text": "The loader in preventive maintenance is not available before its available_from time."},
]


def tasa(mina, modelo, lugar):
    # t/h efectivas de un modelo de scoop en un lugar (baja con la distancia al acopio)
    x = {**mina["tajos"], **mina["reservas"]}[lugar]
    return round(MODELOS[modelo]["t_h"] * min(1.0, 100 / (100 + 0.0015 * (x["dist_acopio_m"] - 100) * 100)), 1)


def eventos(T, t):
    # Lo que las fuentes informaron hasta ahora, en el formato de eventos de la capa
    out = []
    for i, h in enumerate(T.hechos):
        n = int(h["scoop"].split("-")[1])
        e = {"id": f"EV-{i + 1:04d}", "time": h["hora"], "place": h["lugar"], "loader": h["scoop"]}
        if h["tipo"] == "falla":
            e.update(type="equipment_failure", source="radio+cmms", confidence=0.95,
                     summary=f"{h['scoop']} down at {h['lugar']}: hydraulic failure. Maintenance opened a work order.",
                     raw_text=f"Central, aquí scoop {n} en {h['lugar'][3:]}, se me fue la hidráulica, quedo parado.")
        elif h["tipo"] == "actualizacion_estimado":
            e.update(type="repair_update", source="cmms", confidence=0.8,
                     summary=f"Repair estimate for {h['scoop']} revised: part not in stock, {h['rango_h'][0]}-{h['rango_h'][2]} h from the failure.")
        elif h["tipo"] == "tajo_detenido":
            e.update(type="stope_stopped", source="radio", confidence=0.9,
                     summary=f"{h['lugar']} stopped: insufficient ventilation. Ventilation crew estimates {h['rango_h'][0]}-{h['rango_h'][2]} h. Loader {h['scoop']} and crew idle.",
                     raw_text=f"Aquí el jefe de turno, paramos el {h['lugar'][3:]}, no da la ventilación, el scoop {n} queda libre.")
        elif h["tipo"] == "aviso_radio_no_confirmado":
            e.update(type="equipment_status_conflict", source="radio vs cmms", confidence=0.4,
                     summary=f"Radio says {h['scoop']} is stopped at {h['lugar']}, but the CMMS shows it operating; its last work order was closed 20 minutes ago. Unconfirmed.",
                     raw_text=f"...el scoop {n} está parado en el {h['lugar'][3:]}...")
        elif h["tipo"] == "reparado":
            e.update(type="equipment_repaired", source="cmms", confidence=0.95, summary=f"{h['scoop']} repaired.")
        out.append(e)
    return out


def estado(T, t):
    """El estado del turno en este momento, tal como lo entrega la capa (configuraciones A y B)."""
    m, red = T.mina, T.red
    st = T.estado_en(t)
    casa_inv = {c: s for s, c in T.casa.items()}
    aqui = {}
    for s, p in T.pos.items():
        if p:
            aqui.setdefault(p, []).append(s)
    lugares = list(m["tajos"]) + list(m["reservas"]) + list(m["desarrollos"]) + ["TALLER-564"]
    caidos = {c["scoop"]: k for k, c in T.caidos.items()}
    loaders = []
    for s, x in m["scoops"].items():
        if s in caidos:
            status, at = "down", caidos[s]
        elif T.libre_desde[s] > t:
            status, at = "preventive_maintenance", "TALLER-564"
        elif T.pos[s] != T.casa[s]:
            status, at = "on_loan", T.pos[s]
        else:
            status, at = "operating", T.pos[s]
        if T.casa[s] in T.detenidos and status == "operating":
            status = "idle (home stope stopped)"
        loaders.append({"id": s, "model": x["modelo"], "bucket_t": MODELOS[x["modelo"]]["t"], "home": T.casa[s],
                        "at": at, "status": status, "available_from": hhmm(max(T.libre_desde[s], 7.0))})
    cap = {n: mm["camiones"] * mina_v2.CAMION_T * mina_v2.CAMION_EFECTIVO / red.ciclo_camion_h(m, n)
           for n, mm in m["minas"].items()}
    entrada = {n: 0.0 for n in m["minas"]}
    for s, p in T.pos.items():
        if p in m["tajos"] and m["tajos"][p]["fase"] == "carguio" and s not in caidos and p not in T.detenidos:
            entrada[m["tajos"][p]["mina"]] += tasa(m, m["scoops"][s]["modelo"], p)
    return {
        "shift_time": hhmm(t),
        "shift": {"day_shift": "06:00-18:00", "loading_blocks": "07:00-11:00 and 11:30-17:00 (lunch 11:00-11:30)",
                  "oz_formula": "oz = tonnes x grade_gpt / 31.1035"},
        "value_metric": "Ounces hauled to surface by 17:00 plus development value (oz eq). Ore left in stockpiles or stopes carries over to the next shift and does not count.",
        "operating_rules": REGLAS,
        "sources": [{"name": "radio", "status": "online"}, {"name": "cmms", "status": "online"},
                    {"name": "dispatch", "status": "online"}],
        "loans_in_force": [{"loader": s, "home": T.casa[s], "at": T.pos[s]} for s in m["scoops"]
                           if T.pos[s] and T.pos[s] != T.casa[s] and s not in caidos],
        "preventive_maintenance": [{"loader": s, "available_from": hhmm(T.libre_desde[s])}
                                   for s in m["scoops"] if T.libre_desde[s] > t],
        "events": eventos(T, t),
        "stopes": [{"id": k, "mine": x["mine"] if "mine" in x else x["mina"], "level": x["nivel"], "phase": x["fase"],
                    "grade_gpt": x["ley"], "broken_ore_remaining_t": round(st["tajo_t"][k]),
                    "home_loader": casa_inv.get(k), "loaders_here_now": aqui.get(k, []),
                    "stopped": k in T.detenidos,
                    "loading_rate_t_h": {mo: tasa(m, mo, k) for mo in MODELOS}} for k, x in m["tajos"].items()],
        "reserves": [{"id": k, "mine": x["mina"], "level": x["nivel"], "grade_gpt": x["ley"],
                      "broken_ore_t": round(st["tajo_t"][k]), "activation_h": x["activacion_h"],
                      "loading_rate_t_h": {mo: tasa(m, mo, k) for mo in MODELOS}} for k, x in m["reservas"].items()],
        "headings": [{"id": k, "mine": x["mina"], "level": x["nivel"], "type": x["tipo"],
                      "value_oz_per_loader_hour": round(x["valor_oz_h"] * mina_v2.MULT_DESARROLLO, 2),
                      "home_loader": casa_inv.get(k)} for k, x in m["desarrollos"].items()],
        "loaders": loaders,
        "haulage": [{"mine": n, "stockpile_t": round(st["acopio_t"][n]), "stockpile_oz": round(st["acopio_oz"][n], 1),
                     "stockpile_capacity_t": mm["acopio_cap_t"], "trucks": mm["camiones"],
                     "truck_capacity_to_surface_t_h": round(cap[n]), "loader_inflow_t_h": round(entrada[n])}
                    for n, mm in m["minas"].items()],
        "travel_h": {a: {b: red.viaje(a, b) for b in lugares} for a in lugares},
        "repair_estimates": [{"place": k, "loader": c["scoop"], "repair_location": c["lugar"] if c["lugar"] != "en sitio" else "in place",
                              "low_h_clock": hhmm(c["rango_abs"][0]), "likely_h_clock": hhmm(c["rango_abs"][1]),
                              "high_h_clock": hhmm(c["rango_abs"][2])} for k, c in T.caidos.items()],
    }


def archivos(T, t, carpeta):
    """Los mismos datos como archivos crudos de cada sistema (configuración C): sin capa, sin tiempos calculados."""
    m = T.mina
    carpeta.mkdir(parents=True, exist_ok=True)
    plan = {"shift": "day shift 06:00-18:00, loading 07:00-11:00 and 11:30-17:00",
            "loader_models": {k: {"bucket_t": v["t"], "effective_t_h_at_100m": v["t_h"],
                                  "note": "rate falls with distance to the level stockpile: rate = base x 100 / (100 + 0.15 x (dist_m - 100))"}
                              for k, v in MODELOS.items()},
            "stopes": [{"id": k, "mine": x["mina"], "level": x["nivel"], "phase": x["fase"], "grade_gpt": x["ley"],
                        "broken_ore_start_of_shift_t": x["t_disp"], "dist_to_stockpile_m": x["dist_acopio_m"]}
                       for k, x in m["tajos"].items()],
            "reserves": [{"id": k, "mine": x["mina"], "level": x["nivel"], "grade_gpt": x["ley"], "broken_ore_t": x["t_disp"],
                          "activation_h": x["activacion_h"], "dist_to_stockpile_m": x["dist_acopio_m"]} for k, x in m["reservas"].items()],
            "development_headings": [{"id": k, "mine": x["mina"], "level": x["nivel"], "type": x["tipo"],
                                      "value_oz_per_loader_hour": round(x["valor_oz_h"] * mina_v2.MULT_DESARROLLO, 2)}
                                     for k, x in m["desarrollos"].items()],
            "loader_assignment": {s: {"model": x["modelo"], "place": x["casa"]} for s, x in m["scoops"].items()},
            "preventive_maintenance": m["mantenimiento"],
            "haulage": {n: {"trucks": mm["camiones"], "truck_payload_t": mina_v2.CAMION_T,
                            "truck_effective_factor": round(mina_v2.CAMION_EFECTIVO, 3),
                            "level_stockpile_capacity_t": mm["acopio_cap_t"]} for n, mm in m["minas"].items()},
            "value_metric": "oz hauled to surface by 17:00 plus development value; stockpiles carry over"}
    (carpeta / "plan_turno.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1))
    mapa = {"speeds_kmh": mina_v2.VEL, "fixed_manoeuvre_h": mina_v2.MANIOBRA_H,
            "edges": [{"a": a, "b": b, "m": d, "type": ty} for a, b, d, ty in m["aristas"]],
            "note": "places on the same level and side of the drift are joined along the drift; truck cycle = 0.1 h load + up at 7 km/h + 0.05 h dump + down at 12 km/h + 0.1 h queue, from mid depth"}
    (carpeta / "mapa_mina.json").write_text(json.dumps(mapa, ensure_ascii=False, indent=1))
    st = T.estado_en(t)
    despacho = {"time": hhmm(t),
                "mucked_so_far_t": {k: round(x["t_disp"] - st["tajo_t"][k]) for k, x in m["tajos"].items()},
                "level_stockpiles_t": {n: round(v) for n, v in st["acopio_t"].items()},
                "level_stockpiles_oz": {n: round(v, 1) for n, v in st["acopio_oz"].items()},
                "hauled_to_surface_t": round(st["t_sup"]),
                "loader_positions": {s: (T.pos[s] or "down") for s in m["scoops"]}}
    (carpeta / f"despacho_{hhmm(t).replace(':', '')}.json").write_text(json.dumps(despacho, ensure_ascii=False, indent=1))
    ev = eventos(T, t)
    radio = [f"{e['time']} {e.get('raw_text', '')}" for e in ev if e.get("raw_text")]
    (carpeta / "radio.txt").write_text("\n".join(radio) + "\n")
    buf = io.StringIO(); w = csv.writer(buf)
    w.writerow(["ot", "equipment", "place", "opened", "status", "estimate_low_h", "estimate_likely_h", "estimate_high_h", "repair_location", "note"])
    for i, e in enumerate(T.hechos):
        if e["tipo"] == "falla":
            w.writerow([f"OT-{5100 + i}", e["scoop"], e["lugar"], e["hora"], "open", *e["rango_h"], e["lugar_reparacion"], "hydraulic"])
        elif e["tipo"] == "actualizacion_estimado":
            w.writerow([f"OT-{5100 + i}", e["scoop"], e["lugar"], e["hora"], "open (revised)", *e["rango_h"], "", f"part not in stock; hours from failure at {e['desde_falla']}"])
        elif e["tipo"] == "aviso_radio_no_confirmado":
            w.writerow([f"OT-{5000 + i}", e["scoop"], e["lugar"], hhmm(max(7.0, t - 1.5)), "closed 20 min ago", "", "", "", "", "equipment operational"])
        elif e["tipo"] == "reparado":
            w.writerow([f"OT-{5100 + i}", e["scoop"], e["lugar"], e["hora"], "closed", "", "", "", "", "repaired"])
    (carpeta / "cmms_ordenes.csv").write_text(buf.getvalue())
    (carpeta / "reglas.json").write_text(json.dumps(REGLAS, ensure_ascii=False, indent=1))


def a_asignacion(T, d, extra):
    """Traduce la decisión del agente a la asignación que entiende el evaluador, y anota violaciones."""
    asign, violaciones = {}, []
    usados = []
    for x in d.get("loans") or []:
        s, to = x.get("loader"), x.get("to")
        usados.append(s)
        if to in getattr(T, "dudosos", set()):
            violaciones.append(f"movió {s} a {to} por un aviso no confirmado")
        elif to in T.caidos and s in T.mina["scoops"]:
            asign[to] = s
        elif to != T.casa.get(s):
            violaciones.append(f"préstamo inválido {s} -> {to}")
    for x in d.get("reserve_activations") or []:
        s, r = x.get("loader"), x.get("reserve")
        usados.append(s)
        if s in T.mina["scoops"] and T.casa.get(s) in T.detenidos and r in T.mina["reservas"]:
            asign[s] = r
        else:
            violaciones.append(f"reserva inválida {s} -> {r} (regla RESERVE-01)")
    if len(usados) != len(set(usados)):
        violaciones.append("un scoop asignado a dos lugares")
    for to, s in list(asign.items()):
        if to in T.caidos:
            casa = T.casa[s]
            if s in {c["scoop"] for c in T.caidos.values()} or T.libre_desde[s] > extra["t"] + 0.01 or \
                    (casa in T.mina["tajos"] and T.mina["tajos"][casa]["fase"] != "carguio"):
                violaciones.append(f"{s} no se puede prestar (caído, en mantenimiento o en ciclo combinado)")
                asign.pop(to)
    pedidos = {x.get("place") for x in d.get("confirmations_requested") or []}
    return asign, violaciones, sorted(getattr(T, "dudosos", set()) & pedidos)


def preparar_db(path, st):
    con = sqlite3.connect(path)
    con.executescript("""CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS proposals (id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT, focus TEXT, body TEXT, at TEXT);
        CREATE TABLE IF NOT EXISTS tool_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT, tool TEXT, args TEXT, ok INTEGER, at TEXT);""")
    con.execute("INSERT OR REPLACE INTO meta VALUES ('state', ?)", (json.dumps(st, ensure_ascii=False),))
    con.commit()
    return con


def politica(config, carpeta, extras):
    """Crea la función que decide en cada evento llamando a los agentes de la configuración."""
    def decide(T, t, ctx):
        paso = carpeta / ctx["hora"].replace(":", "")
        paso.mkdir(parents=True, exist_ok=True)
        corridas, decisiones = [], []
        if config in ("A", "B"):
            st = estado(T, t)
            con = preparar_db(paso / "layer.db", st)
            if config == "A":
                focos = [""]
            else:
                # Un planificador por cada incidente de esta hora, cada uno sin ver los otros
                focos = sorted({h["lugar"] for h in T.hechos if h["hora"] == ctx["hora"] and h["tipo"] != "reparado"})
            for f in focos:
                nombre = "planning-agent" if not f else f"planning-{f}"
                corridas.append(run_agent(nombre, "function", prompts2.LAYER,
                                          f"Shift time {ctx['hora']}. New information arrived. Decide now.",
                                          paso / "layer.db", model=MODELO, log_dir=paso, server="mcl2/server.py",
                                          extra_env={"MCL_FOCUS": f} if f else None, quiet=True))
            filas = con.execute("SELECT focus, body FROM proposals ORDER BY id").fetchall()
            for foco, body in filas:
                decisiones.append(json.loads(body))
            # Unir decisiones: en B cada planificador decide su tajo; los préstamos previos se mantienen
            if config == "B":
                previos = [{"loader": s, "to": p} for s, p in T.pos.items() if p in T.caidos and p != T.casa[s]]
                d = {"loans": previos, "reserve_activations": [], "confirmations_requested": []}
                for x in decisiones:
                    d["loans"] += x.get("loans") or []
                    d["reserve_activations"] += x.get("reserve_activations") or []
                    d["confirmations_requested"] += x.get("confirmations_requested") or []
                # Si dos planificadores mandan a un mismo tajo, se queda el último (sin coordinación)
                vistos = {}
                for x in d["loans"]:
                    vistos[x.get("to")] = x
                d["loans"] = list(vistos.values())
            else:
                d = decisiones[-1] if decisiones else {}
        else:
            archivos(T, t, paso / "files")
            r = run_agent("single-agent", "function", prompts2.SINGLE,
                          f"Shift time {ctx['hora']}. New information arrived. Decide now.", None,
                          model=MODELO, builtin_tools="Read,Glob,Grep", workdir=paso / "files", use_layer=False,
                          log_dir=paso, quiet=True)
            corridas.append(r)
            txt = r["final"]
            mm = re.search(r"\{.*\}", txt, re.S)
            try:
                d = json.loads(mm.group(0)) if mm else {}
            except json.JSONDecodeError:
                d = {}
        asign, viol, confirmados = a_asignacion(T, d, {"t": t})
        extras.append({"hora": ctx["hora"], "decision_cruda": d, "violaciones": viol,
                       "confirmo_dudosos": confirmados, "segundos": sum(c["seconds"] for c in corridas),
                       "costo_usd": round(sum(c["cost_usd"] for c in corridas), 4),
                       "agentes": len(corridas), "sin_decision": not d})
        return asign
    return decide


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--turnos", type=int, nargs="*", default=list(range(6)))
    ap.add_argument("--patrones", nargs="*", default=None)
    ap.add_argument("--configs", nargs="*", default=["A", "B", "C"])
    args = ap.parse_args()
    out = ROOT / "runs2" / args.run
    out.mkdir(parents=True, exist_ok=True)
    patrones = [p for p in PATRONES if not args.patrones or p.split()[0] in args.patrones]
    for s in args.turnos:
        mina = generar(s); red = Red(mina)
        for p in patrones:
            for c in args.configs:
                destino = out / f"t{s}_p{p.split()[0]}_{c}.json"
                if destino.exists():
                    continue                                   # ya corrido: permite retomar
                registro, extras = [], []
                t0 = time.time()
                clave(mina, p, red, politica(c, out / f"t{s}_p{p.split()[0]}_{c}", extras), registro)
                for r, e in zip(registro, extras):
                    r.update(e)
                destino.write_text(json.dumps({"turno": s, "patron": p, "config": c, "segundos_total": round(time.time() - t0),
                                               "decisiones": registro}, ensure_ascii=False, indent=1))
                perd = sum(r["perdida_oz"] for r in registro)
                print(f"turno {s} | {p} | {c}: pérdida {perd:.1f} oz, {sum(len(r['violaciones']) for r in registro)} violaciones, "
                      f"{sum(r['costo_usd'] for r in registro):.2f} USD", flush=True)


if __name__ == "__main__":
    main()
