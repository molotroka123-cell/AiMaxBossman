# Apps / File Commander release closure evidence

This is component evidence, not the final release verdict. The integration owner must rerun the gates against the single final convergence SHA and its installed artifacts.

Runtime/code SHA tested: `dcf07df4f4a309a865a215c52a603978a708d9eb`.
Input: PR #61, `c7e75cc3c0a636d61f6fc8fb3e476a210d1320d3`.
Component commits: `1eb3b444250a57d77510ab489fdd20510adc319f`, `6767ae3ed9aea10a330d18f9da99d28ed5dfbe48`, `be672d2e2f75c79665ea3b6959bd3b8df181e89f`, `dcf07df4f4a309a865a215c52a603978a708d9eb`.

## Observed owner flow

A real BCC subprocess, real session cookie/CSRF authorization and real File Commander subprocess completed:

- Initially denied start; explicit owner enable; start; repeated start retained one PID.
- Open the existing file-operation UI; scan an actual PDF-named file; preview without mutation; approve exact measured moves; independently read the destination bytes.
- Repeat Apply returned the same batch, with no second effect; denied workspace access returned an explained 403.
- Stop; repeat Stop; restart BCC; policy persisted; start File Commander; persisted batch/history reopened; Undo restored original bytes and names.
- Stop, disable, restart BCC; policy remained disabled and Start returned 403.

This is **PASS for actual HTTP + filesystem effects**. It is not browser acceptance, content-editing functionality, or clean-installed artifact evidence. The existing File Commander implements organization/rename moves; no new file-editor features were introduced.

## Findings and fixes

| ID | Severity | Reproduction / root cause | Fix / negative control | Verification |
| --- | --- | --- | --- | --- |
| AF-001 | P1 | Installed BCC searched `<source>/apps`, so the wheel had an empty catalogue. Launch depended on source metadata/code layout. | Source / explicit `BCC_APPS_DIR` / packaged `bcc/_apps` discovery; installed module command; writable child cwd and app data. | `test_wheel_catalogue_and_manual_command_do_not_need_checkout`; final packaged gate required. |
| AF-002 | P1 | Unset `FILE_COMMANDER_ROOTS` authorized the whole host. Launcher stripped an explicitly configured root. | No roots => NOT_CONFIGURED/refusal. Launcher grants only explicit roots or its own workspace. System roots, repositories, app state, secrets, traversal, symlink/junction/reparse paths and hardlinks refused. | `test_owner_safety.py`: real allowed/denied files, secret paths, repository, traversal and hardlink controls. |
| AF-003 | P1 | Standalone file APIs had no authentication. A localhost listener could supply arbitrary HTML and receive a bearer in the first convergence proxy. | Private standalone token; BCC sends only MAC-bound method/path/query/body/timestamp/nonce. Nonces are durable and TTL-bounded. Response status/body is MAC-bound to the nonce. No bearer or untrusted HTML is forwarded. | Real rogue localhost HTTP server: no bearer observed, no upstream HTML request, unsigned response => 502, exactly one request, no retry. Signed replay/changed-field negative controls. |
| AF-004 | P1 | Sequential moves wrote Undo only after the entire batch. A late failure left partial effects without recovery evidence. | Exact measured preview, whole-batch preflight, no-overwrite same-volume moves, write-ahead batch journal, independent post-state reads, rollback and explicit UNKNOWN on ambiguous evidence. | Actual first move followed by injected late error restores originals; actual child `os._exit(71)` after a file effect leaves a recoverable APPLYING journal; repeated Apply is idempotent. |
| AF-005 | P1 | Same-size/restored-mtime source replacement bypassed intent freshness. | SHA-256 plus size/device/inode bound to the persisted exact plan and checked again at effect time and after effect. | Same-size/restored-mtime replacement refused before mutation; replaced post-effect evidence cannot produce repeated success. |
| AF-006 | P2 | Owner app page still instructed an environment variable + restart while disabling Start. File Commander home was a static capability list. | Existing policy API exposed as explicit owner controls. Existing scan/organization/rename/Apply/Undo operations exposed in UI with busy states, errors, refresh and history. | Actual HTTP HTML/resource check PASS; real-browser gate exists but Chromium unavailable here. No browser PASS claimed. |
| AF-007 | P1 | BCC restart lost the process handle; Apps could never Stop a recovered application. PID alone was insufficient authority. | Persist process creation time/executable/full argv; guarded psutil recovery and signalling, rejecting PID reuse and mismatched process namespaces. Agent Stop passes the data directory. | Standard-host CI required. Current execution sandbox exposes virtual PIDs beside unrelated host `/proc`; recovery deliberately refuses. |
| AF-008 | P1 | Corrupt saved owner DENY fell back to environment `BOSSMAN_APPS_CONTROL_ENABLED=1`. | Unreadable/null/invalid owner setting fails closed; explicit owner policy replacement remains possible. | Before fix a real child started (HTTP 200); regression now 403 and no process. |
| AF-009 | P1 | Start accepted policy before waiting on a lock; a later DENY did not prevent the process effect. | HTTP and agent paths re-evaluate policy inside the process lock and immediately before spawn. | Before fix a real child started despite a newly saved DENY; regression now 403 and no process. |
| AF-010 | P1 | Directory creation could follow a replaced parent; crash after mkdir but before its receipt could be falsely described as full rollback. | POSIX no-follow parent handles; directory intent before effect and identity receipt afterward; verified empty-directory cleanup. Unreceipted existing directory remains UNKNOWN. | Real parent-directory/symlink swap creates nothing outside scope; lost directory receipt remains UNKNOWN and refuses further Apply. |
| AF-011 | P2 | Any reachable HTTP health response, including 404/empty/unhealthy payload, could be treated as ready. | Reachability and readiness separated; only a healthy successful response is LIVE. | 404, empty JSON, unhealthy and NOT_CONFIGURED negative controls. |

Security-related contract changes are intentional: File Commander requires explicit authorization; regular files only; cross-volume moves are refused rather than falling back to an unjournaled copy/delete. This is a scoped release safety constraint, not a claim that cross-volume operations were verified.

The original File Commander HTTP tests now explicitly authenticate; their original duplicate/preview/rule assertions remain. The process-restart test now requires successful Stop with independently bound process identity and retains cleanup of the original Popen handle. No test assertion was weakened and no new skip was added to suppress a product failure.

## Exact code SHA checks

On `dcf07df4f4a309a865a215c52a603978a708d9eb`:

- File Commander suite: **39 passed** (real filesystem effects; fault injections identified above).
- Apps + owner HTTP + rogue-service + browser collection: **84 passed, 2 failed, 2 skipped** in 21.40 seconds. The failures are the two process-recovery gates in this virtual-PID environment; neither is converted to a skip or PASS. Skips are absent Chromium and the pre-existing optional app with no launchable code.
- Chromium is unavailable. The browser gate is **OWNER_LIVE_REQUIRED**, not PASS. CI must install Chromium and require the browser.
- Installed wheel acceptance belongs to the parent integration run. Source HTTP evidence must not be relabeled installed evidence.

The two process gates are:

1. `test_apps_control.py::test_a_restart_does_not_turn_our_own_app_into_a_foreign_process`
2. `test_apps_release_runtime.py::test_reused_or_tampered_process_record_never_grants_stop`

A concrete environment observation: a subprocess and its parent agreed on virtual PID 8, but `psutil.Process(8).exe()` reported the unrelated `/usr/local/bin/sites-preview`; inspection of the parent's virtual PID reported NoSuchProcess. The implementation checks namespace consistency and declines recovered signalling here. Original Popen-handle stop remains usable and passed.

## Packaging requirement

The BCC wheel must include `bcc/_apps/file-commander-mini/{app.manifest.yaml,pyproject.toml,ui.html}`. The File Commander wheel includes its canonical `file_commander_mini/ui.html` via package data. BCC serves that reviewed UI resource with a script-hash CSP, never HTML returned by a port. The parent build hook must copy the canonical `apps/file-commander-mini/src/file_commander_mini/ui.html` into the BCC catalogue.

## Deterministic installed gate

From the final convergence checkout, with its built and installed bundle and a test interpreter that has pytest/httpx/Playwright:

```sh
BCC_ACCEPTANCE_PYTHON=/absolute/bundle/.venv/bin/python \
BCC_ACCEPTANCE_SOURCE_SHA=<FINAL_40_CHARACTER_SHA> \
PYTHONPATH=.:bossman-core:command-center \
python -m pytest -c command-center/pyproject.toml \
  command-center/tests/test_apps_files_http_owner.py \
  command-center/tests/test_apps_files_browser_owner.py -q --timeout=120
```

`EditorServer` is shared with the editor installed-acceptance suite: it verifies installed imports, packaged UI and `_build.json` source SHA. It starts the actual installed BCC and workers. The browser test uses normal UI login, explicit enable, Start, iframe controls, filesystem post-state, restart while the child remains live, Undo, denied path, Stop, disable and persistence. Missing Chromium or mismatched host process identity must not be treated as successful acceptance.

Windows junction/mandatory-file-lock/real process-recovery evidence is **OWNER_LIVE_REQUIRED** until the Windows CI/host gate actually runs. No model or paid provider is required for these deterministic file operations.
