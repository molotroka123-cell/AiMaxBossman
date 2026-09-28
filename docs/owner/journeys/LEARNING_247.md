# Supervised 24/7 learning mode (no Claude)

This is a supervised mode with a readiness gate, not a blind switch. Nothing is installed
automatically. The owner or the lead enables it only after the gate prints
`LEARNING_247=READY`.

## What one cycle does

`tools/owner_journeys/learning_supervisor.py` runs one bounded cycle at a time, rotating
through three kinds:

| kind | task | verifier |
|---|---|---|
| `triage` | one held-out admin-triage item (fake, no PII) as a real bcc task | deterministic exact match and the safety rule |
| `journey` | one SwapMe or Fresh Vibes journey, fake data, run through bcc with tool policy and approvals | the journey's deterministic step checks |
| `k1m6a_verify` | re-verify stored K1m6a claims against cached exchange data (no LLM) | recomputation must match |

A failure produces a **quarantined** lesson candidate in `lesson_candidates.jsonl`, with status
`CANDIDATE_QUARANTINED`. Nothing is ever promoted automatically. The loop never grants Computer
Use, send, post, transfer or permission tools, and never changes permissions. A policy
violation is recorded and blocks readiness.

## Route ladder (Claude-free), cheapest first

The config lives in `<state_dir>\ladder.json`, which is created with defaults on the first run.

| tier | models | rule |
|---|---|---|
| 0 deterministic | no LLM | used for `k1m6a_verify` |
| 1 local | `bossman-fast-qwen36-35b-a3b-q5`, then `bossman-main-qwen38-27b-q5` | thinking off; the default for every LLM cycle |
| 2 free_cloud | OpenRouter `qwen/qwen3.8-27b:free`, `nvidia/nemotron-3-super-120b-a12b:free`, `google/gemma-4-31b-it:free`, `nvidia/nemotron-3-ultra-550b-a55b:free` | used only if the price is verified as 0/0 **live** from the OpenRouter catalog at plan time; only when local could not serve |
| 3 max_cloud | `z-ai/glm-5.3-flash` (live price must be ≤ $0.15 in / $0.50 out per 1M tokens) | only after a free tier failed; **hard cap** reserved before each call (see below) |

**The hard cap.** A durable reservation in `cloud_cap.json` is taken **before** each call. It
covers the worst case: `max_tokens` times the output price, plus the input price. The defaults
are `daily_cap_usd` 0.50 and `per_cycle_cap_usd` 0.05. When the cap or a missing key blocks a
tier, that tier is skipped and the loop continues locally.

To change the cap, edit `ladder.json`, for example `"daily_cap_usd": 1.0`, and restart the
supervisor. To disable the paid tier entirely, set `"allow_max_cloud": false`.

**Keys.** The key is read from the `OPENROUTER_API_KEY` environment variable or from the
`key_file` set in `ladder.json`. It is never logged. A cloud cycle's bcc data dir, which holds
the encrypted provider row, is deleted after the cycle. Reading the key directly from the
owner's Bossman vault is **not wired yet** (see the backlog), so on the owner machine point
`key_file` at the owner's own provider key. The 24-hour $1 test key is not required.

**No Anthropic.** No tier is a Claude or Anthropic model, and the config refuses Anthropic
hosts and model ids. At start the supervisor removes `ANTHROPIC_*` from its environment and
installs an in-process audit hook that blocks **and counts** any DNS lookup or connection to
Anthropic. Every cycle records `anthropic_attempts_total`, and readiness needs at least 12
cycles with zero attempts.

**Privacy.** Cloud cycles run only on fake, privacy-safe tasks (task meta `privacy=public`).
Local cycles run as `privacy=private`, and bcc's provider egress guard blocks cloud in that
case. Every cloud task also carries bcc's `cloud_allowed=true` and a positive
`cloud_budget_usd`, as the product's `cloud_policy` requires.

## Guards

| guard | behaviour |
|---|---|
| PAUSE | `C:\Users\asd\Bossman\rc19-owner-test.PAUSE` or `<state>\PAUSE` pauses the loop; the PAUSE file is also checked before every model call |
| owner busy | a non-allowed model resident in Ollama, Ollama unreachable, or an owner GPU job (`sd-cli.exe`) running: LLM cycles wait, model-free cycles (`k1m6a_verify`) continue; recorded as `owner_busy` / `owner_free` events |
| memory | wait while free memory is below 12 GB |
| STOP | `<state>\STOP` aborts the running cycle and exits (the start script then refuses to start until `-Resume`) |
| owner STOP | the owner's durable Bossman STOP file `<owner-data-root>\computer\STOP` (STOP button, /stop or /pause in the пульт) aborts the running cycle and holds (`HALTED_BY_OWNER`) until «Продолжить» removes it; a STOP file older than the state dir's first run is not a learning decision and is ignored (`owner_stop_baseline` in `state.json`) |
| budget | per UTC day: cycles 400, model calls 3000, wall time 20 h; cloud USD per the ladder cap |
| restart safety | PID lock; atomic `state.json`; fsync'd append-only `cycles.jsonl`; a killed cycle is recorded once as `ABANDONED_ON_RESTART` |
| priority | the process runs at BELOW_NORMAL |

## Readiness gate

```powershell
$env:PYTHONPATH="<repo>\command-center;<repo>\bossman-core;<repo>"
python tools\owner_journeys\learning_247_readiness.py --state-dir <state_dir>             # read-only verdict
python tools\owner_journeys\learning_247_readiness.py live-tests --state-dir <test_dir> --key-file <env-file>
```

`READY` requires every one of these criteria:
- at least 12 consecutive cycles without a crash;
- at least 1.0 h of unattended time;
- zero policy violations and zero cycle errors;
- no duplicate or corrupt state;
- no promotion without an owner-approved Bossman approval bound to the lesson digest;
- journey safety checks pass;
- triage pass rate at least the lab baseline minus 0.10;
- pause honored within 120 s, STOP within 60 s, and kill/restart recovered exactly once;
- at least 12 Claude-free cycles with 0 Anthropic attempts;
- ladder tier recorded for every cycle;
- cloud spend within the daily cap;
- the cap fake-test passes, plus one real tiny `:free` call served by `free_cloud` at $0;
- peak RSS under 4 GB.

The measured numbers for this run are in `C:\Users\asd\Bossman\evidence\rc19\d\learning\`
(`readiness.json`, `live-tests.log`) and in the workstream-D report.

## Lessons, owner approval, goal reports

- `lesson_pipeline.py`: quarantined triage failures become lesson candidates
  (`<state>\lessons\registry.json`, history in `history.jsonl`); every `ab.every_cycles` cycles a
  `lesson_ab` cycle runs an offline A/B (local model, $0) on the held-out set minus the lesson's own
  item; `GAIN_PROVEN` needs a gain of `ab.min_gain_items` in every repeat and no new safety
  violation. Then a Bossman approval (`kind=learning_lesson_promotion`, local core only) is created;
  the lesson is promoted only when that row is `approved` with `decided_by` and the same digest.
  `lesson_pipeline.py rollback <id>` removes it. Other kinds stay quarantined (`no_ab_harness`).
- `goal_reporter.py` + `owner_notify.py`: `<state>\goal.json` (goal, targets, cadence) and Russian
  reports to the companion bot («Пульт») only: every 6 h and on readiness flip, STOP, cap reached,
  error streak, lesson approval/decision. Durable outbox `<state>\notify\outbox.json`, secret
  refusal, dedup, min interval, max 12/day. `--report off` sends nothing (tests, live-tests).
- Entry point: `start_learning_247.ps1` (`-Foreground` for Task Scheduler, `-Status`, `-Pause`,
  `-Resume`, `-Stop`). Owner summary in Russian: `docs/owner/LEARNING_247.md`.

## Enable (provided, not installed)

```powershell
powershell -ExecutionPolicy Bypass -File tools\owner_journeys\learning_247_enable.ps1 -Check      # must print LEARNING_247=READY
powershell -ExecutionPolicy Bypass -File tools\owner_journeys\learning_247_enable.ps1 -Install    # refuses unless READY
powershell -ExecutionPolicy Bypass -File tools\owner_journeys\learning_247_enable.ps1 -Uninstall
```

The script registers a per-user Scheduled Task with these settings:
- starts at logon;
- priority 7 (below normal);
- one instance only;
- restarts 3 times on failure;
- runs with `--owner-data-root %LOCALAPPDATA%\Bossman\CommandCenter`, so the owner STOP button
  stops it.
