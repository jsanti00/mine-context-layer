"""
Clave de HW7 sobre la mina v2: la mejor decisión en cada evento, evaluada simulando el resto del turno
(scoops, acopios y camiones). Reglas validadas por Santiago el 28-oct:
- Mayor valor esperado en onzas, revisado en cada evento (falla, actualización, reparación, tajo detenido).
- Al terminar una reparación: el prestado vuelve o el reparado va al tajo del prestado, lo que rinda más.
- Si radio y CMMS se contradicen, no se mueve nada hasta que confirme el jefe del área.
- Solo ceden scoop los tajos en carguío o los frentes de desarrollo (con su costo en onzas equivalentes).
- Una reserva solo se activa con el scoop de un tajo detenido (RESERVE-01).
"""

import copy
import itertools

from mina_v2 import DT, FIN, INICIO, Red, generar, simular, tasa_scoop

N_MUESTRAS = 5


def muestras(rango, n=N_MUESTRAS):
    # Puntos representativos de una distribución triangular (mínimo, más probable, máximo)
    lo, mo, hi = rango
    if hi <= lo:
        return [lo] * n
    c = (mo - lo) / (hi - lo)
    out = []
    for i in range(n):
        u = (i + 0.5) / n
        out.append(lo + (u * (hi - lo) * (mo - lo)) ** 0.5 if u < c else hi - ((1 - u) * (hi - lo) * (hi - mo)) ** 0.5)
    return out


def roles(mina):
    # R1 = tajo en carguío que más onzas por hora aporta con su scoop; B1 el que menos; M1 el del medio
    casa = {x["casa"]: s for s, x in mina["scoops"].items()}
    c = [t for t, x in mina["tajos"].items() if x["fase"] == "carguio"]
    c.sort(key=lambda t: -mina["tajos"][t]["ley"] * tasa_scoop(mina, casa[t], t))
    r = {f"R{i + 1}": t for i, t in enumerate(c)}
    r.update({f"B{i + 1}": t for i, t in enumerate(reversed(c))})
    r["M1"] = c[len(c) // 2]
    return r


class Turno:
    """Estado del turno a medida que llegan eventos: dónde está cada scoop y qué está caído."""

    def __init__(self, mina, red):
        self.mina, self.red = mina, red
        self.casa = {s: x["casa"] for s, x in mina["scoops"].items()}       # lugar propio de cada scoop
        self.pos = dict(self.casa)                                         # dónde está ahora
        self.libre_desde = {s: x["disponible_desde"] for s, x in mina["scoops"].items()}
        self.caidos = {}       # tajo -> {"scoop", "rango_abs" (horas absolutas), "lugar"}
        self.detenidos = {}    # tajo -> rango absoluto de reapertura
        self.pasado = {s: [] for s in mina["scoops"]}                      # tramos ya ocurridos
        self.desde = INICIO

    def avanzar(self, t):
        # Registra lo que pasó entre la última decisión y "t": cada scoop sigue donde estaba
        for s in self.mina["scoops"]:
            caido = any(c["scoop"] == s for c in self.caidos.values())
            if not caido and self.pos[s] is not None and self.pos[s] not in self.detenidos:
                a = max(self.desde, self.libre_desde[s])
                if a < t:
                    self.pasado[s].append((a, t, self.pos[s]))
        self.desde = t

    def estado_en(self, t):
        # Simula desde el inicio hasta "t" con lo ya ocurrido, para saber cuánto queda en tajos y acopios
        return _cortar(self.mina, self.red, self.pasado, t)


def _cortar(mina, red, agenda, t):
    # Simulación hasta la hora t (truco: la simulación corre hasta FIN, así que se recorta la agenda)
    import mina_v2
    fin_original = mina_v2.FIN
    mina_v2.FIN = t
    try:
        return simular(mina, red, agenda, INICIO)
    finally:
        mina_v2.FIN = fin_original


def agenda_futura(T, ahora, asignacion, R, modos, reapertura):
    """Arma qué hace cada scoop desde "ahora" hasta el fin del turno, para una muestra de horas de reparación.

    asignacion: {tajo caído: scoop que lo cubre}  y  {scoop libre: reserva}
    R: {tajo caído: hora de fin de reparación}; modos: {tajo caído: "vuelve" o "cruza"}
    reapertura: {tajo detenido: hora en que reabre}
    """
    m, red = T.mina, T.red
    ag = {s: [] for s in m["scoops"]}
    cubridor = {v: k for k, v in asignacion.items() if k in T.caidos}
    destino_reserva = {k: v for k, v in asignacion.items() if k not in T.caidos}
    duenos = {c["scoop"]: tajo for tajo, c in T.caidos.items()}
    for s in m["scoops"]:
        inicio = max(ahora, T.libre_desde[s])
        if s in duenos:
            # Scoop caído: vuelve a trabajar al terminar su reparación
            tajo = duenos[s]; c = T.caidos[tajo]
            sitio = tajo if c["lugar"] == "en sitio" else c["lugar"]
            r = max(R[tajo], ahora)
            d = asignacion.get(tajo)
            if d and modos.get(tajo) == "cruza":
                ag[s].append((r + red.viaje(sitio, T.casa[d]), FIN, T.casa[d]))
            else:
                ag[s].append((r + red.viaje(sitio, tajo), FIN, tajo))
        elif s in cubridor:
            tajo = cubridor[s]; c = T.caidos[tajo]
            sitio = tajo if c["lugar"] == "en sitio" else c["lugar"]
            llega = inicio + red.viaje(T.pos[s], tajo)
            if modos.get(tajo) == "cruza":
                ag[s].append((llega, FIN, tajo))
            else:
                relevo = max(R[tajo], ahora) + red.viaje(sitio, tajo)
                ag[s].append((llega, relevo, tajo))
                ag[s].append((relevo + red.viaje(tajo, T.casa[s]), FIN, T.casa[s]))
        elif s in destino_reserva:
            res = destino_reserva[s]
            casa = T.casa[s]
            llega = inicio + red.viaje(T.pos[s], res)
            if modos.get(s) == "queda":
                ag[s].append((llega, FIN, res))
            else:
                vuelve = max(reapertura[casa], llega)
                ag[s].append((llega, vuelve, res))
                ag[s].append((vuelve + red.viaje(res, casa), FIN, casa))
        else:
            # Sin cambios: si estaba prestado y ya no cubre nada, vuelve a su lugar
            destino = T.casa[s] if T.pos[s] in T.caidos and T.pos[s] != T.casa[s] else T.pos[s]
            if destino in T.detenidos:
                ag[s].append((max(reapertura[destino], inicio), FIN, destino))
            else:
                ag[s].append((inicio + red.viaje(T.pos[s], destino), FIN, destino))
    return ag


def evaluar(T, ahora, st, asignacion, n=N_MUESTRAS):
    # Valor esperado de una asignación: promedio sobre muestras, eligiendo en cada una el mejor modo al terminar
    caidos, detenidos = list(T.caidos), list(T.detenidos)
    m_rep = {k: muestras(T.caidos[k]["rango_abs"], n) for k in caidos}
    m_reap = {k: muestras(T.detenidos[k], n) for k in detenidos}
    libres_a_reserva = [s for s in asignacion if s not in T.caidos]
    total = 0.0
    for i in range(n):
        R = {k: m_rep[k][i] for k in caidos}
        reap = {k: m_reap[k][i] for k in detenidos}
        mejor = None
        opciones = [("vuelve", "cruza") if k in asignacion else ("vuelve",) for k in caidos]
        opciones += [("vuelve", "queda")] * len(libres_a_reserva)
        for combo in itertools.product(*opciones):
            modos = dict(zip(caidos + libres_a_reserva, combo))
            st_i = copy.deepcopy(st)
            for k in detenidos:
                st_i.setdefault("parado_hasta", {})[k] = reap[k]
            v = simular(T.mina, T.red, agenda_futura(T, ahora, asignacion, R, modos, reap), ahora, st_i)["valor"]
            mejor = v if mejor is None else max(mejor, v)
        total += mejor
    return total / n


def decidir(T, ahora):
    """Prueba las asignaciones posibles y devuelve la de mayor valor esperado (y su ganancia sobre no hacer nada)."""
    st = T.estado_en(ahora)
    m = T.mina
    ocupados = {c["scoop"] for c in T.caidos.values()}
    duenos_detenidos = {s for s in m["scoops"] if T.casa[s] in T.detenidos}
    movibles = [s for s in m["scoops"]
                if s not in ocupados and s not in duenos_detenidos and T.libre_desde[s] <= ahora + 0.01
                and (T.casa[s] in m["desarrollos"] or T.casa[s] == "TALLER-564"     # el de repuesto, si ya salió del taller
                     or (T.casa[s] in m["tajos"] and m["tajos"][T.casa[s]]["fase"] == "carguio" and T.casa[s] not in T.caidos))]
    movibles += [s for s in duenos_detenidos]
    base = evaluar(T, ahora, st, {})
    # Poda: por cada tajo caído, los 4 mejores candidatos evaluados de a uno
    cand = {}
    for k in T.caidos:
        vals = [(evaluar(T, ahora, st, {k: s}, 3), s) for s in movibles]
        cand[k] = [None] + [s for v, s in sorted(vals, reverse=True)[:4]]
    for s in duenos_detenidos:
        cand[s] = [None] + list(m["reservas"])
    claves = list(cand)
    mejor = (base, {})
    for combo in itertools.product(*[cand[k] for k in claves]):
        asign = {k: v for k, v in zip(claves, combo) if v}
        scoops = [v for k, v in asign.items() if k in T.caidos] + [k for k in asign if k not in T.caidos]
        reservas = [v for k, v in asign.items() if k not in T.caidos]
        if len(scoops) != len(set(scoops)) or len(reservas) != len(set(reservas)):
            continue
        if not asign:
            continue
        v = evaluar(T, ahora, st, asign)
        if v > mejor[0] + 0.3:
            mejor = (v, asign)
    return round(mejor[0] - base, 1), mejor[1]


def aplicar(T, ahora, asign):
    # Mueve los scoops según la decisión tomada
    for k, v in asign.items():
        if k in T.caidos:
            T.pos[v] = k
        else:
            T.pos[k] = v
    for s in T.mina["scoops"]:
        if T.pos[s] in T.caidos and T.pos[s] != T.casa[s] and asign.get(T.pos[s]) != s:
            T.pos[s] = T.casa[s]


PATRONES = {
    "1 una falla": [("09:20", "falla", "R1", ((3, 4, 5), "en sitio"))],
    "2 falla corta en tajo pobre": [("10:00", "falla", "B2", ((1, 1.5, 2), "en sitio"))],
    "3 dos fallas en tajos ricos": [("09:30", "falla", "R1", ((3, 4, 5), "en sitio")),
                                    ("09:30", "falla", "R2", ((4, 5, 6), "en sitio"))],
    "4 dos fallas, una vale la pena": [("09:30", "falla", "R1", ((3, 4, 5), "en sitio")),
                                       ("09:30", "falla", "B3", ((1.5, 2, 2.5), "en sitio"))],
    "5 tres fallas casi juntas": [("09:30", "falla", "R1", ((3, 4, 5), "TALLER-564")),
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
GUARDADOS = {"5", "6", "7", "9"}


def hora(h):
    hh, mm = h.split(":"); return int(hh) + int(mm) / 60


def hhmm(t):
    return f"{int(t):02d}:{round(t % 1 * 60):02d}"


def politica_optima(T, t, ctx):
    # La clave: la mejor asignación posible con la información de ese momento
    return decidir(T, t)[1]


def clave(mina, patron, red=None, politica=None, registro=None):
    """Recorre los eventos del caso. En cada evento decide la "politica" (por defecto, la clave).

    registro: lista donde se guarda, por cada decisión, lo que decidió la política, lo mejor posible
    y cuántas onzas esperadas se pierden frente a lo mejor (para evaluar agentes).
    """
    red = red or Red(mina)
    politica = politica or politica_optima
    T = Turno(mina, red)
    T.hechos = []          # lo que las fuentes (radio, CMMS, mantenimiento) han informado hasta ahora
    rol = roles(mina)
    eventos = sorted(PATRONES[patron], key=lambda e: hora(e[0]))
    # Fin real de cada reparación: el valor más probable del último rango conocido
    t0, fin_real = {}, {}
    for h, tipo, r, dato in eventos:
        if tipo == "falla":
            t0[r] = hora(h); fin_real[r] = hora(h) + dato[0][1]
        if tipo == "actualiza":
            fin_real[r] = t0[r] + dato[1]
    eventos += [(hhmm(t), "reparado", r, None) for r, t in fin_real.items() if t < FIN]
    eventos.sort(key=lambda e: hora(e[0]))
    salida = []
    for i, (h, tipo, r, dato) in enumerate(eventos):
        t, tajo = hora(h), rol[r]
        ultimo = i == len(eventos) - 1 or eventos[i + 1][0] != h
        T.avanzar(t)
        if tipo == "reporte_dudoso":
            T.hechos.append({"hora": h, "tipo": "aviso_radio_no_confirmado", "lugar": tajo,
                             "scoop": next(s for s, c in T.casa.items() if c == tajo)})
            T.dudosos = getattr(T, "dudosos", set()) | {tajo}
            salida.append((h, f"aviso no confirmado en {tajo}", "no mover; pedir confirmación al jefe de área", 0.0))
        elif tipo == "falla":
            s = next(s for s, c in T.casa.items() if c == tajo)
            T.caidos[tajo] = {"scoop": s, "rango_abs": tuple(t + d for d in dato[0]), "lugar": dato[1]}
            T.pos[s] = None
            T.hechos.append({"hora": h, "tipo": "falla", "lugar": tajo, "scoop": s,
                             "rango_h": dato[0], "lugar_reparacion": dato[1]})
        elif tipo == "actualiza":
            T.caidos[tajo]["rango_abs"] = tuple(t0[r] + d for d in dato)
            T.hechos.append({"hora": h, "tipo": "actualizacion_estimado", "lugar": tajo,
                             "scoop": T.caidos[tajo]["scoop"], "rango_h": dato, "desde_falla": hhmm(t0[r])})
        elif tipo == "detenido":
            T.detenidos[tajo] = tuple(t + d for d in dato)
            T.hechos.append({"hora": h, "tipo": "tajo_detenido", "lugar": tajo, "rango_h": dato,
                             "scoop": next(s for s, c in T.casa.items() if c == tajo), "causa": "ventilacion insuficiente"})
        elif tipo == "reparado":
            c = T.caidos.pop(tajo)
            s = c["scoop"]
            T.hechos.append({"hora": h, "tipo": "reparado", "lugar": tajo, "scoop": s})
            sitio = tajo if c["lugar"] == "en sitio" else c["lugar"]
            prestado = next((x for x, p in T.pos.items() if p == tajo and x != s), None)
            if prestado:
                casa_p = T.casa[prestado]
                # Compara: el prestado vuelve a su lugar, o el reparado va al lugar del prestado
                st = T.estado_en(t)
                ag_v = {x: [(t, FIN, T.pos[x])] for x in T.mina["scoops"] if T.pos[x]}
                ag_v[s] = [(t + red.viaje(sitio, tajo), FIN, tajo)]
                ag_v[prestado] = [(t + red.viaje(sitio, tajo), t + red.viaje(sitio, tajo) + 0.01, tajo),
                                  (t + red.viaje(sitio, tajo) + red.viaje(tajo, casa_p), FIN, casa_p)]
                ag_c = {x: [(t, FIN, T.pos[x])] for x in T.mina["scoops"] if T.pos[x]}
                ag_c[s] = [(t + red.viaje(sitio, casa_p), FIN, casa_p)]
                v_v = simular(mina, red, ag_v, t, st)["valor"]
                v_c = simular(mina, red, ag_c, t, st)["valor"]
                if v_c > v_v + 0.3:
                    T.casa[s], T.casa[prestado] = casa_p, tajo
                    T.pos[s] = casa_p
                    salida.append((h, f"{s} reparado", f"{s} va a {casa_p}; {prestado} se queda en {tajo}", round(v_c - v_v, 1)))
                else:
                    T.pos[s] = tajo; T.pos[prestado] = casa_p
                    salida.append((h, f"{s} reparado", f"{prestado} vuelve a {casa_p}", 0.0))
            else:
                T.pos[s] = tajo
            continue
        if not ultimo:
            continue
        ctx = {"hora": h, "eventos": [e for e in eventos if e[0] == h], "rol": rol}
        gana_opt, asign_opt = decidir(T, t)
        if politica is politica_optima:
            gana, asign = gana_opt, asign_opt
        else:
            asign = politica(T, t, ctx) or {}
            st = T.estado_en(t)
            base = evaluar(T, t, st, {})
            limpia = {k: v for k, v in asign.items() if (k in T.caidos and v in T.mina["scoops"])
                      or (k in T.mina["scoops"] and v in T.mina["reservas"])}
            gana = round(evaluar(T, t, st, limpia) - base, 1) if limpia else 0.0
            if registro is not None:
                registro.append({"hora": h, "patron": patron, "agente": asign, "agente_valida": limpia,
                                 "mejor": asign_opt, "oz_agente": gana, "oz_mejor": gana_opt,
                                 "perdida_oz": round(max(0.0, gana_opt - gana), 1),
                                 "dudosos": sorted(getattr(T, "dudosos", set()))})
            asign = limpia
        texto = "; ".join(f"{v} a {k}" if k in T.caidos else f"{k} a reserva {v}" for k, v in asign.items()) or "no mover nada"
        aplicar(T, t, asign)
        quien = ", ".join(f"{e[1]} {rol[e[2]]}" for e in eventos if e[0] == h and e[1] in ("falla", "actualiza", "detenido"))
        salida.append((h, quien, texto, gana))
    return salida


if __name__ == "__main__":
    import sys
    import time
    for semilla in [int(a) for a in sys.argv[1:]] or [0]:
        mina = generar(semilla)
        red = Red(mina)
        print(f"\n=== Turno {semilla} === roles:", {k: v for k, v in roles(mina).items() if k in ("R1", "R2", "R3", "B1", "M1")})
        for p in PATRONES:
            t = time.time()
            for paso in clave(mina, p, red):
                print(f"  [{p}] {paso[0]} {paso[1]}: {paso[2]} (+{paso[3]} oz)")
            print(f"     ({time.time() - t:.1f} s)")
