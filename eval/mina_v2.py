"""
Mina sintética v2 para HW7, calibrada con fuentes públicas (auditoría del 28-oct-2026).

Referencias de calibración (todas públicas, nada copiado como dato operativo real):
- Cerro Negro NI 43-101 (Goldcorp, 2016): 3 minas con rampa y portal propios, rampa al 15%, niveles cada 26 m,
  long hole con relleno de roca cementado, ~4.000 t/día, reservas a 9,6 g/t, ley de corte ~5 g/t,
  scoops Sandvik LH410/LH514 y camiones de 33 a 40 t.
- Estudios de scoops: 34 a 48 t/h efectivas, utilización 45 a 60%.

Qué limita la producción (decisión de Santiago: "ambos"):
- El scoop limita a cada tajo: sin scoop, el tajo no carga.
- El camión limita a cada mina: el scoop descarga en el acopio del nivel y los camiones lo suben a superficie.
  Si el acopio se llena, el scoop se detiene; si el acopio tiene mineral, los camiones siguen aunque falle un scoop.
- Se cuentan las onzas que llegan a superficie más las que quedan en los acopios de nivel al cierre del turno de día
  (2 turnos de 12 h). Lo que queda disparado en el tajo no cuenta.
"""

import copy
import heapq
import math
import random

OZ = 31.1035                      # gramos por onza troy
INICIO, FIN = 7.0, 17.0           # ventana productiva del turno de día (06:00 ingreso, 17:00 retiro)
COLACION = (11.0, 11.5)
DT = 0.1                          # paso de la simulación: 6 minutos
# Valor de una hora de scoop en un frente de desarrollo, según para qué sirve el frente (oz equivalentes por hora).
# Supuesto validado en concepto por Santiago (depende del frente); los rangos son supuestos y se sensibilizan.
TIPOS_DESARROLLO = {
    "ruta critica": (4.0, 6.0),       # habilita el próximo tajo del plan: atrasarlo atrasa producción
    "preparacion": (1.0, 2.0),        # accesos a tajos futuros sin urgencia
    "infraestructura": (0.2, 0.5),    # refugios, bahías, exploración
}
MULT_DESARROLLO = 1.0                 # escenario de sensibilidad: 0,5 = bajo, 1 = base, 2 = alto
LEY_CORTE = 5.5

MODELOS = {"LH514": {"t": 14, "t_h": 42}, "LH410": {"t": 10, "t_h": 30}}   # t/h efectivas a 100 m de acopio
CAMION_T = 40                                                               # camión de 40 t (tipo TH540)
# Fracción efectiva de la flota de camiones para mineral: disponibilidad 75% x utilización 55% x 55% del tiempo
# en mineral (el resto acarrea estéril de desarrollo). Supuesto calibrado con la literatura (45-60% de utilización).
CAMION_EFECTIVO = 0.75 * 0.55 * 0.55

# ---------- Geometría ----------

MINAS = {
    # portal_m: rampa desde el portal al primer nivel; niveles cada 26 m; tramo de rampa al 15% ~ 175 m
    "Norte":   {"niveles": [600, 574, 548, 522], "portal_m": 450, "camiones": 3},
    "Central": {"niveles": [590, 564, 538, 512], "portal_m": 400, "camiones": 4},
    "Este":    {"niveles": [595, 569, 543],      "portal_m": 380, "camiones": 3},
}
SUPERFICIE = [("PORTAL-Central", "PORTAL-Este", 800), ("PORTAL-Central", "PORTAL-Norte", 6000),
              ("PORTAL-Central", "TALLER-SUP", 1200)]
VEL = {"drift": 12, "ramp": 7, "surface": 40}       # km/h; superficie en cama baja
MANIOBRA_H = 0.25


def _id(prefijo, mina, nivel, n):
    return f"{prefijo}-{mina[0]}{nivel}-{n:02d}"


def generar(semilla=0):
    """Genera un turno completo y consistente. Semilla 0 = turno base."""
    rng = random.Random(1000 + semilla)
    mina = {"semilla": semilla, "minas": {}, "tajos": {}, "reservas": {}, "desarrollos": {}, "scoops": {}, "aristas": []}
    for nombre, m in MINAS.items():
        portal = f"PORTAL-{nombre}"
        prev = portal
        for i, nv in enumerate(m["niveles"]):
            nodo = f"R-{nombre}-{nv}"
            mina["aristas"].append((prev, nodo, m["portal_m"] if i == 0 else 175, "ramp"))
            prev = nodo
        camiones = m["camiones"]
        mina["minas"][nombre] = {"portal": portal, "camiones": camiones, "acopio_cap_t": 450,
                                 "acopio_t": rng.randint(80, 160)}
    for a, b, d in SUPERFICIE:
        mina["aristas"].append((a, b, d, "surface"))
    mina["aristas"].append(("R-Central-564", "TALLER-564", 120, "drift"))   # taller subterráneo

    # Tajos: en cada nivel 1 a 2 tajos; fases repartidas como en un ciclo long hole real
    fases = ["carguio"] * 7 + ["perforacion"] * 4 + ["relleno"] * 4
    rng.shuffle(fases)
    lugares = [(n, nv) for n, m in MINAS.items() for nv in m["niveles"]]
    rng.shuffle(lugares)
    lugares = (lugares * 2)[:len(fases)]
    for k, (fase, (nombre, nv)) in enumerate(zip(fases, lugares)):
        tid = _id("TJ", nombre, nv, k + 1)
        ley = min(22.0, max(LEY_CORTE + 0.3, rng.lognormvariate(math.log(9.0), 0.35)))   # media ~9,6 g/t
        mina["tajos"][tid] = {"id": tid, "mina": nombre, "nivel": nv, "fase": fase, "ley": round(ley, 1),
                              "t_disp": rng.randint(220, 420) if fase == "carguio" else 0,
                              "dist_acopio_m": rng.choice([80, 120, 160, 220, 280]),
                              "m_rampa": rng.randint(150, 650), "lado": rng.choice("EW")}
    # Reservas: tajos ya disparados y listos para cargar, sin scoop asignado
    for k in range(3):
        nombre, nv = rng.choice(lugares)
        rid = _id("RS", nombre, nv, k + 1)
        mina["reservas"][rid] = {"id": rid, "mina": nombre, "nivel": nv, "fase": "reserva",
                                 "ley": round(max(LEY_CORTE + 0.3, rng.lognormvariate(math.log(8.0), 0.3)), 1),
                                 "t_disp": rng.randint(250, 450), "activacion_h": rng.choice([0.5, 1.0]),
                                 "dist_acopio_m": rng.choice([120, 160, 220]), "m_rampa": rng.randint(150, 650),
                                 "lado": rng.choice("EW")}
    # Frentes de desarrollo con scoop (marina de estéril)
    for k in range(2):
        nombre, nv = rng.choice(lugares)
        did = _id("DS", nombre, nv, k + 1)
        tipo = rng.choice(list(TIPOS_DESARROLLO))
        mina["desarrollos"][did] = {"id": did, "mina": nombre, "nivel": nv, "m_rampa": rng.randint(200, 700),
                                    "lado": rng.choice("EW"), "tipo": tipo,
                                    "valor_oz_h": round(rng.uniform(*TIPOS_DESARROLLO[tipo]), 1)}
    for x in list(mina["tajos"].values()) + list(mina["reservas"].values()) + list(mina["desarrollos"].values()):
        mina["aristas"].append((x["id"], f"R-{x['mina']}-{x['nivel']}", x["m_rampa"], "drift"))

    # Scoops: 10 en total (6 LH514 y 4 LH410). 7 en tajos en carguío, 2 en desarrollo, 1 en mantenimiento preventivo
    # El plan pone los scoops grandes en los tajos más ricos, como lo haría un jefe de turno
    modelos = ["LH514"] * 6 + ["LH410"] * 4
    carguio = sorted([t for t in mina["tajos"] if mina["tajos"][t]["fase"] == "carguio"],
                     key=lambda t: -mina["tajos"][t]["ley"])
    destinos = carguio[:6] + [carguio[6]] + list(mina["desarrollos"]) + ["TALLER-564"]
    # Mantenimiento preventivo: arranca en el cambio de turno (06:00), un solo scoop a la vez.
    # Servicio de 250 h: 4 a 6 h; de 500 h: 8 a 12 h (supuestos sin fuente dura); a veces no hay ninguno.
    pm = rng.choices(["250 h", "500 h", "ninguno"], weights=[60, 25, 15])[0]
    dur = {"250 h": rng.choice([4, 5, 6]), "500 h": rng.choice([8, 10, 12]), "ninguno": 0}[pm]
    mina["mantenimiento"] = {"servicio": pm, "inicio": 6.0, "horas": dur}
    for k, (mod, dest) in enumerate(zip(modelos, destinos)):
        sid = f"LH-{k + 1:02d}"
        desde = INICIO if dest != "TALLER-564" else (INICIO if pm == "ninguno" else 6.0 + dur)
        mina["scoops"][sid] = {"id": sid, "modelo": mod, "casa": dest, "disponible_desde": desde}
    return mina


# ---------- Traslados ----------

class Red:
    def __init__(self, mina, vel=None, maniobra_h=None):
        self.vel = vel or VEL
        self.maniobra = MANIOBRA_H if maniobra_h is None else maniobra_h
        self.g, self.cache = {}, {}
        info = {**mina["tajos"], **mina["reservas"], **mina["desarrollos"]}
        for a, b, m, tipo in mina["aristas"]:
            self._unir(a, b, m / 1000 / self.vel[tipo])
        # Tajos del mismo nivel y lado comparten galería
        ids = list(info)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                x, y = info[ids[i]], info[ids[j]]
                if x["mina"] == y["mina"] and x["nivel"] == y["nivel"] and x["lado"] == y["lado"]:
                    self._unir(x["id"], y["id"], abs(x["m_rampa"] - y["m_rampa"]) / 1000 / self.vel["drift"])

    def _unir(self, a, b, h):
        self.g.setdefault(a, []).append((b, h)); self.g.setdefault(b, []).append((a, h))

    def viaje(self, a, b):
        # Camino más corto más el tiempo fijo de maniobra
        if a == b:
            return 0.0
        if (a, b) not in self.cache:
            dist, cola = {a: 0.0}, [(0.0, a)]
            while cola:
                d, n = heapq.heappop(cola)
                if n == b:
                    break
                for m, h in self.g[n]:
                    if d + h < dist.get(m, 1e9):
                        dist[m] = d + h; heapq.heappush(cola, (d + h, m))
            self.cache[(a, b)] = self.cache[(b, a)] = round(dist[b] + self.maniobra, 2)
        return self.cache[(a, b)]

    def ciclo_camion_h(self, mina, nombre):
        # Ciclo de un camión: carga 0,1 h, subida cargado a 7 km/h, descarga 0,05 h, bajada vacío a 12 km/h,
        # desde el nivel medio de la mina
        m = MINAS[nombre]
        largo = m["portal_m"] + 175 * (len(m["niveles"]) - 1) / 2
        return 0.1 + largo / 1000 / 7 + 0.05 + largo / 1000 / 12 + 0.1   # 0,1 h de espera en cola


# ---------- Simulación del turno ----------

def en_bloque(t):
    return INICIO <= t < FIN and not (COLACION[0] <= t < COLACION[1])


def tasa_scoop(mina, sid, lugar):
    # t/h efectivas del scoop en ese tajo: baja con la distancia al acopio (100 m = 100%, 300 m ~ 70%)
    x = {**mina["tajos"], **mina["reservas"]}[lugar]
    return MODELOS[mina["scoops"][sid]["modelo"]]["t_h"] * min(1.0, 100 / (100 + 0.0015 * (x["dist_acopio_m"] - 100) * 100))


def simular(mina, red, agenda, desde=INICIO, estado=None):
    """Simula el turno y devuelve onzas a superficie y en acopios más el valor del desarrollo.

    agenda: {scoop: [(desde, hasta, lugar), ...]}  dónde trabaja cada scoop (fuera de esos tramos, viaja o está caído)
    estado: toneladas que quedan en tajos y acopios al momento "desde" (si es None, se usa el inicio del turno)
    """
    st = copy.deepcopy(estado) if estado else {
        "tajo_t": {k: x["t_disp"] for k, x in {**mina["tajos"], **mina["reservas"]}.items()},
        "acopio_t": {n: m["acopio_t"] for n, m in mina["minas"].items()},
        "acopio_oz": {n: m["acopio_t"] * 9.0 / OZ for n, m in mina["minas"].items()},
        "oz_sup": 0.0, "t_sup": 0.0, "desarrollo_h": 0.0, "reserva_lista": {}}
    cap_camion = {n: m["camiones"] * CAMION_T * CAMION_EFECTIVO / red.ciclo_camion_h(mina, n) for n, m in mina["minas"].items()}
    info = {**mina["tajos"], **mina["reservas"]}
    t = desde
    while t < FIN - 1e-9:
        if en_bloque(t):
            ocupado = set()
            for sid, tramos in agenda.items():
                lugar = next((l for a, b, l in tramos if a <= t < b), None)
                if lugar is None or lugar in ocupado:
                    continue
                if lugar in mina["desarrollos"]:
                    st["desarrollo_h"] += DT
                    st["desarrollo_oz"] = st.get("desarrollo_oz", 0.0) + DT * mina["desarrollos"][lugar]["valor_oz_h"] * MULT_DESARROLLO
                    ocupado.add(lugar); continue
                if lugar not in info:
                    continue
                x = info[lugar]
                if t < st.get("parado_hasta", {}).get(lugar, 0):
                    continue                                                  # tajo detenido (ventilación, etc.)
                if x["fase"] == "reserva":
                    lista = st["reserva_lista"].setdefault(lugar, t + x["activacion_h"])
                    if t < lista:
                        continue
                elif x["fase"] != "carguio":
                    continue
                n = x["mina"]
                espacio = mina["minas"][n]["acopio_cap_t"] - st["acopio_t"][n]
                carga = max(0.0, min(tasa_scoop(mina, sid, lugar) * DT, st["tajo_t"][lugar], espacio))
                st["tajo_t"][lugar] -= carga
                st["t_cargada"] = st.get("t_cargada", 0.0) + carga
                st["acopio_t"][n] += carga
                st["acopio_oz"][n] += carga * x["ley"] / OZ
                ocupado.add(lugar)
            for n in mina["minas"]:
                # Los camiones suben lo que haya en el acopio, hasta su capacidad
                sube = min(cap_camion[n] * DT, st["acopio_t"][n])
                if st["acopio_t"][n] > 0:
                    oz = st["acopio_oz"][n] * sube / st["acopio_t"][n]
                    st["acopio_oz"][n] -= oz; st["oz_sup"] += oz
                st["acopio_t"][n] -= sube; st["t_sup"] += sube
        t = round(t + DT, 6)
    # Valor del turno: onzas en superficie + onzas que quedan en los acopios de nivel (cuentan, decisión de Santiago
    # del 1-nov) + valor del desarrollo. Lo que queda disparado en el tajo no cuenta.
    st["oz_acopio"] = sum(st["acopio_oz"].values())
    st["valor"] = st["oz_sup"] + st["oz_acopio"] + st.get("desarrollo_oz", 0.0)
    return st


def agenda_base(mina):
    # Cada scoop en su lugar todo el turno (el de mantenimiento, desde que sale del taller)
    return {s: [(x["disponible_desde"], FIN, x["casa"])] for s, x in mina["scoops"].items()}


if __name__ == "__main__":
    import sys
    for semilla in [int(a) for a in sys.argv[1:]] or [0]:
        mina = generar(semilla)
        red = Red(mina)
        r = simular(mina, red, agenda_base(mina))
        cap = {n: round(m["camiones"] * CAMION_T * CAMION_EFECTIVO / red.ciclo_camion_h(mina, n)) for n, m in mina["minas"].items()}
        carga = {}
        for s, x in mina["scoops"].items():
            if x["casa"] in mina["tajos"]:
                n = mina["tajos"][x["casa"]]["mina"]
                carga[n] = carga.get(n, 0) + round(tasa_scoop(mina, s, x["casa"]))
        leyes = [x["ley"] for x in mina["tajos"].values()]
        print(f"turno {semilla}: cargadas {round(r['t_cargada'])} t, acarreadas {round(r['t_sup'])} t "
              f"({round(r['oz_sup'])} oz), variación de acopios {round(sum(r['acopio_t'].values()) - sum(m['acopio_t'] for m in mina['minas'].values())):+d} t; "
              f"mantenimiento {mina['mantenimiento']['servicio']} ({mina['mantenimiento']['horas']} h); "
              f"ley media {sum(leyes) / len(leyes):.1f} g/t (min {min(leyes)}, max {max(leyes)}); "
              f"desarrollo {r['desarrollo_h']:.1f} h")
        print("   camiones t/h por mina:", cap, "| scoops t/h por mina:", carga,
              "| quedan en tajos:", round(sum(r["tajo_t"][k] for k in mina["tajos"])), "t")
