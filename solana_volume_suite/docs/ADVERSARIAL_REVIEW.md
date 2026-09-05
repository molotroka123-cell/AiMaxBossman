# Local adversarial review, 2026-09-05

Scope: virtual control plane and defensive accounting. No mainnet calls,
real keys, real transactions or attacks on external systems.

Confirmed and fixed:

- **High, if connected to funds:** TreasuryGuard omitted Jito/network costs from
  budget authorization, allowed an exactly exhausted budget, accepted negative
  and nonfinite costs, and could clear the breaker while still over budget.
  Full recorded burn now governs a latched stop. Invalid inputs/reset attempts
  fail before mutation. Recording a cost remains accounting, not trade approval.
- **High control-plane correctness:** concurrent Start could report RUNNING while
  Stop was cancelling the task. Start/Stop/reset/sweep now share a lifecycle lock.
  A stop invalidates earlier in-flight start requests; a fresh request is required.
- **High availability:** Kill Switch waited for an uncompleted request body.
  Stop now runs independently of the body. Other authenticated requests have a
  three-second total read deadline and an 8 KiB limit.
- **High safety invariant:** direct Jito compilation still produced signed
  transactions despite the virtual-only policy. Signing now rejects all callers
  before processing key material. Existing submission blocking remains in place.
- **Defense in depth:** conflicting Authorization headers are rejected; only
  local Host and same-origin browser requests are accepted. This does not imply
  an unauthenticated token bypass was demonstrated.
- **Defense in depth:** JSON nesting is bounded before route parsing, and open
  WebSocket sessions revalidate their token before subsequent telemetry sends.
  The original deep-JSON probe returned 403; a crash was not demonstrated.

Evidence: tests/test_adversarial_security.py contains the reproductions and
regressions. The first run failed 19 security assertions, including parameterized
cases; these are not 19 distinct vulnerabilities. Tests use mock keys, in-process
ASGI calls and bounded scheduling barriers. Additional tests cover stale starts
and body deadlines. Updated legacy tests assert signing is blocked.

This is not a real-money certification or an exhaustive penetration test.
No live execution adapter exists. Historical strategy helpers and dependency
vulnerabilities have not received a complete audit. The loss ledger remains
in-memory and is not a durable financial accounting system.
