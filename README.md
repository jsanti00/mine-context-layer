# Mine Context Layer

A shared context layer for an underground mine that AI agents read and write through MCP.

In a mine, what happens during a shift is spread across systems that don't talk to each other: radio, shift reports, the production plan, the maintenance system (CMMS). Any agent added to the operation would have to rebuild that context on its own. Mine Context Layer gives every agent the same view of the shift, with the origin of every fact and clear permissions:

- **Source agents** (radio, CMMS) publish events from their own system.
- **Function agents** (maintenance, planning) read everything and submit proposals.
- **Only a human** approves an action, from the terminal. No agent can approve through MCP.

MIT MAS.665 AI Studio, Fall 2026. HW6 MVP. All data is synthetic (Synappse sales-demo dataset); no real mine or company.

## What the MVP does

Scenario INC-01: at 09:18 a loader operator radios that "scoop 7" lost hydraulic pressure in stope "1310-04", the highest-grade stope of the shift.

```
 SOURCES (publish)                 LAYER (MCP + SQLite)            FUNCTIONS (read + propose)
 radio-agent ───── event ───────>  events                  <────── maintenance-agent (assessment)
 cmms-agent  ───── event ───────>  proposals               <────── planning-agent    (action)
                                   decisions + call trace
                                   plan, fleet, rules (seed)
                                          │
                                          └──> human approves in the terminal (decision log)
```

1. The **radio agent** hears the radio lines and publishes an `equipment_failure`. The layer resolves "scoop 7" to `LH-07` and "1310-04" to `TP-1310-04`.
2. A small dispatcher wakes agents by subscription. The **CMMS agent** sees the failure and publishes LH-07's repair history and its overdue preventive order.
3. The **maintenance agent** turns that into a repair estimate (range and expected return time).
4. The **planning agent** compares options (do nothing, wait, reassign a loader), applies the mine's dispatch rules and submits an action with numbers, calculation, sources and confidence.
5. The **human** approves or rejects. The decision is logged.
6. The run is **checked against the demo's answer key**, which the agents never see (they run with no file access, only the layer's MCP tools).

## MCP tools

| Tool | Who | What |
|---|---|---|
| `publish_event` | sources | Publish an event with time, validity, author, source, confidence and a link to the original record. Mentions are resolved to system ids |
| `get_shift_snapshot` | all | Clock, targets, operating rules, source health, open events, proposals |
| `get_open_events` | all | Events still valid, by type or id |
| `get_equipment_state` | all | Shift-start fleet data plus open events |
| `get_plan_slice` | all | Stopes (tonnes, grade, loader, priority) and idle places |
| `submit_proposal` | functions | `assessment` (no approval) or `action` (needs a human). Every cited source must exist |
| `get_proposals`, `get_decisions` | all | Proposals and the decision log |

## Run it

Requires [uv](https://docs.astral.sh/uv/) and [Claude Code](https://claude.com/claude-code) logged in. Agents run as headless Claude Code sessions (Sonnet).

```bash
uv sync
uv run run.py                       # you approve in the terminal
uv run run.py --scenario cmms-down  # failure case: CMMS offline
uv run baseline.py                  # one agent reading raw files, no layer
uv run bench.py 3                   # repeat everything, write evidence/benchmark.md
```

Each run saves its database, every agent transcript and a `report.json` in `runs/`.

## Results

Benchmark, 3 runs per configuration (`evidence/benchmark.md`, transcripts in `evidence/bench-runs/`):

| Configuration | Correct action | Error, t at risk (no action) | Seconds to proposal | Cost per correct proposal | Confidence |
|---|---|---|---|---|---|
| Layer, 4 agents | 3/3 | 18% | 70 | $0.17 | 0.68 |
| Layer, CMMS down | 3/3 | 22% | 56 | $0.12 | 0.55 |
| Single agent, raw files | 3/3 | 20% | 39 | $0.20 | free text |
| Single agent, CMMS down | 3/3 | 20% | 41 | $0.19 | free text |

- The 4-agent chain proposes the answer-key action (move LH-11 from TP-1250-02 to TP-1310-04) every time, in about a minute, well inside the 15-minute decision window.
- With the CMMS down, the agents still propose, say what is missing and who to ask, and lower their confidence (0.68 to 0.55).
- The layer runs cost about 15% less than one agent reading the raw files, but take longer because four agents run in sequence.
- Without the mine's dispatch rules in the layer, the planning agent picked a same-level loader from a fill stope instead (`evidence/run1-no-dispatch-rules`). Adding the rules to the layer fixed it.

## Current limitations

- One incident (INC-01) and one shift. Identity resolution uses a rule-based alias table, not learned matching.
- Impact numbers are about 20% above the demo's calibrated key for "no action". The key also leaves out the ore lost at the stope the loader is taken from, so with-action numbers are not directly comparable.
- On this single, well-specified incident, one agent reading the raw files also finds the right action. The layer's value is in permissions, traceability, source health and reuse across agents; testing where that matters (conflicting sources, many incidents) is next.
- Dispatch rule LOADER-BACKUP-02 (stopes in drill or fill cycles keep their loader) was made explicit after the first run; the demo dataset only implied it.

## Repository

```
mcl/store.py       shared SQLite store, seed loader, id resolution
mcl/server.py      MCP server with permissions per role
agents/prompts.py  system prompts of the four agents
agents/runner.py   runs one agent headless and prints each MCP call
run.py             dispatcher, human approval, evaluation
baseline.py        single agent with raw files
eval/              answer key (INC-01 only) and scoring
data/seed/         synthetic mine dataset
data/site_rules.json  dispatch rules of the synthetic mine
evidence/          saved runs and benchmark
```
