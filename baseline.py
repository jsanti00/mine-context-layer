"""
Baseline for the comparison promised in HW5: ONE agent, no layer.

It gets the same information as the multi-agent system (the radio lines, the raw
plan, fleet, inventory and CMMS files, and the same dispatch rules), but as raw
files it has to read itself. It cannot see the answer key either.

  uv run baseline.py
"""

import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from agents.runner import run_agent
from eval.evaluate import KEY, pct_error
from mcl import store

SYSTEM = """You are the planning assistant of an underground gold mine. You have the raw
data files of the current shift in your working folder and can only read them.
A shift decision has a 15-minute window, so work quickly. Do not invent numbers."""

TASK = """Radio lines just heard (09:00 to 09:31, Spanish, mining slang):
{feed}

A loader failed. Propose the best action for the shift. Files available: plan
(Plan_de_turno.json), fleet (Telemetria_flota.json), places (Inventario_de_frentes.json),
CMMS (two CSV files) and the mine's dispatch rules (site_rules.json). Productive time ends at 17:00.
Compare options "do_nothing", "wait_for_repair" and "reassign_loader" (pick the best loader),
with tonnes and ounces at risk for each (total loss for the shift, including where a loader is
taken from). oz = tonnes x grade_gpt / 31.1035.

Answer ONLY with one JSON object, no other text:
{{"failed_equipment", "failed_place",
  "recommendation": {{"action_type", "equipment_id", "from_place", "to_place", "start_by",
    "tonnes_protected", "oz_protected", "tonnes_lost_elsewhere", "oz_lost_elsewhere", "net_oz_protected"}},
  "options": [{{"option_id", "description", "tonnes_at_risk", "oz_at_risk"}}],
  "calculation", "sources": [file names and record ids used], "confidence", "missing_info": []}}"""


def run_baseline(run_dir, model="sonnet", cmms_down=False):
    """Run the single agent once and score it like the layer runs."""
    raw = run_dir / "raw"
    raw.mkdir(parents=True)
    # Same seed files plus the same rules, as plain files
    for f in store.SEED.iterdir():
        shutil.copy(f, raw / f.name)
    shutil.copy(ROOT / "data/site_rules.json", raw / "site_rules.json")
    # Radio_transcripciones.json would reveal incident tags, so the agent only gets the feed
    (raw / "Radio_transcripciones.json").unlink()
    # The raw telemetry export also holds the demo's scripted failure events; the layer
    # only loads the shift-start fleet state, so the baseline gets the same
    tel = json.loads((raw / "Telemetria_flota.json").read_text())
    tel.pop("estados_por_incidente", None)
    (raw / "Telemetria_flota.json").write_text(json.dumps(tel, ensure_ascii=False, indent=1))
    note = ""
    if cmms_down:
        # Same outage as the layer scenario: no CMMS files
        for f in raw.glob("CMMS_*.csv"):
            f.unlink()
        note = "\nNOTE: the CMMS is down since 09:00 (HTTP 503); its files are not available.\n"
    feed = store.radio_feed("09:00", "09:31")
    t0 = time.time()
    res = run_agent("single-agent", "none", SYSTEM, TASK.format(feed=json.dumps(feed, ensure_ascii=False, indent=1)) + note,
                    db_path=None, model=model, builtin_tools="Read,Glob,Grep", workdir=raw,
                    use_layer=False, log_dir=run_dir)
    seconds = round(time.time() - t0, 1)
    # Pull the JSON answer out of the final message
    match = re.search(r"\{.*\}", res["final"], re.S)
    try:
        ans = json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        ans = {}
    inc = KEY["incident"]
    rec = ans.get("recommendation", {})
    do_nothing = next((o for o in ans.get("options", []) if o.get("option_id") == "do_nothing"), {})
    report = {
        "scenario": "baseline-cmms-down" if cmms_down else "baseline",
        "proposed": f"move {rec.get('equipment_id')} from {rec.get('from_place')} to {rec.get('to_place')}",
        "action_correct": rec.get("equipment_id") == "LH-11" and rec.get("to_place") == inc["recomendacion"]["recurso_destino"],
        "id_resolution_ok": str(ans.get("failed_equipment", "")).startswith("LH-07")
                            and str(ans.get("failed_place", "")).startswith("TP-1310-04"),
        "missing_info": ans.get("missing_info"),
        "tonnes_at_risk_no_action": {"agent": do_nothing.get("tonnes_at_risk"), "key": inc["impacto"]["rechazado"]["toneladas"],
                                     "error_pct": pct_error(do_nothing.get("tonnes_at_risk"), inc["impacto"]["rechazado"]["toneladas"])},
        "oz_at_risk_no_action": {"agent": do_nothing.get("oz_at_risk"), "key": inc["impacto"]["rechazado"]["onzas"],
                                 "error_pct": pct_error(do_nothing.get("oz_at_risk"), inc["impacto"]["rechazado"]["onzas"])},
        "confidence": ans.get("confidence"),
        "sources": ans.get("sources"),
        "parsed_answer": bool(ans),
        "seconds_radio_to_proposal": seconds,
        "within_15_min_window": seconds <= 900,
        "cost_usd_total": res["cost_usd"],
        "cost_per_correct_proposal": None,
        "agents": [res],
    }
    if report["action_correct"]:
        report["cost_per_correct_proposal"] = res["cost_usd"]
    (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    d = ROOT / "runs" / f"{datetime.now():%Y%m%d-%H%M%S}-baseline"
    r = run_baseline(d)
    print(json.dumps({k: v for k, v in r.items() if k != "agents"}, ensure_ascii=False, indent=1))
