# Learning Case: UX2_CDP_LOOPBACK_2026-09-03

## Metadata

- date: 2026-09-03
- area: command-center tests / desktop window / local HTTP readiness
- code-fix commit: `023148d`
- files: `command-center/tests/test_ux2_desktop.py`, `command-center/tests/test_ux2_thinking_pane.py`, `tools/capture_ux_evidence.py`
- no production runtime code changed

## Symptom

Command Center CI was red on both Python 3.11 and 3.12 with exactly one
failure: `tests/test_ux2_desktop.py::test_real_chromium_app_window_renders_command_center`.
Everything else passed (814 passed, 2 skipped). The failure message was:

```
AssertionError: окно не поднялось: None, rc=None
```

Locally the same test passed every time, which is why it survived review.

## Reproducible evidence

- CI run 33760662032 on SHA `83abfe3`, both matrix jobs, identical failure.
- Captured stderr of the browser in the same job:
  `DevTools listening on ws://127.0.0.1:50661/devtools/browser/<uuid>`
- `rc=None` in the assertion: the browser process was **alive**, not crashed.
- The test polled `http://127.0.0.1:<port>/json/version` every 0.3 s for 30 s
  and never got an answer.

So: the browser was running, its debug server announced itself, and the test
still could not reach it. The two facts only fit if the test was asking a
different address than the one the browser bound, or if the readiness signal it
used was not the one that actually became ready.

## Root cause

The test chose the debug port itself: it bound a socket to port 0, read the
ephemeral port the kernel offered, closed the socket, and passed that number to
the browser as `--remote-debugging-port=N`.

Between closing that socket and the browser binding, the port is free. On a
loaded CI runner the kernel may hand the same ephemeral port to any other
socket — including one opened by the same test suite. The test then polls an
address that belongs to something else, or to nothing.

Compounding it, the HTTP endpoint `/json/version` was the **only** readiness
signal. There was no independent way to learn either the real port or the real
moment the debug server started listening.

## Rejected hypotheses

- **"Flaky timing, needs a longer timeout."** Rejected: 30 s of polling at
  0.3 s intervals is 100 attempts; a browser that has already printed
  "DevTools listening" is not still starting up.
- **"The browser crashed."** Rejected by the evidence: `proc.poll()` returned
  `None`, so the process was alive at assertion time.
- **"A proxy in CI intercepted the loopback request."** Rejected: GitHub
  runners set no proxy variables, and the poller already used an explicit
  empty `ProxyHandler`.
- **"CI resolves `localhost` to IPv6 while the browser binds IPv4."**
  Rejected as the cause: the test connected to the literal `127.0.0.1`, never
  to the name `localhost`.
- **"Skip or soften the test on CI."** Rejected on principle: that converts a
  real signal into silence and would have left the desktop feature unproven.

## Minimal fix

Stop guessing the port; let the browser choose and publish it.

- Launch with `--remote-debugging-port=0` and an explicit
  `--remote-debugging-address=127.0.0.1`.
- Read `<user-data-dir>/DevToolsActivePort`. The browser writes that file only
  after its debug server is listening, and it contains the real port on line 1
  and the browser websocket path on line 2. It is therefore both the address
  and the readiness signal, with no race between them.
- Connect Playwright over `ws://127.0.0.1:<port><path>` rather than through the
  HTTP endpoint, and assert the browser product string via `Browser.getVersion`
  over CDP so the "this is really Chrome" check is preserved.

Second, unrelated defect found while reproducing: local readiness checks used
`httpx.get(...)` with default `trust_env=True`, so `ALL_PROXY` / `HTTP(S)_PROXY`
from the environment were applied to `127.0.0.1`. A proxy is meant for external
providers; applied to loopback it either answers for someone else or fails on a
missing socks extra. Fixed with a narrow `trust_env=False` client at the two
loopback call sites only. Provider HTTP behaviour is untouched.

## Regression tests

- `test_real_chromium_app_window_renders_command_center` — unchanged feature
  assertions (real `--app` launch, login screen, in-window login, no token in
  the URL, no token anywhere in the window profile) plus a new assertion that
  the port came from `DevToolsActivePort` and is not a guessed value.
- `test_local_health_checks_ignore_proxy_environment` — sets a dead
  `socks5://127.0.0.1:<closed port>` in `ALL_PROXY`, `HTTP_PROXY`, `HTTPS_PROXY`
  and their lowercase forms, clears `NO_PROXY`, and requires that a live local
  server is still found **and** that a closed port is still reported dead.

Result: `tests/test_ux2_desktop.py` 12 passed; full Command Center suite
815 passed, 3 skipped locally; CI 816 passed, 2 skipped on py3.11.

## Adversarial variants

- Dead proxy that cannot even be constructed (socks without the extra
  installed) — the narrow client must not read it at all.
- Proxy set but pointing at a closed port — a naive client would hang or error;
  the fix must ignore it for loopback.
- Closed local port with proxy set — must still be reported as *not* alive, so
  the fix cannot be "always answer true".
- Browser exits during startup — the readiness helper reports the exit code and
  the file contents rather than waiting out the full timeout.

## Lessons for the local model

- A process that is **alive** while its endpoint is unreachable is an
  addressing problem, not a timing problem. Do not answer it with a longer
  timeout.
- Never pre-allocate a port and hand it to another process. The gap between
  "I closed the socket" and "it bound the socket" belongs to the kernel. Ask
  the child to choose (`port=0`) and to tell you what it chose.
- Prefer a readiness signal the subject itself publishes at the moment it
  becomes ready. A file that only exists once the server listens is stronger
  than polling an endpoint that may never have been yours.
- Environment proxy variables are for external destinations. Applying them to
  `127.0.0.1` is a bug; disable them narrowly at the loopback call site, never
  globally.
- When a test fails only in CI, the difference is environmental — enumerate the
  environmental differences before touching the assertion.
