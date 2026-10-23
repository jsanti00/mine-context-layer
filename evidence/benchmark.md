# Benchmark: INC-01, 3 runs per configuration (2026-10-23 09:28)

Answer key (Synappse demo dataset): move LH-11 from TP-1250-02 to TP-1310-04; 190 t at risk with no action.

| Configuration | Correct action | IDs resolved | Error, t at risk | Seconds to proposal | Avg cost | Cost per correct | Avg confidence | Missing info flagged |
|---|---|---|---|---|---|---|---|---|
| layer (4 agents) | 3/3 | 3/3 | 18.4% | 69.5 | $0.165 | $0.165 | 0.7 | 3/3 |
| layer, CMMS down | 3/3 | 3/3 | 21.6% | 56.4 | $0.122 | $0.122 | 0.6 | 3/3 |
| single agent, raw files | 3/3 | 3/3 | 19.7% | 38.8 | $0.195 | $0.195 | text | 3/3 |
| single agent, CMMS down | 3/3 | 3/3 | 20.3% | 40.6 | $0.189 | $0.189 | text | 3/3 |

Cost is the API-equivalent cost reported by Claude Code (model: Sonnet).

Proposals per run:

- layer (4 agents) (20261023-091750-normal): move LH-11 from TP-1250-02 to TP-1310-04, correct=True, confidence=0.7
- layer (4 agents) (20261023-092114-normal): move LH-11 from TP-1250-02 to TP-1310-04, correct=True, confidence=0.65
- layer (4 agents) (20261023-092433-normal): move LH-11 from TP-1250-02 to TP-1310-04, correct=True, confidence=0.7
- layer, CMMS down (20261023-091859-cmms-down): move LH-11 from TP-1250-02 to TP-1310-04, correct=True, confidence=0.55
- layer, CMMS down (20261023-092223-cmms-down): move LH-11 from TP-1250-02 to TP-1310-04, correct=True, confidence=0.55
- layer, CMMS down (20261023-092544-cmms-down): move LH-11 from TP-1250-02 to TP-1310-04, correct=True, confidence=0.55
- single agent, raw files (20261023-091951-baseline): move LH-11 from TP-1250-02 to TP-1310-04, correct=True, confidence=text
- single agent, raw files (20261023-092319-baseline): move LH-11 from TP-1250-02 to TP-1310-04, correct=True, confidence=text
- single agent, raw files (20261023-092646-baseline): move LH-11 from TP-1250-02 to TP-1310-04, correct=True, confidence=text
- single agent, CMMS down (20261023-092027-baseline-cmms-down): move LH-11 from TP-1250-02 to TP-1310-04, correct=True, confidence=text
- single agent, CMMS down (20261023-092358-baseline-cmms-down): move LH-11 from TP-1250-02 to TP-1310-04, correct=True, confidence=text
- single agent, CMMS down (20261023-092726-baseline-cmms-down): move LH-11 from TP-1250-02 to TP-1310-04, correct=True, confidence=text
