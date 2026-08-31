# REAL HARDWARE — FINAL ACCEPTANCE CHECKLIST

Executable checklist for the day the owner's real computer arrives. Everything
here is currently `SKIP_HOST`/`NOT_TESTED_LIVE` — this document does not claim
any of it passed; it tells the next session exactly what to run and what PASS
looks like. Do not fake-green any row: if a step cannot run, mark
`SKIP_HOST`/`SKIP_EXTERNAL_SERVICE`/`SKIP_EXTERNAL_CREDENTIAL` with the reason.

## Before starting
- `git fetch origin claude/bossman-control-v03-43igbk && git status` — confirm
  clean tree, local == remote SHA.
- Read `docs/context/CURRENT_STATE.md` and `docs/context/FINAL_CONNECTIVITY_MATRIX.md`
  for what is already proven vs. what this checklist still needs.

## A. Ollama → Gateway → Planner → Stage13 → Windows → Notepad → fresh observation
1. Start Ollama locally; confirm `bossman-fast` (or configured alias) resolves.
2. `BOSSMAN_GATEWAY_URL` pointed at a running Stage 3 Gateway; `cloud_policy=never`
   for the computer-operator planner (already hardcoded — verify, don't change).
3. Create a Stage 13 task with a Notepad goal; watch it go through
   OBSERVING → PLANNING → (policy) → RUNNING → OBSERVING → verified.
4. Confirm `test_live_notepad_actually_launches` and
   `test_windows_foreground_uses_os_foreground_handle_not_get_active`
   (`bossman-core/tests/test_stage13_wiring_notepad.py`,
   `test_stage13_windows_adapter.py`) go from SKIP to PASS on this host.
5. PASS bar: task completes with `verified=True`, no capability-lying, fresh
   observation used for the verify step (not a stale cached one).

## B. CLOUD_CALLS=0 for local-only path
- With `cloud_policy=never` set on the relevant agent, run a full task and
  grep the Gateway/LiteLLM access log for any request leaving the local
  network. PASS bar: literally zero.

## C. Browser live
- Run the toolkit/browser.py and bcc browser-control test suites against a
  real Chromium on this host (not headless-only CI stub). Confirm
  `confirm_default=True` mutating actions still route through approval.

## D. Live providers
- Configure at least one real cloud provider key server-side (never in
  client/browser). Confirm `SKIP_EXTERNAL_CREDENTIAL` tests in both suites
  flip to PASS. Confirm cost is tracked by the Cost Governor, not silently
  uncounted.

## E. Plugins with available credentials
- For each of the 13 `plugin:<id>.<cap>` capabilities in command-center,
  supply real credentials where applicable and confirm `SKIP_EXTERNAL_CREDENTIAL`
  flips to PASS (SQL read, HTTP GET with SSRF guard, etc.).

## F. Restart → restore
- Kill the core process mid-task (SIGKILL, not graceful). On restart, confirm
  `mark_interrupted()` marks the task `interrupted` and is visible in the UI,
  and that `working_memory`/`decision_memory`/`failure_memory` for that task
  are intact (durable, not lost) — this is now directly testable since
  `runner.py` writes them on every task (see `FINAL_CONNECTIVITY_MATRIX.md`).

## G. Low-memory mode
- Set `BOSSMAN_LOW_MEMORY=1`, confirm Guardian's `low_memory_budget` path
  activates and P0/P1/protected context is never evicted even under pressure.

## H. Context Guardian: RAW vs GUARDED IntelligenceRetention
Run the SAME task set twice: once with context assembly unguarded (raw
history), once through the existing `apply_context_engine`/`compact_session`
path. Compute:

```
IntelligenceRetention = VerifiedSuccess_guarded / VerifiedSuccess_raw
```

**Production gate: absolute verified-success degradation ≤ 1 percentage point.**
Do not run this against `bossman_v3`'s Context/Data Guardian — that module is
still `UNWIRED` (see connectivity matrix); this row is about the
already-`WORK` context_engine path only, unless/until Data Guardian gets
wired with its own acceptance pass.

## I. Direct Model vs Bossman (AAF)
Run the same task set directly against the model (no Bossman scaffolding)
and through Bossman (policy/approval/memory/verification). Compute:

```
AAF = VerifiedUtility(Model+Bossman) / VerifiedUtility(Model Direct)
```

Report the number measured — do not estimate or round up. A number below 1.0
is a valid, honest result and should be reported as such, not hidden.

## J. Bossman vs OpenCode
Same task set, same model, through OpenCode vs through Bossman's canonical
cycle. Report verified-success rate, cost, and latency for both — this is a
comparison, not a pass/fail gate.

## K. CyberSec enable under observation
1. Set `BOSSMAN_CYBERSEC_V1_ENABLED=1` in a non-production/staging run first.
2. Watch `cybersec.injection_detected` events on the EventBus during normal
   agent operation (the firewall is now wired into `runner.py._call_tool` —
   see connectivity matrix). Confirm zero false-positive rate degrades task
   completion meaningfully before enabling in the actual owner deployment.
3. This is observation-only — it does NOT include the red-vs-blue stress lab
   (see L).

## L. RED vs BLUE stress lab — separate, later gate
Do **NOT** run this as part of hardware acceptance. It is a distinct,
subsequent gate that requires:
- `BOSSMAN_CYBERSEC_V1_ENABLED=1` **and** `BOSSMAN_CYBER_LAB_ENABLED=1` **and**
  `BOSSMAN_CYBER_LAB_ACK=I_UNDERSTAND_THIS_IS_A_SANDBOX`.
- `SandboxFacts(is_disposable=True, production_secrets_mounted=False,
  production_network_allowed=False)` — a genuinely disposable sandbox, not the
  owner's real machine.
- RED side = Fable via OpenCode, but only through the lab's typed
  `AttackIntent` scenario adapter — never raw shell, never production
  credentials, never production network.
- Full contract: `docs/security/FUTURE_RED_BLUE_STRESS_TEST.md`.

## Reporting format for whoever runs this
For every row: `PASS` with the exact command/evidence, or an honest skip
label with the reason. No row may claim `PASS` without a command that was
actually run on real hardware in this pass.
