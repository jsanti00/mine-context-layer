# HW7 benchmark results

One JSON per case (`t<shift>_p<pattern>_<config>.json`): every decision moment with the agent's raw decision, the valid assignment, the answer key's best decision and the ounces lost against it.

- `run1-surface-metric/`: value = ounces hauled to surface by 17:00. Configs A, B, C.
- `run2-stockpile-metric/`: value = ounces at surface + ounces in level stockpiles at 17:00. Configs A, B, C and D (layer v3).

Configs: A layer, B layer with one planner per failure, C single agent with raw files, D layer v3 (`mcl2/server_v3.py`: `rank_loan_options` and `check_decision`). Model: Claude Sonnet.

## Run 2 summary (54 cases)

| Config | Oz lost | Cases with no loss | Decisions dropped for rule violations | USD per case | Seconds per decision |
|---|---|---|---|---|---|
| A. Layer | 692 | 15 | 2 | 0.07 | 17 |
| B. One failure at a time | 836 | 18 | 7 | 0.08 | 27 |
| C. Raw files | 893 | 18 | 13 | 0.10 | 50 |
| D. Layer v3 | 225 | 39 | 0 | 0.04 | 12 |

Run 1 (surface metric): A 967 oz, B 1,025 oz, C 854 oz lost.

Reproduce: `.venv/bin/python bench2.py --run <name> --configs A B C D`; re-score saved decisions with `eval/recalificar.py runs2/<name>`.
