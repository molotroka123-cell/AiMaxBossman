# App iframe state lost during shell refresh

ID: ASTRA-CC-UI-001. Severity: P1 owner workflow interruption.

CI run 34291958202, Python 3.11 and 3.14 Command Center jobs: the real
File Commander owner test restored a file successfully, selected a denied
parent directory, then expected its policy refusal. Instead the interface
returned to the configured root and displayed the initial successful scan.
The Python 3.12 job reached the later control; that ambiguous locator was
already corrected in the input tree.

The shell calls `renderPage()` on initial WebSocket connection, reconnection,
and Refresh. Each successful render unconditionally removed the previous
view, including the embedded app iframe. Removing its ancestor destroys its
browser context: selections, preview state, in-flight feedback and errors
disappear. The File Commander script then reinitialized to its first allowed
root. This explains the CI timing sensitivity; a server policy was not bypassed.

Negative control: the new Node test executes the unmodified production
`renderPage()` function with an instrumented DOM. It fails because the active
iframe's ancestor is detached once. Four changed-identity/policy controls
pass. This is deterministic JavaScript preparation, not live browser proof.

Fix: app view includes its exact app identifier. The shell retains the
currently attached frame only when app identifier, source URL, sandbox and
title all match the refreshed live app. It replaces the launcher header so
process controls and health metadata update. A stopped app, another app,
different address or changed sandbox uses the normal replacement path.

Positive control: five Node tests pass, including zero ancestor detachments
and fresh launcher header identity. Existing Playwright owner regression now
explicitly refreshes after selecting the denied path and after receiving the
refusal. It waits for the old header to detach before checking retained input
and error state. It still exercises real app process launch, file mutation,
rollback, BCC restart, persistent policy and actual filesystem bytes.

The app-frame and existing Web Designer autosave Node suites together report
**22 passed, zero skipped** (3.42 seconds). JavaScript syntax, workflow YAML
and browser test compilation pass. The local browser invocation reports
**one skipped**, with no live browser execution.

Local Chromium is unavailable, so this document does not claim the browser
case passed locally. Required live proof is the real Command Center CI job
on the integrated SHA. The mandatory Node contract step is added without
changing coverage thresholds or timeout settings.

```bash
node --test command-center/ui/tests/app_view.test.mjs
PYTHONPATH=.:bossman-core:command-center python -m pytest -c command-center/pyproject.toml \
  command-center/tests/test_apps_files_browser_owner.py -q
```
