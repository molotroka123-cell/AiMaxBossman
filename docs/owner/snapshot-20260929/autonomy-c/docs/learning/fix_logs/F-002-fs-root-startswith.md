# Learning Case: F-002-fs-root-startswith

## Metadata
MODEL: claude-fable-5-1
AGENT: fable-lead
START_SHA: bb944d47864e70c3b93f01382e94f22dd59aeab5
END_SHA: 9ba0300c390a95f9b8eddbf494c68f24ea99bf83
LEARNING_STATUS: VERIFIED
OUTCOME: FIXED
VERIFIED_BY: pytest:bossman-core/tests/test_tools.py, poc:.agents/redteam/poc_sibling_probe.py re-run, glm-5.3-runpod-regression
CONFIDENCE: 0.95
TAGS: {"bug_class": "path_traversal", "component": "bossman.toolkit.files", "domain": "security", "security_boundary": "filesystem", "severity": "MEDIUM"}
FINDINGS: F-002

## Task
fs.* root confinement used str.startswith (sibling-prefix escape)

## Symptom
With workdir …/coder, fs.read('../coder-secrets/s.txt') and fs.write('../coder-secrets/pwned.txt') succeeded.

## Reproduction
- .agents/redteam/poc_sibling_probe.py

## Evidence
- str(p).startswith(str(workdir)) is true for the sibling '…/coder-secrets'
- read and write both succeeded before the fix; PermissionError after

## Hypotheses considered
- prefix string comparison (root cause)
- missing resolve()

## Rejected hypotheses + why
- missing resolve(): paths were resolved; the comparison itself was wrong

## Root cause
_resolve compared string prefixes instead of a path containment relation.

## Relevant code paths
- bossman-core/bossman/toolkit/files.py:_resolve
- bossman-core/bossman/toolkit/files.py:fs_list

## Fix strategy
_contains(root, p): p == root or root in p.parents; fs_list refuses to descend into directories whose resolved target escapes.

## Alternatives considered
- append os.sep to the prefix (still string-based, breaks on drive roots/UNC)

## Why this fix was chosen
Uses pathlib semantics that already handle separators, case and drive roots.

## Files changed
- bossman-core/bossman/toolkit/files.py

## Tests added
- bossman-core/tests/test_tools.py::test_fs_read_sibling_prefix_escape_blocked
- bossman-core/tests/test_tools.py::test_fs_write_sibling_prefix_escape_blocked

## Original reproduction after fix
blocked

## Adversarial variants
- nested ../coder/../coder-secrets/x: blocked
- fs.list through junction: no recursion (poc_variants V3/V4)

## Regression
bossman-core 1234 passed / 50 skipped

## Fresh external verification
PoC re-run with fresh fixtures; GLM 5.3 RunPod regression.

## Generalizable lessons
- Prefix equality on paths is a classic sibling-escape; use parents containment.

## Teach local model
- Recognize: startswith(str(root)) in any authz check
- Prefer: relative_to()/parents
- Verify using: sibling directory sharing the root's name prefix
