"""Sensibilidad de la clave v2: velocidades de traslado y valor de una hora de desarrollo."""
import sys
import mina_v2
from clave_v2 import PATRONES, clave
from mina_v2 import Red, generar

TURNOS = range(6)
ESC_VEL = {"bajo": ({"drift": 8, "ramp": 4, "surface": 25}, 0.5), "base": (None, None),
           "alto": ({"drift": 18, "ramp": 10, "surface": 60}, 0.08)}


def decisiones(vel, maniobra, dev):
    # Corre todos los casos y guarda solo las decisiones (sin onzas)
    mina_v2.MULT_DESARROLLO = dev
    out = {}
    for s in TURNOS:
        m = generar(s); red = Red(m, vel, maniobra)
        for p in PATRONES:
            out[(s, p)] = [x[2] for x in clave(m, p, red)]
    return out


base = decisiones(None, None, 1.0)
print("casos:", len(base))
for nombre, (vel, man) in ESC_VEL.items():
    if nombre == "base":
        continue
    otro = decisiones(vel, man, 1.0)
    print(f"velocidad {nombre}: cambia la decisión en {sum(otro[k] != base[k] for k in base)} de {len(base)}")
for dev in (0.0, 0.5, 2.0):
    otro = decisiones(None, None, dev)
    print(f"valor del desarrollo x{dev}: cambia la decisión en {sum(otro[k] != base[k] for k in base)} de {len(base)}")
