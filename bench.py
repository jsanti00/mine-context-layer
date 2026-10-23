"""
Repeats each configuration several times and writes evidence/benchmark.md.

  uv run bench.py 3
"""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from baseline import run_baseline


def layer_run(scenario):
    # Run the full multi-agent pipeline unattended and read its report
    subprocess.run([sys.executable, str(ROOT / "run.py"), "--scenario", scenario, "--auto-approve"],
                   check=True, stdout=subprocess.DEVNULL)
    last = sorted((ROOT / "runs").glob(f"*-{scenario}"))[-1]
    return json.loads((last / "report.json").read_text()), last.name


def summarize(name, reports):
    # One row of the comparison table
    n = len(reports)
    rate = lambda k: f"{sum(bool(r.get(k)) for r in reports)}/{n}"
    avg = lambda xs: round(sum(xs) / len(xs), 1) if xs else None
    errs = [r["tonnes_at_risk_no_action"]["error_pct"] for r in reports
            if r.get("tonnes_at_risk_no_action", {}).get("error_pct") is not None]
    conf = [r["confidence"] for r in reports if isinstance(r.get("confidence"), (int, float))]
    flagged = [r for r in reports if r.get("missing_info")]
    cost = round(sum(r["cost_usd_total"] for r in reports) / n, 3)
    correct = sum(bool(r.get("action_correct")) for r in reports)
    return (f"| {name} | {rate('action_correct')} | {rate('id_resolution_ok')} | {avg(errs)}% | "
            f"{avg([r['seconds_radio_to_proposal'] for r in reports])} | ${cost} | "
            f"{'$' + str(round(cost * n / correct, 3)) if correct else 'n/a'} | "
            f"{avg(conf) if conf else 'text'} | {len(flagged)}/{n} |")


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    results = {"layer (4 agents)": [], "layer, CMMS down": [], "single agent, raw files": [],
               "single agent, CMMS down": []}
    names = {k: [] for k in results}
    for i in range(n):
        for scenario, key in (("normal", "layer (4 agents)"), ("cmms-down", "layer, CMMS down")):
            r, run_name = layer_run(scenario)
            results[key].append(r); names[key].append(run_name)
            print(f"{key} #{i + 1}: {r.get('proposed')} correct={r.get('action_correct')}")
        for down, key in ((False, "single agent, raw files"), (True, "single agent, CMMS down")):
            d = ROOT / "runs" / f"{datetime.now():%Y%m%d-%H%M%S}-{'baseline-cmms-down' if down else 'baseline'}"
            r = run_baseline(d, cmms_down=down)
            results[key].append(r); names[key].append(d.name)
            print(f"{key} #{i + 1}: {r.get('proposed')} correct={r.get('action_correct')}")
    lines = [f"# Benchmark: INC-01, {n} runs per configuration ({datetime.now():%Y-%m-%d %H:%M})", "",
             "Answer key (Synappse demo dataset): move LH-11 from TP-1250-02 to TP-1310-04; 190 t at risk with no action.", "",
             "| Configuration | Correct action | IDs resolved | Error, t at risk | Seconds to proposal | Avg cost | Cost per correct | Avg confidence | Missing info flagged |",
             "|---|---|---|---|---|---|---|---|---|"]
    lines += [summarize(k, v) for k, v in results.items()]
    lines += ["", "Cost is the API-equivalent cost reported by Claude Code (model: Sonnet).", "",
              "Proposals per run:", ""]
    for k, reps in results.items():
        for r, nm in zip(reps, names[k]):
            lines.append(f"- {k} ({nm}): {r.get('proposed')}, correct={r.get('action_correct')}, "
                         f"confidence={r.get('confidence') if isinstance(r.get('confidence'), (int, float)) else 'text'}")
    out = ROOT / "evidence" / "benchmark.md"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
