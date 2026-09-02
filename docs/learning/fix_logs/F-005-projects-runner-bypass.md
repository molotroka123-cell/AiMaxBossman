# Learning Case: F-005-projects-runner-bypass

## Metadata
MODEL: claude-fable-5-1
AGENT: agent-B+lead
START_SHA: 3ec4c81d72b4930e1ac9006541ac7ebd8036ab6a
END_SHA: 6542273
LEARNING_STATUS: VERIFIED
OUTCOME: FIXED
VERIFIED_BY: pytest:bossman-core/tests/test_secrem_f005_projects.py
CONFIDENCE: 0.85
TAGS: {"domain": "security", "bug_class": "policy_bypass", "component": "bossman.projects.runner", "severity": "MEDIUM", "security_boundary": "execution"}
FINDINGS: F-005

## Task
projects pipeline bypassed runner._call_tool enforcement; templated shell

## Symptom
REGISTRY[spec['builtin']].handler(params) ran without allowlist/approval; cmd templates were formatted into create_subprocess_shell.

## Reproduction
- bossman-core/tests/test_secrem_f005_projects.py::test_repro_param_injection_stays_single_argv_element
- ::test_undeclared_builtin_is_refused_without_execution

## Evidence
- pre-fix: shlex.quote per value then .format into a shell string; builtin handler called directly
- post-fix: build_cmd_argv → ['ffmpeg','-i','a.mp4; rm -rf /','$(id).mp4'] via exec; undeclared builtin → ProjectToolDenied; confirm_default → approvals.create/wait

## Hypotheses considered
- structural: projects had its own executor path (root cause)
- quoting believed sufficient

## Rejected hypotheses + why
- route through runner._call_tool directly (needs AgentSpec/run context the project pipeline lacks) — replicated its semantics instead

## Root cause
A second execution path existed without the canonical allowlist/approval discipline.

## Relevant code paths
- bossman-core/bossman/projects/runner.py:_run_builtin
- bossman-core/bossman/projects/runner.py:build_cmd_argv
- bossman-core/bossman/projects/runner.py:_execute

## Fix strategy
Allowlist builtins by registry.yaml declaration; confirm_default/mandatory_confirm → owner approval; templates split into argv BEFORE substitution with restricted placeholders; only the documented `sh -c` template keeps a (quoted) shell.

## Alternatives considered
- ban shell templates entirely (breaks piper_local pipeline; pinned as known exception)

## Why this fix was chosen
Model-supplied values can no longer move argument boundaries; approvals reuse the canonical store.

## Files changed
- bossman-core/bossman/projects/runner.py

## Tests added
- bossman-core/tests/test_secrem_f005_projects.py

## Original reproduction after fix
blocked

## Adversarial variants
- unknown placeholder
- extra params
- missing values
- malformed shell template

## Regression
bossman-core focused 247 passed incl. stage13 hostexec red-team

## Fresh external verification
pytest; approvals monkeypatched to assert calls, no subprocess for denied cases.

## Generalizable lessons
- Every executor path must go through the same policy gate; a 'convenience' path is a bypass.

## Teach local model
- Recognize: direct REGISTRY[...].handler calls outside the runner
- Prefer: argv split before substitution
