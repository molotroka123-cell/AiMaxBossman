# Learning Case: BCC-V2-FINAL-CLOSURE-001/cdp-port-race

## Metadata
MODEL: claude-opus-5
AGENT: fable-lead
START_SHA: 83abfe36d34627b1d704e47ae166e0eaf15e1e59
END_SHA: 023148d
LEARNING_STATUS: VERIFIED
OUTCOME: FIXED
VERIFIED_BY: ci:github-actions
CONFIDENCE: 0.9
TAGS: {"domain": "test_infrastructure", "bug_class": "port_race", "component": "command-center.tests.desktop", "severity": "MEDIUM"}
FINDINGS: UX2-CDP-PORT-RACE, UX2-PROXY-LOOPBACK

## Task
Command Center CI red on py3.11 and py3.12: the real-Chromium desktop test never reached the browser debug endpoint

## Symptom
test_real_chromium_app_window_renders_command_center failed in CI only, with 'окно не поднялось: None, rc=None': the browser process was alive and had printed 'DevTools listening', but /json/version never answered within 30 s of polling.

## Reproduction
- CI run 33760662032 on 83abfe3; both matrix jobs (py3.11, py3.12) failed identically while 814 other tests passed. Not reproducible locally.

## Evidence
- CI 33760662032 (83abfe3): 1 failed, 814 passed on both Python versions; captured stderr shows 'DevTools listening on ws://127.0.0.1:50661/...' while rc=None
- CI 33782358395 (fc09a1c): success on py3.11 and py3.12
- CI 33791453157 (0744604): success

## Hypotheses considered
- wrong address polled because the pre-allocated port was reassigned (confirmed as the mechanism the fix removes)
- readiness signalled only by an endpoint that may never have belonged to the test (confirmed)

## Rejected hypotheses + why
- flaky timing needing a longer timeout - rejected: 100 polls over 30 s against a browser that already announced its debug server
- browser crashed - rejected: proc.poll() returned None, the process was alive at assertion time
- proxy interception on the loopback request - rejected: GitHub runners set no proxy vars and the poller already used an empty ProxyHandler
- localhost resolving to IPv6 while the browser bound IPv4 - rejected: the test used the literal 127.0.0.1, never the name
- skip or soften the test in CI - rejected on principle: converts a real signal into silence and leaves the desktop feature unproven

## Root cause
The test allocated an ephemeral port itself (bind to 0, read, close) and passed it to the browser as --remote-debugging-port=N. Between closing that socket and the browser binding it, the kernel may reassign the port to another socket, so the poller can address a stranger. The HTTP endpoint was also the only readiness signal, giving no independent way to learn the real port or the moment the debug server started listening.

## Relevant code paths
- command-center/tests/test_ux2_desktop.py::_devtools_endpoint
- command-center/tests/test_ux2_desktop.py::test_real_chromium_app_window_renders_command_center
- command-center/tests/test_ux2_thinking_pane.py::loopback_get
- tools/capture_ux_evidence.py::Server.start

## Fix strategy
Let the browser choose the port (--remote-debugging-port=0, --remote-debugging-address=127.0.0.1) and read <user-data-dir>/DevToolsActivePort, which the browser writes only once the debug server is listening; it carries the real port and the browser websocket path. Connect Playwright over that websocket and assert the product string via Browser.getVersion over CDP. Separately, use a narrow trust_env=False client for the two loopback readiness checks so ALL_PROXY/HTTP(S)_PROXY are not applied to 127.0.0.1.

## Alternatives considered
- retry loop with a longer deadline (does not address a wrong address)
- retry the whole test on failure (hides a deterministic defect)
- mock the browser or Popen (would stop proving the desktop feature)

## Why this fix was chosen
It removes the race rather than widening the window around it: the file that carries the address is written only when the address is ready, so address and readiness cannot disagree. All feature assertions are preserved and one is added.

## Files changed
- command-center/tests/test_ux2_desktop.py
- command-center/tests/test_ux2_thinking_pane.py
- tools/capture_ux_evidence.py

## Tests added
- command-center/tests/test_ux2_desktop.py::test_local_health_checks_ignore_proxy_environment
- port-provenance assertion inside test_real_chromium_app_window_renders_command_center

## Original reproduction after fix
1 failed, 814 passed, 2 skipped (both Python versions)

## Adversarial variants
- dead socks proxy that cannot be constructed without the socks extra
- proxy set to a closed loopback port
- closed local port with proxy set must still report not-alive (the fix cannot be 'always true')
- browser exits during startup: helper reports exit code and file contents instead of waiting out the timeout

## Regression
tests/test_ux2_desktop.py 12 passed; Command Center suite 815 passed, 3 skipped locally; CI py3.11 816 passed, 2 skipped

## Fresh external verification
GitHub Actions on the exact SHA, both Python versions, real preinstalled Chromium

## Failed approaches / recovery lessons
- A process that is alive while its endpoint is unreachable is an addressing problem, not a timing problem.

## Generalizable lessons
- Never pre-allocate a port and hand it to another process; ask the child to choose (port=0) and to report what it chose.
- Prefer a readiness signal the subject publishes at the moment it becomes ready over polling an endpoint that may not be yours.
- Environment proxy variables are for external destinations; disable them narrowly at loopback call sites, never globally.
- A test that fails only in CI differs environmentally: enumerate environmental differences before touching assertions.

## Teach local model
- isinstance-free rule: alive process + unreachable endpoint => wrong address, not slow start
- port=0 plus DevToolsActivePort is the race-free way to attach to a Chromium debug server
- httpx/requests: use trust_env=False for 127.0.0.1 health checks; leave provider clients alone

## Limitations / follow-up
- Why Chrome's HTTP /json/version was unreachable in that CI environment is still unexplained; the test no longer depends on it, so it is not a blocker.
