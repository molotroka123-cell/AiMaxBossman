# Browser installation readiness closure

Baseline: `a037302c0f72ff9e64cb59ca7aa7cc094aa0ed34`.
Scope: installation diagnosis, browser executable selection and the empty Browser screen.
This is not a live Chromium, navigation, takeover or security acceptance report.

| Finding | Severity | Reproduction and root cause | Fix and controls |
|---|---|---|---|
| BR-001 | P1 | With importable Playwright, set `PLAYWRIGHT_BROWSERS_PATH` to a missing directory. `/api/capabilities` grants Chromium and `/api/browser/health` reports available. The capability probe trusted a nonempty environment string; the manager only imported the adapter. A directory at the preinstalled executable path also counted as an executable. | Read installed Playwright revision metadata and verify a regular executable at the selected OS path. Check the actual optional Python adapter. Missing dependency, missing/empty directory, wrong revision/OS and denied execute permission all refuse. Installing and removing the executable changes the next observation without restart. |
| BR-002 | P1 | The feature's preinstalled-browser wrapper rewrote `chromium.launch` only. `launch_persistent_context` retained Playwright's default and could fail with the same installation reported available by BCC. | The manager supplies the measured executable to both launch methods. Positive dispatch controls cover standard-cache and preinstalled binaries, with and without a persistent profile. Headless-only installation refuses a visible session before starting a driver. |
| BR-003 | P2 | The empty Browser screen said `рантайм готов` even when its health request failed, or when only installation had been checked. | Show the installation recovery hint, an explicit unknown state after a failed probe, or “launch not yet checked” after discovery. Three rendered-UI controls inject those health conditions without claiming that the injected input proves installation. |
| BR-004 | P2 | The shared pytest availability helper started `sync_playwright` during module collection; collection inside an active event loop could leave an unfinished `Connection.init`. | Reuse the production filesystem resolver without driver startup or a stale availability cache. The common UI launcher passes that same path explicitly. A negative control recorded a driver-start attempt before the fix and none afterwards. |

The resolver supports Linux, Windows and macOS default cache locations, current
Chrome for Testing and legacy Chromium layouts, Linux/macOS ARM layouts,
`PLAYWRIGHT_BROWSERS_PATH`, hermetic value `0`, and relative paths resolved using
`INIT_CWD`. It uses the driver's declared revision, not a glob over stale cache
versions. The existing preinstalled `/opt/pw-browsers/chromium` path is preserved.
Polling does not start a driver, spawn a subprocess, retain a growing cache, or
create browser sessions. A 100-request control checks that no tasks or sessions
are added and the driver remains absent.

## Observed evidence

- Before the fix, all four initial negative controls failed on the baseline:
  missing directory capability; adapter-only availability; browser health;
  directory mistaken for executable.
- Local Python 3.12 focused run: **76 passed, 18 skipped**. The skipped tests
  require real Chromium; no browser launch succeeded in this environment.
- Genuine local prerequisite observation using installed Playwright and no
  Chromium: `available=false`, `executable=null`, `driver_started=false`,
  `sessions=0`.
- Existing capability, public/system health and browser API failure-state tests
  remain included. No existing assertions or skip conditions were weakened.
- Source whitespace validation passed. The release must rerun these checks on
  its final SHA. Native Windows/Linux browser launch and rendered-UI checks
  remain **CI_REQUIRED**; the filesystem layout fixtures are not native OS proof.

Focused command:

```sh
python -m pytest -c command-center/pyproject.toml \
  command-center/tests/test_browser_installed_readiness.py \
  command-center/tests/test_capability_manifest.py \
  command-center/tests/test_release_health.py \
  command-center/tests/test_lane4_p1_fixes.py \
  command-center/tests/test_feat_browser.py \
  command-center/tests/test_v22_browser_security.py \
  command-center/tests/test_browser_navigation_ui.py
```

Final integration must record the applied commit and final-SHA CI results in the
release closure ledger. Installation availability is deliberately not a healthy
browser claim: system health remains UNKNOWN until a connected live context is
observed, and missing prerequisites retain the existing OFFLINE status.
