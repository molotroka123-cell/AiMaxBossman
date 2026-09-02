# Learning Case: F-003-media-probe-path

## Metadata
MODEL: claude-fable-5-1
AGENT: fable-lead
START_SHA: bb944d47864e70c3b93f01382e94f22dd59aeab5
END_SHA: 9ba0300c390a95f9b8eddbf494c68f24ea99bf83
LEARNING_STATUS: VERIFIED
OUTCOME: FIXED
VERIFIED_BY: pytest:bossman-core/tests/test_tools.py, glm-5.3-runpod-regression
CONFIDENCE: 0.95
TAGS: {"bug_class": "path_traversal", "component": "bossman.toolkit.media", "domain": "security", "security_boundary": "filesystem", "severity": "MEDIUM"}
FINDINGS: F-003

## Task
media.probe lacked path validation

## Symptom
probe built (workdir/arg).resolve() and passed it to ffprobe without any check, unlike its ffmpeg twin.

## Reproduction
- call media.probe with ../../../etc/passwd, /etc/passwd, C:\Windows\win.ini

## Evidence
- ffmpeg path used _path_arg_ok; probe did not
- after fix all three inputs rejected (test_media_probe_refuses_escape_paths)

## Hypotheses considered
- missing barrier on the twin function (root cause)

## Root cause
Inconsistent application of an existing barrier between two sibling tools.

## Relevant code paths
- bossman-core/bossman/toolkit/media.py:probe
- bossman-core/bossman/toolkit/media.py:_path_arg_ok

## Fix strategy
Apply _path_arg_ok to probe.path (rejects absolute, drive letters, UNC, '..').

## Alternatives considered
- sandbox ffprobe in a container (heavier; barrier already existed)

## Why this fix was chosen
Reuse the same barrier as ffmpeg — one policy, two callers.

## Files changed
- bossman-core/bossman/toolkit/media.py

## Tests added
- bossman-core/tests/test_tools.py::test_media_probe_refuses_escape_paths

## Original reproduction after fix
blocked

## Adversarial variants
- absolute POSIX
- Windows drive letter
- dot-dot relative

## Regression
bossman-core 1234 passed / 50 skipped

## Fresh external verification
pytest re-run; GLM 5.3 RunPod regression.

## Generalizable lessons
- When one of two twin tools has a barrier, grep for the sibling — inconsistency is the bug.

## Teach local model
- Recognize: two functions building subprocess args from a path
- Inspect first: does each apply the same validator?
- Verify using: the three canonical escape forms
