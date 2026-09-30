# Web Designer safe responsive preview integration — 2026-09-06

Base: Fable `6464d523929c199bcefaab8311d2b1a39245101b`.
Source: PR #9 `d62e92ffba348ab224a5921b8bb15af5b039017f`.

Port only the responsive preview workbench: device presets, validated custom
dimensions, rotation, fit/fit-width/fixed zoom, project-local preferences, and
their tests. Do not merge the older branch's server or editor implementation.

The DOM parser, generator escaping, atomic file writes, and compare-and-swap
server code remain byte-for-byte identical to Fable. The editor retains dirty
buffer protection, base-version writes, selection invalidation and explicit
nested-content replacement confirmation. Preview retains `sandbox=allow-scripts`
and sender-window validation. Added browser fixture version checks and a picker
assertion at 50% zoom; these need actual Chromium before claiming GUI evidence.

Validation from the isolated integration worktree:

```sh
node --test command-center/tests/test_web_designer_viewport.mjs
PYTHONPATH="$PWD/command-center:$PWD/bossman-core" /tmp/bossman-epoch4-venv/bin/python -m pytest command-center/tests/test_web_designer.py command-center/tests/test_web_designer_viewport.py command-center/tests/test_web_designer_sandbox_ui.py -q -rs
/tmp/bossman-epoch4-venv/bin/python tools/skips_registry.py --check
git diff --check
```

Node: 17 passed. Python: 43 passed, 2 skipped because Chromium is unavailable.
The Python count includes one wrapper that reruns the Node contracts; the two
counts are not independent total coverage. No paid model calls and no claims of
browser validation or V3 closure.
