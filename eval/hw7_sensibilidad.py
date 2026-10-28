"""Sensibilidad de la clave de HW7 a las velocidades de traslado: ¿cambia la mejor decisión?"""
import json
from hw7_sim import MAPA, PATRONES, Mapa, clave, turno

esc = MAPA["sensitivity"]
mapas = {k: Mapa({"drift": v["drift"], "ramp": v["ramp"], "surface": v["surface"]}, v["overhead_h"]) for k, v in esc.items()}
turnos = [turno(s) for s in range(6)]
cambios, total, detalle = {"low": 0, "high": 0}, 0, []
for tr in turnos:
    for p in PATRONES:
        # Comparamos las decisiones (sin las onzas) de cada escenario contra la base
        dec = {k: [paso[2] for paso in clave(tr, p, m)] for k, m in mapas.items()}
        total += 1
        for k in ("low", "high"):
            if dec[k] != dec["base"]:
                cambios[k] += 1
                detalle.append((tr["semilla"], p, k, dec["base"], dec[k]))
print(f"casos evaluados: {total} (6 turnos x {len(PATRONES)} patrones)")
print("casos cuya mejor decisión cambia:", cambios)
for d in detalle:
    print(d[0], d[1], d[2], "\n   base:", d[3], "\n   otro:", d[4])
