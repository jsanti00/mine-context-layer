"""
Runs the INC-01 scenario end to end.

  uv run run.py                     normal run, you approve in the terminal
  uv run run.py --scenario cmms-down the CMMS is offline during the incident
  uv run run.py --auto-approve      unattended run (for repeated evaluation)

Flow: the radio agent hears the shift radio and publishes events. A small dispatcher
watches the layer and wakes each agent when something it subscribes to appears:
  equipment_failure -> cmms-agent (if the CMMS is online)
  failure + CMMS records (or CMMS down) -> maintenance-agent
  repair assessment -> planning-agent
The planning agent's action proposal then waits for a human decision.
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from agents import prompts
from agents.runner import run_agent
from mcl import store
from eval.evaluate import evaluate, print_report

BOLD, RESET = "\033[1m", "\033[0m"


def say(text):
    # Messages from the dispatcher itself
    print(f"{BOLD}[layer]{RESET} {text}")


def csv_text(rows):
    # Turn the CMMS export into compact text for the CMMS agent
    out = []
    for name, items in rows.items():
        out.append(f"# {name}")
        if items:
            out.append(",".join(items[0].keys()))
            out += [",".join(r.values()) for r in items]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="normal", choices=["normal", "cmms-down"])
    ap.add_argument("--auto-approve", action="store_true")
    ap.add_argument("--model", default="sonnet")
    args = ap.parse_args()

    # Each run gets its own folder with the database, transcripts and report
    run_dir = ROOT / "runs" / f"{datetime.now():%Y%m%d-%H%M%S}-{args.scenario}"
    run_dir.mkdir(parents=True)
    db_path = run_dir / "layer.db"
    con = store.connect(db_path)
    store.seed(con)
    say(f"run {run_dir.name}: plan, fleet and stope inventory loaded")

    # Simulate the CMMS outage if asked
    if args.scenario == "cmms-down":
        con.execute("UPDATE sources SET status='down', since='09:00', "
                    "note='CMMS API not responding (HTTP 503) since 09:00' WHERE name='cmms'")
        con.commit()
        say("CMMS is DOWN for this run")

    # The radio feed between 09:00 and 09:31, routine chatter included
    feed = store.radio_feed("09:00", "09:31")
    store.set_meta(con, "sim_now", "09:31")
    say(f"shift clock 09:31, {len(feed)} radio lines arriving")
    t0 = time.time()
    runs = []

    radio_prompt = "Radio lines just heard (JSON):\n" + json.dumps(feed, ensure_ascii=False, indent=1)
    runs.append(run_agent("radio-agent", "source", prompts.RADIO, radio_prompt, db_path,
                          source="radio", model=args.model, log_dir=run_dir))

    # Dispatcher: wake agents by subscription until nothing new happens
    done = set()
    while True:
        failures = con.execute("SELECT id FROM events WHERE type='equipment_failure'").fetchall()
        cmms_online = con.execute("SELECT status FROM sources WHERE name='cmms'").fetchone()["status"] == "online"
        assessments = con.execute("SELECT id FROM proposals WHERE kind='assessment'").fetchall()
        actions = con.execute("SELECT id FROM proposals WHERE kind='action'").fetchall()
        if failures and cmms_online and "cmms" not in done:
            say(f"{failures[0]['id']} equipment_failure -> waking cmms-agent")
            done.add("cmms")
            runs.append(run_agent("cmms-agent", "source",
                                  prompts.CMMS.format(cmms=csv_text(store.load_cmms_rows())),
                                  "New equipment failures were published. Add what the CMMS knows.",
                                  db_path, source="cmms", model=args.model, log_dir=run_dir))
        elif failures and "maint" not in done:
            why = "CMMS records" if cmms_online else "CMMS is down, no records"
            say(f"failure + {why} -> waking maintenance-agent")
            done.add("maint")
            runs.append(run_agent("maintenance-agent", "function", prompts.MAINTENANCE.format(),
                                  "Estimate the repair for the open equipment failures.",
                                  db_path, model=args.model, log_dir=run_dir))
        elif assessments and not actions and "plan" not in done:
            say(f"{assessments[-1]['id']} repair assessment -> waking planning-agent")
            done.add("plan")
            runs.append(run_agent("planning-agent", "function", prompts.PLANNING.format(),
                                  "A loader failed. Propose the best action for the shift.",
                                  db_path, model=args.model, log_dir=run_dir))
        else:
            break
    seconds_to_proposal = round(time.time() - t0, 1)

    # Human decision, outside MCP: no agent can approve
    action = con.execute("SELECT * FROM proposals WHERE kind='action' ORDER BY id DESC").fetchone()
    if action:
        rec = json.loads(action["recommendation"])
        print(f"\n{BOLD}=== {action['id']} waiting for approval ==={RESET}")
        print(f"{action['title']}\n{json.dumps(rec, ensure_ascii=False, indent=1)}")
        print(f"confidence {action['confidence']}, sources {action['sources']}")
        missing = json.loads(action["missing_info"] or "[]")
        if missing:
            print("missing info: " + "; ".join(missing))
        if args.auto_approve:
            decision, note, who = "approved", "auto-approved for unattended evaluation run", "auto"
        else:
            ans = input(f"{BOLD}Approve? [y/n]{RESET} ").strip().lower()
            decision = "approved" if ans.startswith("y") else "rejected"
            note, who = input("Note (optional): ").strip(), "shift-supervisor"
        con.execute("INSERT INTO decisions (proposal_id, decision, decided_by, note, decided_at) "
                    "VALUES (?,?,?,?,?)", (action["id"], decision, who, note, store.now_wall()))
        con.execute("UPDATE proposals SET status=? WHERE id=?", (decision, action["id"]))
        con.commit()
        say(f"{action['id']} {decision}, logged in the decision log")
    else:
        say("no action proposal was produced")

    # Check the result against the demo's answer key
    report = evaluate(db_path, runs, seconds_to_proposal, args.scenario)
    (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print_report(report)
    say(f"evidence saved in {run_dir.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
