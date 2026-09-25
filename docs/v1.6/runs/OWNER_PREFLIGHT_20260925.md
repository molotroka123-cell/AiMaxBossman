# Bossman 1.5 → 1.6 owner preflight — 2026-09-25

Status: **PARTIAL / NOT FROZEN**. This is an evidence checkpoint, not live acceptance. No 1.6 integration branch or Windows release artifact has been certified.

## Source identity

- Current 1.5 candidate: `fa594b7ccd2cd1077c0ef94281f8c8b001bf41b2` (pushed; **not** `V15_FINAL_SHA`).
- 1.6 self-evolution source: `e574b497487f3f887374992fa013072b39f84acb`.
- BossNet source before this report: `dfc8b3fb35e1e691199df358b2128bda496cf541` (pushed).
- Historical 1.5 RC2 is not an integration base. A dry `merge-tree` found no text conflict for 1.5 + self-evolution; 1.5 + BossNet has six conflict files: `README.md`, `docs/v1.5/README.md`, `tests/test_distill_recorder.py`, `tools/bossman_15_economy_run.py`, `tools/distill_recorder.py`, `tools/worker_client.py`. No merge was performed.

## Executable evidence

| Surface | Observed result | Limit |
| --- | --- | --- |
| 1.5 root `pytest -q tests` | 2691 passed, 10 skipped, exact 1.5 candidate SHA | Skips and installed UX remain release gates. |
| 1.5 autonomy/economy targeted | 31 passed | Does not prove live self-improvement. |
| 1.5 owner CMD | Backend reports exact source identity; models, agents, skills, tools, tasks and approvals read from one backend, 6/6 successful | Computer control unavailable; coding path not startable. |
| BossNet foundation targeted | 25 passed | Foundation contracts, not a distributed live run. |
| SearXNG setup tests | 16 passed | Live search is not configured. |
| Social Farm unit suite | 426 passed, 29 skipped | Windows vault ACL patch tested on real NTFS and pushed; independent model review remains unverified. |
| Self-evolution Jev tests | 71 passed | Live Jev route is unavailable; safe fallback only. |
| Self-evolution self-improvement tests | 98 passed, 1 failed, 1 skipped | Old branch fails STOP descendant PID accounting; current 1.5 candidate contains the tested correction. |

## Owner runs and learning

- **YOUTUBE-001: PARTIAL.** One real public stream was downloaded outside Git and transcribed locally; three separate Nemotron roles produced quarantined pilot material. No deterministic chart/outcome verification or unseen transfer; no trading.
- **INSTAGRAM-001: OWNER LIVE PENDING.** No login or external mutation. Social Farm security regression is green; web search and the business DM scope gate are not live-proven.
- **BOSSBLOCKS-001: NOT STARTED.** Game bootstrap and coding limit pretests passed (11/11); the four-hour clock was not started because the required 1.6 Bossman/Jev coding and owner-emulator route is not available.
- **Scientific self-improvement cycles:** 0 of minimum 3 completed; positive transfer: 0. A provider/model response or a test fixture is not a promoted lesson.

## Runtime, privacy, and cost

- `BOSSMAN_DATA_DIR` resolves outside the Git worktrees. A tracked-file scan found zero complete OpenRouter-token-pattern matches; full brain continuity and release artifact leak gates remain pending.
- The existing OpenRouter route returned HTTP 401 on a Bossman task. No credential is recorded here. Do not retry paid routes or put owner secrets in prompts or Git.
- Local Qwen3.8 completed one simple CMD task, but two independent ACL review attempts returned `EMPTY_RESULT`; verifier verdict is **UNVERIFIED**. `/no_think` produced a short real local response in a separate probe, without certifying the ACL review.
- Verified paid spend for this checkpoint: **USD 0**. No GLM or RunPod usage. `ASTER_CODE_WRITES=0`; release blocker fixes were Codex escalations because the Bossman coding path was unavailable.
- **Power incident:** Windows entered Modern Standby (Kernel-Power 506, `Idle Timeout`) at 04:11:28 and again at 04:25:41 local time despite ordinary sleep timers at zero and, on the second occasion, a live `ES_SYSTEM_REQUIRED` process. The first wake (507) was at 04:16:39 by mouse. The earlier claim that zero sleep timers alone kept this S0 machine awake was false.
- **Current mitigation, bounded observation:** a `BossmanKeepAwake` scheduled task starts at owner logon with no execution time limit; its hidden Python process issues continuous `ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED`, refreshes an external heartbeat, and is visible under both `SYSTEM` and `DISPLAY` in `powercfg /requests`. Display idle timeout is now zero on AC and battery to avoid the observed screen-off S0 trigger. After more than seven minutes, the task remained running, the heartbeat was fresh, no *new* Kernel-Power 506 event appeared, and owner CMD still reached the exact 1.5 backend. The previous S0 session began before this change; a full post-wake endurance cycle and long soak remain **UNVERIFIED**. The monitor can be switched off physically; Windows is configured to keep its display session active.
- Local-only operational ledger: `C:\Users\asd\AppData\Local\Bossman\owner-run\owner-ops-20260925.jsonl`. Telegram owner checkpoint 364 was delivered.

## Gates and next version order

Same-product Terminal Run contract: CMD, dashboard and Telegram must use the same backend/data, permissions and evidence. The CMD read-only paths are observed; cross-surface UX/Telegram acceptance is not complete.

North Star ladder: `SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT` is evidenced by code/tests only; `SELF_REPAIR_SINGLE_CYCLE_PASS`, `SELF_REPAIR_3_CYCLE_PASS`, `TRANSFER_MEASURED_GAIN`, `24H_SOAK_PASS`, `48H_SOAK_PASS`, `WEEK_MODE_READY`, and `REVENUE_CAPABLE_PILOT` are **not achieved**.

Required order remains: live-accept and freeze exact 1.5 SHA → converge and accept exact 1.6 with all three owner runs and three separate scientific cycles → consider 1.7 only after its independent GREEN. No old RC2 base, no fake PASS, no force push.
