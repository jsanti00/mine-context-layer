"""
Mine Context Layer v3 (HW7 improvement): same tools as v2 plus two derived-context tools.

Why: in the first benchmark the layer agent picked the right stope but the wrong loader in most
of its losses (it only considered development loaders and never a loader from a lower-grade
producing stope), and some decisions were dropped for breaking an operating rule.

  rank_loan_options: for every down stope and every loader that can be lent, a first-order
                     estimate of ounces gained, ounces lost at the loader's home and the net.
                     Plain arithmetic (rate x productive hours x grade), not the benchmark key.
  check_decision:    dry run of a decision against the operating rules, before submitting.
"""

# Aquí reutilizamos el servidor v2 completo: mismas herramientas, mismo registro de llamadas
import server as base
from server import db, log_call, mcp, state

OZ = 31.1035                     # gramos por onza troy
BLOQUES = [(7.0, 11.0), (11.5, 17.0)]   # horas de carguío del turno (colación 11:00-11:30)


def horas(hhmm):
    # Convierte "13:20" en 13.33 horas
    h, m = hhmm.split(":")
    return int(h) + int(m) / 60


def horas_utiles(a, b):
    # Horas de carguío entre a y b, descontando colación y fuera de turno
    return sum(max(0.0, min(b, y) - max(a, x)) for x, y in BLOQUES)


def contexto(st):
    # Índices de lugares, scoops, fallas y avisos dudosos a partir del estado del turno
    stopes = {x["id"]: x for x in st["stopes"]}
    heads = {x["id"]: x for x in st["headings"]}
    loaders = {x["id"]: x for x in st["loaders"]}
    down = {e["place"]: e for e in st["repair_estimates"]}
    dudosos = {e["place"] for e in st["events"] if e["type"] == "equipment_status_conflict"}
    ahora = horas(st["shift_time"])
    return stopes, heads, loaders, down, dudosos, ahora


def prestable(x, stopes, ahora):
    """Si un scoop se puede prestar ahora, y desde dónde sale. Devuelve (sí/no, motivo)."""
    if x["status"] == "down":
        return False, "down"
    if horas(x["available_from"]) > ahora + 0.01:
        return False, f"preventive maintenance until {x['available_from']} (PM-01)"
    casa = x["home"]
    if casa in stopes and stopes[casa]["phase"] != "carguio":
        return False, "its stope is in drilling or fill (LOADER-SOURCE-01)"
    return True, ""


@mcp.tool()
def rank_loan_options(stope: str = "") -> list[dict]:
    """For each down stope (or only `stope`), every loader that could be lent there, ranked by a
    first-order net estimate in oz: gain at the down stope until its likely repair time, minus what
    the loader's home loses while it is away (development value per hour, or the home stope's own
    ore at its grade). Gains and losses are ore left unloaded at 17:00, not speed: if the repaired
    loader can clear the down stope by itself before 17:00, a loan there gains nothing. Ore that would not fit in the mine's stockpile plus truck haulage by 17:00
    (mine_excess_to_17h_t > 0) is not counted, neither as gain nor as loss. Includes loaders from producing stopes: lending from a lower-grade stope can
    pay. Use it to compare options; you still decide the combination (one loader, one place) and
    check stockpile room and haulage."""
    con = db(); st = state(con); log_call(con, "rank_loan_options", {"stope": stope})
    stopes, heads, loaders, down, dudosos, ahora = contexto(st)
    room = {h["mine"]: h["stockpile_capacity_t"] - h["stockpile_t"] for h in st["haulage"]}
    # Exceso de cada mina hasta las 17:00: lo que los scoops cargarían y no cabe en acopio + camiones.
    # Si es positivo, la mina está saturada: un scoop más no suma y uno menos resta poco.
    H = horas_utiles(max(ahora, 7.0), 17.0)
    exceso = {h["mine"]: h["loader_inflow_t_h"] * H - (room[h["mine"]] + h["truck_capacity_to_surface_t_h"] * (17.0 - ahora))
              for h in st["haulage"]}
    out = []
    for p, rep in down.items():
        if stope and p != stope or p in dudosos or p not in stopes:
            continue
        dest = stopes[p]
        fin = min(17.0, horas(rep["likely_h_clock"]))          # el préstamo dura hasta la reparación probable
        # Lo que el scoop reparado alcanza a cargar solo, de la reparación a las 17:00 (si se repara en
        # el taller, primero tiene que volver). Eso se carga igual: el préstamo solo gana lo que quedaría.
        modelo_caido = loaders[rep["loader"]]["model"]
        vuelta = st["travel_h"].get(rep["repair_location"], {}).get(p, 0.0) if rep["repair_location"] != "in place" else 0.0
        t_solo = dest["loading_rate_t_h"][modelo_caido] * horas_utiles(fin + vuelta, 17.0)
        t_sin = min(dest["broken_ore_remaining_t"], t_solo)
        opciones = []
        for s, x in loaders.items():
            ok, motivo = prestable(x, stopes, ahora)
            if not ok or s == rep["loader"]:
                continue
            origen = x["at"]
            viaje = st["travel_h"].get(origen, {}).get(p)
            if viaje is None:
                continue
            llega = max(ahora, 7.0) + viaje
            h_dest = horas_utiles(llega, fin)
            # Toneladas cargadas al cierre con el préstamo, menos las que se cargarían sin él
            t_con = min(dest["broken_ore_remaining_t"], dest["loading_rate_t_h"][x["model"]] * h_dest + t_solo)
            t_gana = t_con - t_sin
            t_gana = max(0.0, t_gana - max(0.0, exceso[dest["mine"]]))   # lo que no cabe en el acopio no cuenta
            gana = t_gana * dest["grade_gpt"] / OZ
            # Lo que deja de producir su lugar de origen mientras está fuera (ida, préstamo y vuelta)
            vuelve = min(17.0, fin + st["travel_h"].get(p, {}).get(x["home"], 0.0))
            h_casa = horas_utiles(ahora, vuelve)
            casa = x["home"]
            if casa in heads:
                pierde, de_donde = heads[casa]["value_oz_per_loader_hour"] * h_casa, "development heading"
            elif casa in stopes and not stopes[casa]["stopped"] and x["status"] != "on_loan":
                c = stopes[casa]
                # Lo que su tajo deja sin cargar al cierre: carga de todo el turno menos carga sin las horas fuera
                tasa_c, h_tot = c["loading_rate_t_h"][x["model"]], horas_utiles(max(ahora, 7.0), 17.0)
                t_pierde = min(c["broken_ore_remaining_t"], tasa_c * h_tot) - min(c["broken_ore_remaining_t"], tasa_c * max(0.0, h_tot - h_casa))
                t_pierde = max(0.0, t_pierde - max(0.0, exceso[c["mine"]]))   # si su mina está saturada, pierde menos
                pierde = t_pierde * c["grade_gpt"] / OZ
                de_donde = f"producing stope ({c['grade_gpt']} g/t)"
            else:
                pierde, de_donde = 0.0, "idle, spare or already on loan"
            opciones.append({"loader": s, "model": x["model"], "from": origen, "home_type": de_donde,
                             "travel_h": round(viaje, 2), "productive_h_at_stope": round(h_dest, 1),
                             "gain_oz": round(gana, 1), "home_loss_oz": round(pierde, 1),
                             "net_oz": round(gana - pierde, 1)})
        opciones.sort(key=lambda o: -o["net_oz"])
        out.append({"down_stope": p, "mine": dest["mine"], "grade_gpt": dest["grade_gpt"],
                    "broken_ore_t": dest["broken_ore_remaining_t"], "likely_repair": rep["likely_h_clock"],
                    "t_the_repaired_loader_clears_alone_by_17h": round(t_sin),
                    "stockpile_room_now_t": round(room[dest["mine"]]),
                    "mine_excess_to_17h_t": round(exceso[dest["mine"]]),
                    "options": opciones[:8]})
    return out


@mcp.tool()
def check_decision(loans: list[dict], reserve_activations: list[dict]) -> dict:
    """Dry run of a decision against the operating rules, before submit_decision. Returns the
    violations found (an invalid decision is dropped entirely, so fix them and check again)."""
    con = db(); st = state(con)
    log_call(con, "check_decision", {"loans": loans, "reserve_activations": reserve_activations})
    stopes, heads, loaders, down, dudosos, ahora = contexto(st)
    reservas = {x["id"] for x in st["reserves"]}
    v, usados = [], []
    for l in loans or []:
        s, to = l.get("loader"), l.get("to")
        usados.append(s)
        if s not in loaders:
            v.append(f"{s}: unknown loader"); continue
        if to in dudosos:
            v.append(f"{s} -> {to}: radio and CMMS disagree there, ask for confirmation instead (CONFIRM-01)")
        elif to == loaders[s]["home"]:
            continue
        elif to not in down:
            v.append(f"{s} -> {to}: loans only go to a stope whose loader is down")
        ok, motivo = prestable(loaders[s], stopes, ahora)
        if not ok:
            v.append(f"{s}: cannot be lent, {motivo}")
    for r in reserve_activations or []:
        s, rs = r.get("loader"), r.get("reserve")
        usados.append(s)
        casa = loaders.get(s, {}).get("home")
        if rs not in reservas or casa not in stopes or not stopes[casa]["stopped"]:
            v.append(f"{s} -> {rs}: only a loader of a stopped stope can open a reserve (RESERVE-01)")
    if len(usados) != len(set(usados)):
        v.append("the same loader is assigned to two places")
    return {"ok": not v, "violations": v}


if __name__ == "__main__":
    mcp.run()
