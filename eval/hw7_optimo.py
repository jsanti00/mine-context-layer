"""
Mejor plan posible para cada caso de HW7 (la "clave" contra la que se evalúa a los agentes).

Regla validada por Santiago (28-oct): se elige lo que dé mayor valor esperado en onzas, y la decisión
se revisa en cada paso. Cuando termina una reparación se vuelve a decidir: o el scoop prestado vuelve
a su tajo, o el scoop reparado se va al tajo que cedió el suyo, lo que convenga.

Los traslados salen del mapa sintético de la mina (data/mine_layout.json), no de una regla fija.
"""

import heapq
import itertools
import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PLAN = json.loads((RAIZ / "data/seed/Plan_de_turno.json").read_text())["tajos_produccion"]
MAPA = json.loads((RAIZ / "data/mine_layout.json").read_text())

T = {t["id"]: t for t in PLAN}                                  # tajos del plan por id
LOADER = {t["equipo_carguio"]: t["id"] for t in PLAN}           # scoop -> tajo donde trabaja
FIN = 17.0                                                      # fin de la ventana productiva (17:00)
ACCESO = {a["stope"]: a for a in MAPA["stope_access"]}          # cómo se llega a cada tajo
VEL = MAPA["speeds_kmh"]


def ritmo(tajo):
    # Onzas por hora de un tajo: onzas del plan repartidas en 9,5 horas productivas
    return T[tajo]["onzas_plan"] / 9.5


def _grafo():
    # Armamos la red de túneles con el tiempo (en horas) de recorrer cada tramo
    g = {}
    def unir(a, b, h):
        g.setdefault(a, []).append((b, h)); g.setdefault(b, []).append((a, h))
    for e in MAPA["edges"]:
        unir(e["a"], e["b"], e["m"] / 1000 / VEL[e["type"]])
    for s, a in ACCESO.items():
        unir(s, a["node"], a["m"] / 1000 / VEL["drift"])
    # Tajos del mismo nivel y del mismo lado comparten galería: se va directo por ella
    for s1, s2 in itertools.combinations(ACCESO, 2):
        a1, a2 = ACCESO[s1], ACCESO[s2]
        if a1["node"] == a2["node"] and a1["side"] == a2["side"]:
            unir(s1, s2, abs(a1["m"] - a2["m"]) / 1000 / VEL["drift"])
    return g


G = _grafo()


def viaje(a, b):
    # Tiempo de traslado de un scoop entre dos puntos: camino más corto más el tiempo fijo de maniobra
    if a == b:
        return 0.0
    dist, cola = {a: 0.0}, [(0.0, a)]
    while cola:
        d, n = heapq.heappop(cola)
        if n == b:
            return round(d + MAPA["fixed_move_overhead_h"], 2)
        for m, h in G[n]:
            if d + h < dist.get(m, 1e9):
                dist[m] = d + h; heapq.heappush(cola, (d + h, m))
    raise ValueError(f"sin camino {a} -> {b}")


def hora(h):
    # Convierte "09:30" en 9,5 horas
    hh, mm = h.split(":"); return int(hh) + int(mm) / 60


def valor(falla, donante, modo):
    """Onzas ganadas frente a no hacer nada, para una falla cubierta por un donante.

    falla = (scoop caído, horas de reparación, hora de la falla, lugar de reparación)
    modo  = "vuelve" (el prestado regresa al terminar la reparación) o "cruza" (el reparado va al donante)
    """
    sc, rep, hf, lugar = falla
    f, d = LOADER[sc], LOADER[donante]
    t0 = hora(hf); R = t0 + rep
    sitio = f if lugar == "en sitio" else lugar                  # dónde queda el scoop reparado
    llega_reparado_f = R + viaje(sitio, f)                       # sin hacer nada, el tajo para hasta esto
    llega_prestado = t0 + viaje(d, f)
    if modo == "vuelve":
        # El prestado se queda hasta que llega el reparado y luego regresa a su tajo
        vuelta_donante = llega_reparado_f + viaje(f, d)
    else:
        # El prestado se queda en el tajo caído y el reparado va directo al donante
        vuelta_donante = R + viaje(sitio, d)
    gana = ritmo(f) * max(0.0, min(FIN, llega_reparado_f) - min(FIN, llega_prestado))
    pierde = ritmo(d) * (min(FIN, vuelta_donante) - min(FIN, t0))
    return gana - pierde


def resolver(fallas, ocupados=()):
    # Prueba todas las combinaciones (donante y modo por falla) y se queda con la de más onzas
    caidos = {LOADER[f[0]] for f in fallas}
    donantes = [l for l, t in LOADER.items()
                if T[t]["fase"] == "carguio" and t not in caidos and l not in ocupados]  # regla LOADER-BACKUP-02
    opciones = [None] + [(d, m) for d in donantes for m in ("vuelve", "cruza")]
    mejor = None
    for combo in itertools.product(opciones, repeat=len(fallas)):
        usados = [c[0] for c in combo if c]
        if len(usados) != len(set(usados)):
            continue                                              # un scoop no puede ir a dos tajos
        partes = [(LOADER[f[0]], "esperar", 0.0) if c is None else (LOADER[f[0]], f"{c[0]} ({c[1]})", valor(f, *c))
                  for f, c in zip(fallas, combo)]
        partes = [(a, b, round(v, 1)) for a, b, v in partes]
        total = sum(p[2] for p in partes)
        if any(p[2] < 0 for p in partes):
            continue                                              # nunca conviene una movida que resta
        if mejor is None or total > mejor[0] + 1e-9:
            mejor = (round(total, 1), partes)
    return mejor


CASOS = {
    "1": [("LH-07", 4, "09:20", "en sitio")],
    "2": [("LH-05", 1.5, "10:00", "en sitio")],
    "3": [("LH-07", 4, "09:30", "en sitio"), ("LH-03", 5, "09:30", "en sitio")],
    "4": [("LH-07", 4, "09:30", "en sitio"), ("LH-08", 2, "09:30", "en sitio")],
    "5": [("LH-07", 4, "09:30", "TALLER-1280"), ("LH-01", 4, "09:40", "en sitio"), ("LH-09", 3, "09:50", "en sitio")],
}

if __name__ == "__main__":
    for k, f in CASOS.items():
        print(k, resolver(f))
    print("6", resolver([("LH-03", 6, "10:00", "en sitio")], ocupados=("LH-11",)))
    print("7", resolver([("LH-07", 4, "09:30", "en sitio")]))
