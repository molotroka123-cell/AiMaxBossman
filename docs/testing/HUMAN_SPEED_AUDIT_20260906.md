# Human-speed test checkpoint — no runtime activation

Base: PR #25 head `e4a5a2311837df298dca114fd5c3b076f01ff989`.
Work is isolated in `test/astra-human-speed-gates-20260906`; the full UI run
and Fable/main/integration branches are not changed.

## Requested CI audit

Run `34033667511`, validation job `101487731001`:
159 root/V5 tests passed, 72 Core tests passed; no FAILED/ERROR/SKIPPED tests
were reported in either pytest summary. Secret scan passed. The separate
publish job also succeeded. Node deprecation notices are setup warnings,
not failed application tests. There is no failed-test/file/line list to invent.

This run checked a materialized SQLite patch above `b0b06fac`, then published
`fb4740d2`. It is not exact-HEAD evidence for the later `e4a5a231` changes.
The later durable V5 admission code is already in e4a5a231; the older PR body
calling it a future checkpoint must not cause duplicate implementation.

## Requirement clarification

PR #7's PRODUCT_EVOLUTION_CONTRACT specifies visible click/key acknowledgment
p95 <=100 ms; local controller invalidation p95 <=50 ms and p99 <=100 ms.
It does NOT state a universal 50-ms human perception constant. A controller
microbenchmark cannot prove human-level computer use or an OS stop guarantee.
CAS <10 ms and recovery <2 s are additional owner-requested test targets here.

## New coverage

- `tests/test_v5_human_speed.py`: actual file-backed CAS with stale-write denial,
  deterministic real-file observation with sleep prohibited, five genuine child
  process crashes and committed-state rollback/reopen; strict timing assertions.
- `bossman-core/tests/test_v4_human_reaction.py`: measured interrupt/poll behavior
  on the canonical controller, without OS input or external effect dispatch.
- `tools/human_speed_gate.py`: finite samples, no trimmed outliers, strict bounds;
  visible UI traces require exact code SHA, clean source, five sessions with
  at least 100 acknowledged trusted click/key events each and trace digests.
  Missing ACKs, wrong tier, stale SHA and malformed clocks do not pass.
- Read-only CI on Linux/Windows Python 3.12 stores source identity, raw samples,
  JUnit and explicit INSUFFICIENT_EVIDENCE for the unavailable UI trace.

No placeholder sleep was removed from production: the existing deterministic
observer has no such call. Its new regression prevents introducing one.
No policy, approval, coverage threshold, N0 flag or production source is changed.

## Local diagnostic evidence, not exact-HEAD certification

Python 3.13.5/Linux. The initial archive-based CAS run before applying PR #25's
known connection-close correction hit max 14.595735 ms and failed the new
strict 10-ms gate. That failed run is retained, not silently discarded.
After reproducing the already-published connection-lifetime correction locally:
19 tests passed; CAS max 0.936662 ms/100 writes, observation max 0.146781 ms,
store crash-restart max 760.124532 ms/5 trials. A malformed-type guard test was
subsequently added and needs its final CI result. These are diagnostic source
checks, not timings attributed to an untested new remote commit.

The new controller test plus existing visual/reaction suites: 72 passed, exit 0;
controller p95 0.002494 ms, p99 0.004166 ms/100 local calls. These exclude the OS
hook, browser delivery, screenshots, model delay and network. No human comparison
was run and no product-wide speed multiplier is claimed.

## Remaining N0 / latency boundaries

| Gap | N0 relation | Latency path |
|---|---|---|
| Missing target-host UI trace and actual OS-hook qualification | Acceptance evidence still missing; no activation | Direct |
| Store-only restart is not whole-app Recovery Kernel qualification | Full crash/recovery acceptance still needed | Direct |
| Local timings are not matched human/Bossman task runs | Human-level claim unverified | End-to-end |
| Exact final-SHA full CI, live model preservation and UI run | Separate acceptance gates | Mixed |

Component PASS never authorizes N0. External input JSON validates data shape,
not the honesty of its producer; retain trusted trace artifacts and run identity.
The frozen UI run stays on its own SHA. Retest affected interactions only after
controlled integration, never while the owner is measuring a different tree.
