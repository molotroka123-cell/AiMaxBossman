# Learning Case: F-009-terminal-sandbox-root-skip

## Metadata
MODEL: claude-fable-5-1
AGENT: fable-lead
START_SHA: 3ec4c81d72b4930e1ac9006541ac7ebd8036ab6a
END_SHA: aa672829
LEARNING_STATUS: VERIFIED
OUTCOME: FIXED
VERIFIED_BY: pytest:command-center/tests/test_secrem_f009_terminal.py
CONFIDENCE: 0.85
TAGS: {"bug_class": "authz_bypass", "component": "bcc.features.tools_terminal", "domain": "security", "security_boundary": "host_filesystem", "severity": "HIGH"}
FINDINGS: F-009, F-011

## Task
command-center terminal.run sandbox skipped allowed-roots check and bind-mounted arbitrary cwd RW (HIGH)

## Symptom
mode=sandbox set effective_roots=[cwd]: the model chose any host directory as the 'root' and it went into `-v cwd:/work` read-write at effect auto, no approval.

## Reproduction
- command-center/tests/test_secrem_f009_terminal.py::test_repro_cwd_outside_roots_refused_in_every_mode (fails on pre-fix code: tool accepted outside cwd)

## Evidence
- tools_terminal._tool_run: `effective_roots = [cwd] if mode == 'sandbox' else roots` (pre-fix)
- TerminalManager.start had no containment check; only policy.decision on command text
- post-fix: outside cwd → ToolResult(error) 'вне разрешённых корней' for sandbox/project_host/system_admin; no session created

## Hypotheses considered
- sandbox semantics intentionally 'run anywhere in a container' (design)
- cwd resolution happened after authz so ../ and symlinks compared unresolved
- missing session ownership let other tasks drive a session

## Rejected hypotheses + why
- container isolation is enough: the mount is the host directory itself, RW — isolation of the process does not protect host files

## Root cause
Authorization of the path was replaced by the container boundary; the container does not bound the mounted host directory.

## Relevant code paths
- command-center/bcc/features/tools_terminal.py:_tool_run
- command-center/bcc/features/tools_terminal.py:_resolve_cwd
- command-center/bcc/v2/terminal_control.py:TerminalManager.start

## Fix strategy
Same roots for all modes (owner roots + own scratch); resolve cwd before authz; manager-level containment check before policy; sessions carry owner=task id enforced on status/stdin/kill; normalize_run_args shows resolved cwd in the approval preview.

## Alternatives considered
- require approval for sandbox mounts (keeps arbitrary mount reachable via approval fatigue)
- read-only mount (breaks the sandbox's purpose; still leaks reads)

## Why this fix was chosen
Path authorization is the trusted boundary; the container stays as defense in depth. Minimal, no new mode.

## Files changed
- command-center/bcc/features/tools_terminal.py
- command-center/bcc/v2/terminal_control.py

## Tests added
- command-center/tests/test_secrem_f009_terminal.py

## Original reproduction after fix
blocked in all three modes; zero sessions started

## Adversarial variants
- ../ traversal from root: blocked
- symlink inside root pointing outside: blocked (resolved before authz)
- direct TerminalManager.start with outside cwd: PermissionError
- foreign task status/stdin/kill on owned session: error

## Regression
command-center focused: 88 passed / 1 skipped (tool loop, terminal/browser, terminal map, e2e mission, redaction)

## Fresh external verification
pytest fresh process runs (real subprocess in project_host mode inside root succeeds; outside refused).

## Generalizable lessons
- A sandbox that mounts a caller-chosen host path is not a boundary for that path.
- Resolve, then authorize, then execute — never authorize the unresolved string.

## Teach local model
- Recognize: any 'roots = [arg]' pattern
- Inspect first: where is cwd resolved relative to the containment check?
- Avoid: relying on container isolation for host-path authorization
- Verify using: outside dir, ../ , symlink-out, plus a control run inside the root

## Limitations / follow-up
- Docker daemon unavailable on this host: RW bind-mount runtime proof NOT_TESTED_ON_THIS_HOST (test skips with marker).
