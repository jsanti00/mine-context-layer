"""
Recalifica un banco ya corrido sin volver a llamar a los agentes: repite sus decisiones guardadas
en el mismo turno y vuelve a calcular la clave (sirve si se corrige la clave).
Uso: python eval/recalificar.py runs2/hw7-v2
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from clave_v2 import PATRONES, clave          # la clave y los patrones de casos
from mina_v2 import Red, generar              # el generador de turnos


def repetir(decisiones):
    # Política que devuelve, en orden, lo que el agente decidió en la corrida original
    cola = list(decisiones)
    return lambda T, t, ctx: dict(cola.pop(0)["agente"]) if cola else {}


carpeta = Path(sys.argv[1])
cambios = 0
for f in sorted(carpeta.glob("t*_p*_*.json")):
    d = json.loads(f.read_text())
    mina = generar(d["turno"])                                 # mismo turno sintético
    registro = []
    clave(mina, d["patron"], Red(mina), repetir(d["decisiones"]), registro)
    for viejo, nuevo in zip(d["decisiones"], registro):
        # Guarda la clave nueva y la pérdida sin recortar (negativa si el agente superó a la clave)
        dif = round(nuevo["oz_mejor"] - nuevo["oz_agente"], 1)
        if abs(max(0.0, dif) - viejo["perdida_oz"]) > 0.05:
            cambios += 1
        viejo.update(mejor=nuevo["mejor"], oz_agente=nuevo["oz_agente"], oz_mejor=nuevo["oz_mejor"],
                     perdida_oz=max(0.0, dif), diferencia_oz=dif)
    f.write_text(json.dumps(d, ensure_ascii=False, indent=1))
print("decisiones con pérdida distinta:", cambios)
