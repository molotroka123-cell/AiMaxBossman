# Command Center CI83 fixture reconciliation

Input: `a037302c0f72ff9e64cb59ca7aa7cc094aa0ed34`. Original GitHub run
34291958202 jobs 102280222098 (Python 3.14), 102280222167 (3.11),
102280222263 (3.12) each completed with seven failures, 3001 passed and
20 skipped. Coverage was respectively 82%, 78%, 78%, above the unchanged
72% gate. Pytest completed in 1373.65, 1707.76 and 1392.72 seconds; these
were assertion failures, not timeouts or presumed runner noise.

Five failures used obsolete fixture assumptions:

| Test | Demonstrated invalid assumption | Correction and retained control |
| --- | --- | --- |
| action preview schedule deletion | Scheduled task POST with no configured executor now correctly refuses before mutation. | Configure an actual provider/model/agent using `make_stack`, assert successful task creation, retain exact schedule-link deletion and preview assertions. |
| approved snapshot restore | Both runnable task POSTs without an agent were refused, leaving both IDs absent. | Use a configured agent for both original tasks, assert POST success, retain real database rollback, absent/present rows and safety-copy assertions. |
| external tool output | Test replaced `browser.read_dom` with a string-returning function and created no observable browser session. Run failed with `BROWSER_OBSERVATION_UNAVAILABLE`. | Use a fixture-specific external read tool. Retain completed read-only task and prompt-injection data-marker assertions, additionally verify the injection remains data and production browser tool is not replaced. |
| action gate dispatch row | Test inserted a bookkeeping row and asserted COMPLETED without observing any browser effect. Run correctly failed with `BROWSER_OBSERVATION_UNAVAILABLE`. | Assert original narrow gate returns NOT_APPLICABLE, then independent finalizer rejects completion with the exact missing-observation reason. Actual positive completion remains in real Chromium action-router test. |
| real browser action router | Fixture goal was `/watch`, but its HTTP site served `/watch.html`. Host/path security matching correctly rejects this sibling path. | Declare exact `/watch.html` goal and retain COMPLETED plus explicit VERIFIED history assertion. New runnable unit negative controls preserve wrong-path, wrong-port and query-string spoof refusal. |

The Fable revoked-approval fixture was already corrected in the input tree.
The seventh failure, File Commander browser status/reset race, is a product
issue investigated separately; it is not closed by these fixture changes.

No production completion, admission, authorization or URL verification logic
changes. No thresholds, skips or expected-output weakening. These are
legitimate test corrections: the old positive fixtures contradicted the
current security contract, and stronger independent observations remain
required. Unit fixtures are not live model or real browser proof.

Local focused run, including completion truth, F-012 independent verification
and executor admission: **91 passed, 2 skipped in 24.12 seconds**. Both skips
require real Chromium, which is absent locally. Real-browser acceptance
remains required in CI.

```bash
PYTHONPATH=.:bossman-core:command-center python -m pytest -c command-center/pyproject.toml \
  command-center/tests/test_action_gate.py command-center/tests/test_action_preview.py \
  command-center/tests/test_v21_snapshot.py command-center/tests/test_v21_tool_loop.py \
  command-center/tests/test_action_router.py command-center/tests/test_p0_completion_truth.py \
  command-center/tests/test_secrem_f012_verification.py command-center/tests/test_executor_admission.py -q
```
