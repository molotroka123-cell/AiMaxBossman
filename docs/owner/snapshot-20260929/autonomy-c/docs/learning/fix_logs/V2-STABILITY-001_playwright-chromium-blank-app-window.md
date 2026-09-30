# Learning Case: V2-STABILITY-001/playwright-chromium-blank-app-window

## Metadata
MODEL: deepseek-v4-flash-latest
AGENT: stability-pass
START_SHA: 27e3bf93c3e0a24983874a0bf5d5893ffb68685d
END_SHA: 83221c94a8e9697c7c68e90884c986b49a574ce5
LEARNING_STATUS: VERIFIED
OUTCOME: FIXED
VERIFIED_BY: tool:cdp:raw-websocket
CONFIDENCE: 0.9
TAGS: {}
FINDINGS:

## Task
Owner desktop: blank --app window on Windows when autodetect picks a Playwright-cached Chromium build

## Symptom
bcc-desktop opens a Chromium --app window that stays blank; the page never loads and no request reaches the server; window exits or looks broken without explanation

## Reproduction
- BCC_DATA_DIR=<temp>; python -m bcc on 127.0.0.1:8877
- chrome --app=http://127.0.0.1:8877/ with %LOCALAPPDATA%\ms-playwright\chromium-1234 binary
- CDP: page target exists with the right URL but document.body is empty and location.href=about:blank
- same argv with system Chrome 152 or Edge loads fine

## Evidence
- diag3/diag4: page target with URL, body_len=0, readyState=complete
- diag5: M1 app-mode body 0 vs M3 normal navigation body 22122
- diag6: PW-cache Chromium sends no GET /; system Chrome and Edge send it
- post-switch harness to system Chrome: scenarios 1..8 all PASS

## Hypotheses considered
- CDP origin guard (rejected: raw WS protocol answers; blank page exists without any driver)
- profile conflicts / stray chrome processes holding the port (rejected: repeated clean launches + full process-tree kills changed nothing)
- server/port problems (rejected: identity 200; normal navigation loads the same page)
- debug flags (rejected: --no-first-run etc. matrix unchanged)

## Rejected hypotheses + why
- CDP origin guard
- profile conflicts / stray chrome processes holding the port
- server/port problems
- debug flags --no-first-run

## Root cause
Playwright-cached Chromium build (Chrome 151) does not navigate in --app mode on this Windows environment; autodetect picked it and the owner got a blank window with no diagnostics

## Relevant code paths
- command-center/bcc/desktop.py:run
- command-center/bcc/desktop.py:find_browser
- command-center/bcc/desktop.py:main

## Fix strategy
Diagnose, do not patch the build: warn when autodetect selects a playwright-cached build and print a ready instruction; add --window-timeout / BCC_APP_STARTUP_TIMEOUT with readable exit-124 message; --status JSON; window_opened_at in desktop.lock; Windows registry browser discovery; bcc-open --web entry; reorder candidates only for default call

## Alternatives considered
- always prefer system browser over mains (breaks explicit --browser and heroku-like flows)
- block playwright builds outright (breaks legitimate automation hosts)

## Why this fix was chosen
The owner gets an honest signal instead of a silent blank window, and there is always a one-command path to the web UI

## Files changed
- command-center/bcc/desktop.py
- command-center/pyproject.toml
- command-center/tests/test_ux2_desktop.py

## Tests added
- command-center/tests/test_ux2_desktop.py::test_runtime_window_timeout_is_passed_to_launcher
- command-center/tests/test_ux2_desktop.py::test_lock_records_window_opened_at
- command-center/tests/test_ux2_desktop.py::test_status_prints_json_state_without_launching
- command-center/tests/test_ux2_desktop.py::test_bcc_open_entry_command_gets_web_flag
- command-center/tests/test_ux2_desktop.py::test_bcc_desktop_entry_does_not_inject_web_flag

## Original reproduction after fix
FAIL: blank window with PW-cache Chromium; PASS with system Chrome and Edge

## Adversarial variants
- explicit --browser and --browser-arg untouched
- bcc-open flag injection only for bcc-open entry
- ALL_PROXY dead proxy: window and login still work
- no token in URL or logs

## Regression
command-center focused: 5 new tests pass; live scenarios 1..8 all PASS on system Chrome

## Fresh external verification
Live owner-desktop repro on Windows with real Chromium app-mode window driven by raw-CDP observer; system Chrome/Edge work, PW-cache build does not navigate

## Generalizable lessons
- browser autodetect must not trust existence alone for --app mode; an honest build marker plus owner-facing diagnostics beats a silent blank window
- live desktop scenario on the system browser is the only proof; headless/CDP-green is not

## Teach local model
- A window that opens but never navigates is an environment/browser issue, not a server issue: check body_len and whether GET / arrives
- Playwright-cached Chromium may fail --app navigation; prefer system Chrome/Edge on owner machines

## Limitations / follow-up
- repro confirmed on this Windows machine; other OS/build combos untested
- the failure mechanism inside the PW-cache Chromium build remains unexplained; no longer a blocker because diagnostics are in place
