# Long-horizon run lh-20261101-022820 — receipt

Verdict: **FAIL** · mode `memory` · rounds passed 0 of 1 · elapsed 1.29 h · cap none h · agent 52 · driver v2

Reasons:
- round 1: step verify blocked: ["verify.json not all ok: ['worst_margin_product', 'top_customer', 'region_drop']"]

| Round | Seed | Planted (product / customer / region) | Steps verified | Backend killed | STOP: calls after | Dup calls | Report complete | Memory: record recalled / code word in report | Minutes | SHA | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 985999250 | P-05 / C-0959 / Восток | 3/5 | yes | 0 | 0 | — | — | — | 3b23ceb6 | FAIL |

24 h soak: **NOT_CLAIMED** — a 1.29 h run never claims 24H_SOAK; use --soak-hours 24

Files: `state.json` (checkpoints per round), `driver.log`, `r<k>/truth.json` (planted truth + seed + memory-only auditor code word), `r<k>/agent_REPORT.md`, `r<k>/agent_verify.json`.

Memory check: the driver writes the verified findings plus «аудитор: <code word>» to Bossman memory; the code word is in no file the agent can read and not in any prompt. Negative control: `python tools/long_horizon_run.py start --rounds 1 --no-memory-control` — the code word must be ABSENT.
