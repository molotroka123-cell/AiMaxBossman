# Web Designer release recovery

Baseline: `c7e75cc3c0a636d61f6fc8fb3e476a210d1320d3` (PR #61).
This is subsystem evidence, not a release verdict or final-SHA acceptance.

| Finding | Severity | Reproduction and correction | Regression |
|---|---|---|---|
| MF-012 | P2 | A save/edit error only produced a temporary toast; failure of the recovery GET could hide the initial failure. Persistent error panel now retains precise HTTP/detail text and separately reports refresh success/failure. It offers a one-click retry only for a transient code-save failure whose original version and code are still current. It never offers mutation replay for a policy refusal, stale element, oversized input, or model call. | `test_web_designer_recovery_ui.py`; autosave Node suite |
| WD-R01 | P1 | `flushSave` caught failure and returned no outcome. Apply, model edit, generation, restore and project navigation continued after that failure. They now abort unless the draft was fully saved. | Node `ошибка сохранения не разрешает зависимую правку элемента`; browser conflict/generation case |
| WD-R02 | P1 | After a 409, silently adopting the new server version meant the next keystroke overwrote another tab's work. The version is refreshed, both texts remain available, autosave pauses and replacement requires an explicit comparison/confirmation. | Node 409 case; browser conflict/comparison case |
| WD-R03 | P2 | Two concurrent saves could send the same mutation twice; text typed while a slow save was in flight could miss the only remaining save timer. Saves now share an in-flight promise and schedule another save for newer text. A lost response followed by a read of the exact submitted HTML does not cause another write. | Node concurrent-save, in-flight typing and lost-response cases |
| WD-R04 | P1 | Generation accepted an old `base_version` and overwrote a newer tab; repeating the request made another version. Generation now carries and checks its captured version under the project lock. | `test_web_designer_release_regressions.py` generation cases |
| WD-R05 | P2 | Project delete used `rmtree(ignore_errors=True)` and returned success even when the files remained. Removal failure is now HTTP 409; success requires the directory to be absent. | `test_a_locked_project_is_not_reported_as_deleted` |

Validation on the implementation tree:

- Node autosave/recovery suite: **17 passed**, zero skips.
- Focused Web Designer API, parser/safety, model-choice and release suites: **83 passed**.
- JavaScript syntax, Python compile and `git diff --check`: passed.
- Negative control on unmodified baseline: the stronger Node suite fails 10 of 17 cases; both generation controls return erroneous HTTP 200 rather than 409. These failures show actual overwritten code / duplicated mutation where relevant, not merely missing UI strings.
- Chromium acceptance: **OWNER_LIVE_REQUIRED / environment blocked**, not PASS. `BCC_REQUIRE_BROWSER=1 ... pytest -x command-center/tests/test_web_designer_recovery_ui.py` fails at launch because the Chrome executable is absent. One standard `python -m playwright install chromium` attempt exhausted its CDN download attempts with 30-second timeouts and HTTP 502 / `Connection refused`. No alternate download route or approval bypass was used.

The added browser suite operates real BCC project creation, save, selection, Apply twice, model-choice persistence and app restart. Only transport-error responses are deliberately injected for the failure-path cases; the suite does not claim a live provider/model run. Command Center CI already installs Chromium; the suite is required when `BCC_REQUIRE_BROWSER=1`. The Node recovery suite is now also an explicit Command Center CI check.

Run browser acceptance once Chromium is available:

```sh
BCC_REQUIRE_BROWSER=1 PYTHONPATH=.:bossman-core:command-center python -m pytest -q command-center/tests/test_web_designer_recovery_ui.py command-center/tests/test_web_designer_apply_idempotent_ui.py
```

This source-tree harness is not clean installed-package evidence. Final packaged owner acceptance and final CI results must be recorded on the convergence SHA by the release owner.

Test correction: the old 409 Node test explicitly expected silent overwrite of the refreshed remote version. That expectation contradicted the owner's no-data-loss requirement. It now requires preservation of both texts and blocks implicit overwrite, with a real-browser explicit replacement case added. The existing autosave/edit request-order assertion now includes the registry-model GET introduced by the already-present MF-011 fix; it still checks the entire exact sequence.

## Independent storage follow-up

Further investigation after MF-012 found three release-blocking storage defects:

| Finding | Severity | Negative control | Correction and positive control |
|---|---|---|---|
| WD-R06 | P1 | A numeric project-directory symlink, linked `current.html`, linked metadata or linked history exposed external data or admitted external writes. All four real-filesystem controls failed on the baseline. | Validate project/root/file components for symlink or Windows reparse/junction before I/O; refuse the operation and omit unsafe entries from project listings. Four controls prove external files remain byte-identical and their contents absent from responses. |
| WD-R07 | P1 | Two actual Python processes read version 1 and both returned success for version 2. The old in-process lock did not protect filesystem compare-and-swap. Concurrent project creation also allocated the same ID twice. | Reuse `bossman_shared.fable_budget._CrossProcessFileLock` (POSIX `flock`, Windows `msvcrt`) for full project mutations and catalog allocation. Acquire off the event loop; a cancelled waiter cleans up a late-acquired lock. Two-process controls now require one commit/one 409 and two distinct allocated projects. A weak in-process lock registry does not retain every historical app root. |
| WD-R08 | P1 | Injecting failure after `current.html` installation but before metadata commit exposed new HTML with the old version. The existing crash control only failed the first replace. | Metadata remains the commit pointer and readers use its already-written history snapshot. GET and model-edit input bind code and version to the same metadata read. The crash control now observes the previous committed code/version, then succeeds with a fresh guarded write. Existing save/history/model regressions are retained. |

Storage-boundary suite: **8 passed** on Linux, including real two-process races and lock cancellation. Windows junction/locking behavior is implemented and executable in the same suite, but still **OWNER_LIVE_REQUIRED** until Windows CI/host execution. No Windows proof is inferred from the Linux result.

These component checks reject planted project links; they do not claim to sandbox a privileged process concurrently rewriting BCC's own data directory outside the API. The release's filesystem authorization boundary must keep untrusted agents from mutating that storage directly.

WD-R09 (P1): a pending save or restore validated project existence, then waited for its lock. If DELETE won that lock, the stale request recreated the deleted project and returned HTTP 200. Two HTTP negative controls reproduced this on `bef627d6fc58a78fa825d0b5ec6412d8fd9b5181`. Every existing-project mutation now revalidates existence under the acquired lock at effect time. Both controls require HTTP 404 and an absent project directory. The resulting focused suite passed **93 tests**; the storage suite now contains 10 controls.
