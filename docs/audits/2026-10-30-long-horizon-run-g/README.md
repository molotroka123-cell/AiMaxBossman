# Long-horizon run lh-20261030-g — receipt

Source = runtime SHA `61f6b0a870a5f91666d1113a65e81fa5b9c86d45` (identity PASS at start and after the forced restart).
Driver `tools/long_horizon_run.py`, agent 52 (local Gemma 4 31B, ctx 32K, Ollama), cost $0.00, 12:45:19–13:26:22 UTC (41 min).
Task ids 805–809, 30 tool calls (21 executed), tokens in 211 066 / out 11 870.

| Check | Result | Evidence |
|---|---|---|
| 5 steps, each verified by independent code against planted truth (`truth.json`, outside agent roots) | PASS | state.json `steps.*.verified` |
| Checkpoint after each step (task id, SHA, result sha256, next step) | PASS | state.json `steps.*.checkpoint` |
| Real interruption: :8801 killed mid step 3 after 2 completed calls, same SHA back, same task 807 finished | PASS | driver.log `interrupt_kill_backend`, `identity` |
| No completed tool call executed twice | PASS (0) | result `duplicated_completed_calls` |
| STOP in step 4: zero new tool calls in the 45 s after STOP; then owner-style retry finished the step | PASS (0) | `stop_check` |
| Memory recall event in step 5 | PARTIAL: 1 `memory.recalled` event; relevance of recalled facts NOT verified | `memory` |
| Final report answers the task | PARTIAL: numbers verified (C-0042 15.9 %, P-07 −207 530.4), but the «Север» −32.1 % finding is missing from REPORT.md | agent_REPORT.md |
| 2–4 hour duration | NOT MET: 41 min | timestamps |
| 24 h soak | NOT_RUN | — |

Approvals were given by the test harness (`by=harness:long_horizon(test-folder)`), only for terminal calls inside the run
folder, via bounded leases (max 40 uses, 30 min); 2 calls refused by the harness guard (false positive on `f"...\n"`).

Bossman defects found by runs a–f and fixed before this run: stacked memory reservations (f16adfbc), review read bare
names in roots[0]=Python312\Lib — the owner's 14:10 finalize_refused→failed→deadlock chain (1f46bfd7), STOP/fail did not
release memory (c4ec83e5), resident model reserved twice (d1411222), failed attempt + pre-existing file / output
markers (928fa152, 61f6b0a8), `cmd /C` hung under Git-bash (e9ad41d2); agent model `:latest` had num_ctx 8192 (agent 52 → ctx32k).
