"""
Checks a run against the answer key of the Synappse demo dataset (INC-01).

The answer key is only read here, after the agents finish. Agents never see it:
they run without file access and the layer does not expose it.
"""

import json
import sqlite3
from pathlib import Path

KEY = json.loads((Path(__file__).resolve().parent / "answer_key.json").read_text())


def pct_error(estimate, truth):
    # How far an estimate is from the key, in percent
    if estimate is None:
        return None
    return round(abs(float(estimate) - truth) / truth * 100, 1)


def option(options, option_id):
    # Find one option by its id in the agent's list
    for o in options or []:
        if o.get("option_id") == option_id:
            return o
    return {}


def evaluate(db_path, runs, seconds_to_proposal, scenario):
    """Score the run: action, impact numbers, traceability, speed and cost."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    inc = KEY["incident"]
    key_equipment = inc["recomendacion"]["recurso_origen"].split()[0]  # "LH-11"
    key_from = inc["recomendacion"]["recurso_origen"].split("en ")[1].split(",")[0]  # "TP-1250-02"
    key_to = inc["recomendacion"]["recurso_destino"]  # "TP-1310-04"
    key_risk = inc["impacto"]["rechazado"]["toneladas"]  # tonnes lost with no action
    key_residual = inc["impacto"]["aceptado"]["toneladas_residual"]  # tonnes lost with the action
    key_risk_oz = inc["impacto"]["rechazado"]["onzas"]  # ounces lost with no action

    # Did the radio agent and the layer turn "scoop 7 ... 1310-04" into LH-07 at TP-1310-04?
    failures = [dict(r) for r in con.execute("SELECT * FROM events WHERE type='equipment_failure'")]
    resolved = [f for f in failures if "LH-07" in json.loads(f["subject_ids"]) and f["location"] == "TP-1310-04"]

    action = con.execute("SELECT * FROM proposals WHERE kind='action' ORDER BY id DESC").fetchone()
    report = {"scenario": scenario, "answer_key": f"{inc['id']} / {inc['recomendacion']['id']}: "
                                                  f"move {key_equipment} from {key_from} to {key_to}"}
    report["id_resolution_ok"] = bool(resolved)
    if action:
        rec = json.loads(action["recommendation"])
        opts = json.loads(action["options"])
        sources = json.loads(action["sources"])
        do_nothing = option(opts, "do_nothing").get("tonnes_at_risk")
        do_nothing_oz = option(opts, "do_nothing").get("oz_at_risk")
        report.update({
            "proposal_id": action["id"],
            "proposed": f"move {rec.get('equipment_id')} from {rec.get('from_place')} to {rec.get('to_place')}",
            "action_correct": rec.get("equipment_id") == key_equipment and rec.get("to_place") == key_to,
            "tonnes_at_risk_no_action": {"agent": do_nothing, "key": key_risk,
                                         "error_pct": pct_error(do_nothing, key_risk)},
            "oz_at_risk_no_action": {"agent": do_nothing_oz, "key": key_risk_oz,
                                     "error_pct": pct_error(do_nothing_oz, key_risk_oz)},
            # Not scored: the demo key leaves out the ore lost at the stope the loader
            # is taken from, while the agent nets it out, so the two are not comparable
            "with_action_note": {"agent_net_oz_protected": rec.get("net_oz_protected"),
                                 "agent_oz_lost_at_donor_stope": rec.get("oz_lost_elsewhere"),
                                 "key_residual_tonnes_excluding_donor": key_residual},
            "cites_failure_event": any(f["id"] in sources for f in failures),
            "confidence": action["confidence"],
            "missing_info": json.loads(action["missing_info"] or "[]"),
            "decision": (con.execute("SELECT decision FROM decisions WHERE proposal_id=?",
                                     (action["id"],)).fetchone() or {"decision": None})["decision"],
        })
    else:
        report.update({"proposal_id": None, "action_correct": False})
    cost = round(sum(r["cost_usd"] for r in runs), 4)
    report.update({
        "seconds_radio_to_proposal": seconds_to_proposal,
        "within_15_min_window": seconds_to_proposal <= 900,
        "cost_usd_total": cost,
        "cost_per_correct_proposal": cost if report.get("action_correct") else None,
        "agents": runs,
        "tool_calls": con.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0],
        "denied_calls": con.execute("SELECT COUNT(*) FROM tool_calls WHERE ok=0").fetchone()[0],
    })
    return report


def print_report(r):
    # Short, readable summary at the end of a run
    ok = lambda b: "\033[32mPASS\033[0m" if b else "\033[31mFAIL\033[0m"
    print("\n\033[1m=== Check against the demo answer key ===\033[0m")
    print(f"answer key:          {r['answer_key']}")
    print(f"agents proposed:     {r.get('proposed')}  {ok(r.get('action_correct'))}")
    print(f"ID resolution:       scoop 7 / 1310-04 -> LH-07 / TP-1310-04  {ok(r.get('id_resolution_ok'))}")
    if r.get("proposal_id"):
        a, b = r["tonnes_at_risk_no_action"], r["oz_at_risk_no_action"]
        n = r["with_action_note"]
        print(f"at risk, no action:  agent {a['agent']} t / {b['agent']} oz vs key {a['key']} t / {b['key']} oz "
              f"({a['error_pct']}% / {b['error_pct']}% off)")
        print(f"with the action:     agent nets {n['agent_net_oz_protected']} oz after "
              f"{n['agent_oz_lost_at_donor_stope']} oz lost at the donor stope (key ignores the donor loss)")
        print(f"traceable sources:   {ok(r.get('cites_failure_event'))}   confidence {r.get('confidence')}")
        if r.get("missing_info"):
            print(f"missing info flagged: {len(r['missing_info'])} item(s)")
    print(f"radio -> proposal:   {r['seconds_radio_to_proposal']} s  {ok(r['within_15_min_window'])} (15-min window)")
    print(f"cost:                ${r['cost_usd_total']} total, {r['tool_calls']} MCP calls, {r['denied_calls']} denied")
