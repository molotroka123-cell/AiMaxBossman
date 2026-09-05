# Five-agent bounded defensive audit — 2026-09-05

Owner-requested attack window: approximately 17:54–17:59 UTC (five minutes).
Critical remediation and delivery continue after that window; this is not a
five-minute full-product attestation. All activity targeted authorized local
repository fixtures. No mainnet transaction, funding, trade, Jito submission,
external network attack, or paid model inference occurred.

## Source and independent evidence

The window began on `6b11330948370f9b9dee93da45748c14f89904ef` and integrated
execution-truth fixes at `1b953a8cd663d1408a88abebb88cd8d8a80abb08`.
The source SHA in each row is authoritative; these overlapping selections must
not be added together as a unique-test total or attributed to a later commit.

| Independent specialist | Source | Actual result | Evidence and limitation |
| --- | --- | --- | --- |
| 03 Security | 6b11330 | 14 passed, 1 failed | `test_gateway_loopback_proxy.py` and root `test_ci_secret_scan.py`; ten proxy/auth cases pass. Repository scanner flags two public sample mint addresses in `setup_mainnet.py`, not demonstrated secrets. |
| 04 Fleet/recovery | 6b11330 | 34 + 6 + 4 passed, overlapping areas | Fleet core/E2E/safety/recovery, Astra `f00/unified/fleet/backoff/queue/zombie/lease`, and FL01 fencing. First combined invocation had four async-fixture errors; corrected Command Center invocation passed. |
| 06 Memory/context | 6b11330 | 21 passed, 59 deselected | Astra journal confinement (10), serialized budget (6), authoritative scope ownership (1), unified admission (1), invalid GPU resources (3). Selection `o007 or astra009 or astra010 or astra007 or memory or context`. |
| 07 Windows | 1b953a8 | 7 passed, 2 failed | `test_v2_poststate_verifiers.py`: local Git observation returned UNVERIFIED instead of expected FAILED; disagreement fixture assumes a POSIX second source absent on Windows. No production bypass reproduced by these two failures. |
| 10 Independent finalizer reviewer | 1b953a8 | 14 passed | `test_mission010_execution_truth.py`; failed mutations, missing postconditions, invalid human override and failed/stopped children are rejected. Two SQLite cleanup warnings remain. |

Commands used Python 3.12 on Windows with `PYTHONUTF8=1`, explicit checkout
`PYTHONPATH`, and Command Center `asyncio_mode=auto` where required. No missing
test, failed collection, timeout, skipped hardware, or unknown state counts as a
pass. Agent 03's initial broad task was rejected by automatic risk review; its
completed task was restricted to existing local defensive unit tests.

## Confirmed fixes integrated before delivery

- **P1 missing executor:** mission-console creation omitted `agent_id`; backend
  previously created a queued run unconditionally. Admission now records
  `BLOCKED_CAPABILITY_UNAVAILABLE` without creating a run. Six fresh regression
  cases pass. UI correction is a separate tested batch.
- **P1 false completion:** finalizer now rejects failed or unobserved effectful
  calls, malformed required effects, missing postconditions, pending execution
  after approval, and mismatched task/run identity. Human review cannot waive
  required world-state evidence. Mission aggregation cannot complete a parent
  with a failed/stopped required child.
- The implementing specialist reproduced nine failures against historical
  functions. Lead integration independently ran admission + finalizer + new truth
  tests: **24 passed**, five SQLite cleanup warnings. Independent agent 10 ran the
  new truth selection separately: **14 passed**. Neither is full regression.

## Critical vault safety follow-up

Source inspection found two dangerous defaults in the mainnet setup wizard:
a public hardcoded fallback password and deletion/recreation of an existing vault
when unlocking raises an error. The latter can destroy existing key material
after a wrong password. These are defensive configuration/data-preservation
issues; no real wallet was created or accessed by this audit.

Remediation requires explicit sufficiently long credentials before any probe,
hidden interactive entry, and fail-closed unlock errors that preserve every byte
of an existing vault. Delivery includes the focused regression result in the
adjacent mission evidence; no simulation is described as financial acceptance.

Implemented in `788b24b`: the lead independently reran all six vault safety tests,
which passed. A post-commit scanner caught a synthetic test password missed by the
agent's pre-staging scan (the scanner sees tracked files only). A precise fixture
annotation was added and the staged repository rescanned before delivery.

## Remaining limits

- Same-kind unrelated postcondition binding in the V2 finalizer remains an open
  review target: matching verifier kind alone does not prove action-target or
  expected-value identity. This concern was inspected, not dynamically closed.
- Seven legacy assertions expecting unsupported action completion and one
  Windows execution timeout were reported by the implementing specialist. They
  require reconciliation before a green broad-product claim.
- SQLite cancellation/closed-event-loop cleanup warnings need investigation.
- Windows targeted acceptance is PARTIAL/FAIL until corrected and rerun.
- Authenticated remote fleet transport, actual sandbox hardware, provider
  hot-swap and controlled model comparison were not attested.
- Final-candidate exact-SHA CI is pending; an earlier green workflow is not proof
  for this report's delivery commit. No unattended/autonomous readiness is claimed.

Main mission `BOSS-FINAL-REALITY-CLOSURE-010` continues with benchmark and UX work.
