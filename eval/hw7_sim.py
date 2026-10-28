"""
Simulador de turnos y clave de HW7 (versión 3, validada por Santiago el 28-oct).

Qué modela, en simple:
- Varios turnos distintos, no uno solo: cada turno cambia leyes, toneladas, fases y qué scoop está en qué tajo.
- Cada tajo produce solo en sus bloques de carguío del cronograma (no en la colación ni mientras perfora).
- Mantenimiento da un rango para cada reparación (mínimo, más probable, máximo) y lo puede actualizar.
- Se decide en cada evento (falla, actualización, reparación terminada) lo que dé más onzas esperadas.
- Un tajo detenido libera su scoop, que puede activar un tajo de reserva.
- Los traslados salen del mapa sintético de la mina (data/mine_layout.json), con velocidades ajustables.
"""

import copy
import heapq
import itertools
import json
import random
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
BASE = json.loads((RAIZ / "data/seed/Plan_de_turno.json").read_text())["tajos_produccion"]
INV = json.loads((RAIZ / "data/seed/Inventario_de_frentes.json").read_text())["frentes"]
MAPA = json.loads((RAIZ / "data/mine_layout.json").read_text())
FIN = 17.0                                              # fin de la ventana productiva

# Bloques de carguío según la fase del tajo (horas del día)
BLOQUES = {
    "carguio": [(7, 11), (11.5, 17)],
    "carguio-perforacion": [(7, 11), (14.5, 17)],
    "relleno-carguio": [(7, 10.5)],
}
OZ = 31.1035                                            # gramos por onza troy


# ---------- Mapa y traslados ----------

class Mapa:
    def __init__(self, vel=None, maniobra_h=None):
        # Velocidades (km/h) y tiempo fijo por traslado; se pueden cambiar para sensibilizar
        self.vel = vel or MAPA["speeds_kmh"]
        self.maniobra = MAPA["fixed_move_overhead_h"] if maniobra_h is None else maniobra_h
        self.g, self._cache = {}, {}
        acceso = {a["stope"]: a for a in MAPA["stope_access"]}
        for e in MAPA["edges"]:
            self._unir(e["a"], e["b"], e["m"] / 1000 / self.vel[e["type"]])
        for s, a in acceso.items():
            self._unir(s, a["node"], a["m"] / 1000 / self.vel["drift"])
        # Tajos del mismo nivel y lado comparten galería
        for s1, s2 in itertools.combinations(acceso, 2):
            a1, a2 = acceso[s1], acceso[s2]
            if a1["node"] == a2["node"] and a1["side"] == a2["side"]:
                self._unir(s1, s2, abs(a1["m"] - a2["m"]) / 1000 / self.vel["drift"])

    def _unir(self, a, b, h):
        self.g.setdefault(a, []).append((b, h)); self.g.setdefault(b, []).append((a, h))

    def viaje(self, a, b):
        # Camino más corto entre dos puntos más el tiempo fijo de maniobra
        if a == b:
            return 0.0
        if (a, b) not in self._cache:
            dist, cola = {a: 0.0}, [(0.0, a)]
            while cola:
                d, n = heapq.heappop(cola)
                if n == b:
                    break
                for m, h in self.g[n]:
                    if d + h < dist.get(m, 1e9):
                        dist[m] = d + h; heapq.heappush(cola, (d + h, m))
            self._cache[(a, b)] = self._cache[(b, a)] = round(dist[b] + self.maniobra, 2)
        return self._cache[(a, b)]


# ---------- Turnos ----------

def turno(semilla):
    """Devuelve un turno. Semilla 0 = el turno base del dataset; otras semillas = variaciones."""
    rng = random.Random(semilla)
    tajos = {}
    for t in BASE:
        tajos[t["id"]] = {"id": t["id"], "fase": t["fase"], "ley": t["ley_plan_gt"],
                          "t": t["toneladas_objetivo"], "scoop": t["equipo_carguio"]}
    if semilla:
        for x in tajos.values():
            x["ley"] = round(x["ley"] * rng.uniform(0.75, 1.25), 1)       # la ley cambia ±25%
            x["t"] = round(x["t"] * rng.uniform(0.85, 1.15))              # las toneladas ±15%
        tp = [k for k in tajos if k.startswith("TP")]
        for k in tp:
            tajos[k]["fase"] = "carguio"
        for k in rng.sample(tp, 3):
            tajos[k]["fase"] = "carguio-perforacion"                      # 3 tajos en ciclo combinado al azar
        scoops = [x["scoop"] for x in tajos.values()]
        rng.shuffle(scoops)                                               # cada scoop arranca en otro tajo
        for x, s in zip(tajos.values(), scoops):
            x["scoop"] = s
    for x in tajos.values():
        x["oz"] = x["t"] * x["ley"] / OZ
        x["bloques"] = BLOQUES[x["fase"]]
        x["ritmo"] = x["oz"] / sum(b - a for a, b in x["bloques"])        # onzas por hora mientras carga
    reservas = {f["id"]: {"id": f["id"], "ley": f["ley_gt"], "t": f["toneladas_disponibles"],
                          "activacion": f["tiempo_activacion_h"], "bloques": BLOQUES["carguio"]}
                for f in INV if f["estado_preparacion"] == "disponible" and f["id"].startswith("TP")}
    for r in reservas.values():
        r["oz"] = r["t"] * r["ley"] / OZ
        r["ritmo"] = r["oz"] / 9.5
    return {"semilla": semilla, "tajos": tajos, "reservas": reservas}


def prod(x, desde, hasta=FIN):
    # Onzas que produce un tajo si tiene scoop entre "desde" y "hasta", según sus bloques de carguío
    total = 0.0
    for a, b in x["bloques"]:
        total += max(0.0, min(b, hasta) - max(a, desde)) * x["ritmo"]
    return min(total, x["oz"])


# ---------- Rangos de reparación ----------

def muestras(rango, n=11):
    # Puntos representativos de una distribución triangular (mínimo, más probable, máximo)
    lo, mo, hi = rango
    out = []
    for i in range(n):
        u = (i + 0.5) / n
        c = (mo - lo) / (hi - lo) if hi > lo else 0.5
        out.append(lo + ((u * (hi - lo) * (mo - lo)) ** 0.5 if u < c else hi - ((1 - u) * (hi - lo) * (hi - mo)) ** 0.5)
                   if hi > lo else lo)
    return out


# ---------- Valor de una decisión ----------

def valor_cubrir(m, tr, caido, scoop, ahora, fin_rep, lugar_rep):
    """Onzas esperadas ganadas al mandar "scoop" a cubrir el tajo "caido", frente a no cubrirlo.

    fin_rep son muestras de la hora en que termina la reparación. Al terminar, se elige lo mejor entre
    devolver el prestado a su tajo o mandar el reparado al tajo del prestado.
    """
    f = tr["tajos"][caido]
    casa = tr["casa"][scoop]                                          # tajo propio del scoop prestado
    d = tr["tajos"][casa]
    pos = tr["pos"][scoop]
    ganancias = []
    for R in fin_rep:
        sitio = caido if lugar_rep == "en sitio" else lugar_rep
        llega_rep = R + m.viaje(sitio, caido)
        sin = prod(f, llega_rep) + prod(d, ahora + m.viaje(pos, casa))       # nadie se mueve (o el prestado vuelve ya)
        f_con = prod(f, ahora + m.viaje(pos, caido))
        vuelve = prod(d, llega_rep + m.viaje(caido, casa))
        cruza = prod(d, R + m.viaje(sitio, casa))
        ganancias.append(f_con + max(vuelve, cruza) - sin)
    return sum(ganancias) / len(ganancias)


def valor_reserva(m, tr, scoop, reserva, ahora, fin_parada):
    # Onzas esperadas de llevar el scoop de un tajo detenido a un tajo de reserva
    r = tr["reservas"][reserva]
    casa = tr["tajos"][tr["casa"][scoop]]
    pos = tr["pos"][scoop]
    listo = ahora + m.viaje(pos, reserva) + r["activacion"]
    ganancias = []
    for R in fin_parada:
        sin = prod(casa, max(R, ahora))
        # Al reabrir su tajo, el scoop vuelve o se queda en la reserva, lo que rinda más
        volver = prod(r, listo, max(R, listo)) + prod(casa, max(R, listo) + m.viaje(reserva, casa.get("id")))
        quedarse = prod(r, listo)
        ganancias.append(max(volver, quedarse) - sin)
    return sum(ganancias) / len(ganancias)


def mejor_decision(m, tr, ahora, caidos, detenidos, ocupados):
    """Busca la mejor asignación de scoops en este momento.

    caidos:    {tajo: (muestras de fin de reparación, lugar de reparación)}  tajos sin scoop por falla
    detenidos: {scoop: muestras de reapertura}  scoops libres porque su tajo está detenido
    ocupados:  scoops que no se pueden mover (caídos, o en tajos de ciclo combinado)
    """
    movibles = [s for s, c in tr["casa"].items()
                if s not in ocupados and tr["tajos"][c]["fase"] == "carguio"
                and c not in caidos and s not in detenidos]                       # regla LOADER-BACKUP-02
    # Opciones de cada tajo caído: esperar o recibir un scoop movible
    op_caidos = {c: [None] + [(s, valor_cubrir(m, tr, c, s, ahora, *caidos[c])) for s in movibles]
                 for c in caidos}
    # Opciones de cada scoop libre: quedarse, ir a una reserva (regla RESERVE-01) o cubrir un caído
    op_libres = {s: [None] + [(r, valor_reserva(m, tr, s, r, ahora, detenidos[s])) for r in tr["reservas"]]
                 for s in detenidos}
    mejor = (0.0, {})
    claves = list(op_caidos) + list(op_libres)
    for combo in itertools.product(*[op_caidos.get(k) or op_libres.get(k) for k in claves]):
        usados = [o[0] for k, o in zip(claves, combo) if o and k in op_caidos]
        destinos = [o[0] for k, o in zip(claves, combo) if o and k in op_libres]
        if len(usados) != len(set(usados)) or len(destinos) != len(set(destinos)):
            continue                                                             # un scoop o una reserva, una sola vez
        if any(o and o[1] < 0 for o in combo):
            continue                                                             # nunca una movida que resta
        total = sum(o[1] for o in combo if o)
        if total > mejor[0] + 0.05:
            mejor = (total, {k: o for k, o in zip(claves, combo) if o})
    return round(mejor[0], 1), {k: (o[0], round(o[1], 1)) for k, o in mejor[1].items()}


# ---------- Casos: patrones que se aplican a cualquier turno ----------

def roles(tr):
    # Ordena los tajos en fase "carguio" por ritmo: R1 es el más rico, B1 el más pobre, M1 el del medio
    c = sorted([x for x in tr["tajos"].values() if x["fase"] == "carguio"], key=lambda x: -x["ritmo"])
    r = {f"R{i + 1}": x["id"] for i, x in enumerate(c)}
    r.update({f"B{i + 1}": x["id"] for i, x in enumerate(reversed(c))})
    r["M1"] = c[len(c) // 2]["id"]
    return r


# Cada evento: (hora, tipo, rol del tajo, datos). Rango de reparación = (mínimo, más probable, máximo) en horas.
PATRONES = {
    "1 una falla": [("09:20", "falla", "R1", ((3, 4, 5), "en sitio"))],
    "2 falla corta en tajo pobre": [("10:00", "falla", "B2", ((1, 1.5, 2), "en sitio"))],
    "3 dos fallas en tajos ricos": [("09:30", "falla", "R1", ((3, 4, 5), "en sitio")),
                                    ("09:30", "falla", "R2", ((4, 5, 6), "en sitio"))],
    "4 dos fallas, una vale la pena": [("09:30", "falla", "R1", ((3, 4, 5), "en sitio")),
                                       ("09:30", "falla", "B3", ((1.5, 2, 2.5), "en sitio"))],
    "5 tres fallas casi juntas": [("09:30", "falla", "R1", ((3, 4, 5), "TALLER-1280")),
                                  ("09:40", "falla", "R3", ((3, 4, 5), "en sitio")),
                                  ("09:50", "falla", "R4", ((2, 3, 4), "en sitio"))],
    "6 segunda falla con respaldo ocupado": [("09:20", "falla", "R1", ((3, 4, 5), "en sitio")),
                                             ("10:00", "falla", "R2", ((5, 6, 7), "en sitio"))],
    "7 fuentes que se contradicen": [("09:30", "falla", "R1", ((3, 4, 5), "en sitio")),
                                     ("09:30", "reporte_dudoso", "R2", None)],
    "8 el estimado de reparación cambia": [("09:30", "falla", "R3", ((0.25, 0.4, 0.5), "en sitio")),
                                           ("10:00", "actualiza", "R3", (5, 6, 8))],
    "9 tajo detenido y reserva": [("10:00", "detenido", "M1", (5, 6, 8))],
}
GUARDADOS = {"5", "6", "7", "9"}          # casos que no se usan para ajustar, solo para la prueba final


def hora(h):
    hh, mm = h.split(":"); return int(hh) + int(mm) / 60


def clave(tr, patron, m):
    """Recorre los eventos del caso y devuelve la mejor decisión en cada uno, con la información de ese momento."""
    tr = copy.deepcopy(tr)
    tr["casa"] = {x["scoop"]: x["id"] for x in tr["tajos"].values()}    # tajo propio de cada scoop
    tr["pos"] = dict(tr["casa"])                                         # dónde está cada scoop ahora
    rol = roles(tr)
    caidos, detenidos, ocupados, salida = {}, {}, set(), []
    eventos = sorted(PATRONES[patron], key=lambda e: hora(e[0]))
    # Agregamos el fin real de cada reparación: el valor más probable del último rango conocido
    fin_real = {}
    for h, tipo, r, dato in eventos:
        if tipo == "falla":
            fin_real[r] = hora(h) + dato[0][1]
        if tipo == "actualiza":
            fin_real[r] = hora(PATRONES[patron][0][0]) + dato[1]
    eventos += [(f"{int(t):02d}:{round(t % 1 * 60):02d}", "reparado", r, None) for r, t in fin_real.items() if t < FIN]
    eventos.sort(key=lambda e: hora(e[0]))
    inicio = {}
    for i, (h, tipo, r, dato) in enumerate(eventos):
        ultimo_de_su_hora = i == len(eventos) - 1 or eventos[i + 1][0] != h
        t, tajo = hora(h), rol[r]
        scoop = next(s for s, c in tr["casa"].items() if c == tajo) if tipo != "reparado" else None
        if tipo == "reporte_dudoso":
            salida.append((h, f"reporte por radio no confirmado en {tajo}", "no mover; pedir confirmación al jefe de área", 0.0))
            if not ultimo_de_su_hora:
                continue
            tipo = "dudoso"                                                    # igual se decide sobre lo confirmado
        if tipo == "falla":
            inicio[r] = t
            ocupados.add(scoop)
            caidos[tajo] = ([t + d for d in muestras(dato[0])], dato[1])
        elif tipo == "actualiza":
            caidos[tajo] = ([inicio[r] + d for d in muestras(dato)], caidos[tajo][1])
        elif tipo == "detenido":
            detenidos[scoop] = [t + d for d in muestras(dato)]
        elif tipo == "reparado":
            # Al terminar la reparación se decide: el prestado vuelve o el reparado va al tajo del prestado
            lugar = caidos.pop(tajo)[1]
            sitio = tajo if lugar == "en sitio" else lugar
            reparado = next(s for s in ocupados if tr["casa"][s] == tajo)
            ocupados.discard(reparado)
            prestado = next((s for s, p in tr["pos"].items() if p == tajo and s != reparado), None)
            tr["pos"][reparado] = sitio
            if prestado:
                casa_p = tr["tajos"][tr["casa"][prestado]]
                vuelve = prod(casa_p, t + m.viaje(sitio, tajo) + m.viaje(tajo, casa_p["id"]))
                cruza = prod(casa_p, t + m.viaje(sitio, casa_p["id"]))
                if cruza > vuelve + 0.05:
                    tr["casa"][reparado], tr["casa"][prestado] = casa_p["id"], tajo
                    tr["pos"][reparado] = casa_p["id"]
                    salida.append((h, f"{reparado} reparado", f"{reparado} va a {casa_p['id']}; {prestado} se queda en {tajo}", round(cruza - vuelve, 1)))
                else:
                    tr["pos"][prestado] = casa_p["id"]; tr["pos"][reparado] = tajo
                    salida.append((h, f"{reparado} reparado", f"{prestado} vuelve a {casa_p['id']}", 0.0))
            else:
                tr["pos"][reparado] = tajo
            continue
        if not ultimo_de_su_hora:
            continue                                                       # se decide cuando se conocen todos los eventos de esa hora
        # En fallas, actualizaciones y tajos detenidos se busca la mejor asignación de este momento
        # Los scoops ya prestados también se pueden mantener, mover o devolver
        total, plan = mejor_decision(m, tr, t, caidos, detenidos, ocupados)
        asignados = {v[0] for k, v in plan.items() if k in caidos}
        for s_ in tr["pos"]:
            if s_ not in ocupados and s_ not in asignados and s_ not in detenidos and tr["pos"][s_] in caidos:
                tr["pos"][s_] = tr["casa"][s_]                                     # el que no sigue prestado, vuelve
        texto = "; ".join(f"{v[0]} a {k}" if k in caidos else f"{k} a reserva {v[0]}" for k, v in plan.items()) or "no mover nada"
        for k, v in plan.items():
            if k in caidos:
                tr["pos"][v[0]] = k
            else:
                tr["pos"][k] = v[0]
                detenidos.pop(k, None)
        salida.append((h, ", ".join(f"{e[1]} {rol[e[2]]}" for e in eventos if e[0] == h and e[1] in ("falla", "actualiza", "detenido")), texto, total))
    return salida


if __name__ == "__main__":
    import sys
    m = Mapa()
    for semilla in [int(x) for x in sys.argv[1:]] or [0]:
        tr = turno(semilla)
        print(f"\n=== Turno {semilla} === roles:", {k: v for k, v in roles(tr).items() if k in ('R1', 'R2', 'B1', 'M1')})
        for p in PATRONES:
            for paso in clave(tr, p, m):
                print(f"  [{p}] {paso[0]} {paso[1]}: {paso[2]} (+{paso[3]} oz)")
