# Learning Case: F-001-fs-search-glob-escape

## Metadata
MODEL: claude-fable-5-1
AGENT: fable-lead
START_SHA: bb944d47864e70c3b93f01382e94f22dd59aeab5
END_SHA: 9ba0300c390a95f9b8eddbf494c68f24ea99bf83
LEARNING_STATUS: VERIFIED
OUTCOME: FIXED
VERIFIED_BY: pytest:bossman-core/tests/test_tools.py, poc:.agents/redteam/poc_search_glob.py re-run, glm-5.3-runpod-regression
CONFIDENCE: 0.93
TAGS: {"domain": "security", "bug_class": "path_traversal", "component": "bossman.toolkit.files", "severity": "HIGH", "security_boundary": "filesystem"}
FINDINGS: F-001

## Task
fs.search glob argument escapes agent workspace (HIGH)

## Symptom
fs.search(pattern='.', glob='../../outside/*') returned canary lines from two levels above the workspace; any default-granted coder/analyst planner had arbitrary file read.

## Reproduction
- .agents/redteam/poc_search_glob.py (synthetic canary BOSSMAN_TEST_SECRET_… outside workdir)
- variant: directory junction inside workdir + default glob

## Evidence
- PoC output contained the outside canary line before the fix
- fs_search iterated ctx.workdir.rglob(glob) with the model-supplied glob and never applied _resolve to candidates
- after fix: PoC prints VERDICT: blocked; junction variants blocked; in-workdir search still finds hits

## Hypotheses considered
- glob passed to rglob unchecked (root cause)
- rglob follows junctions/symlinks outward
- workdir itself mis-resolved

## Rejected hypotheses + why
- workdir mis-resolution: workdir resolved correctly; only candidate paths escaped
- only junctions: plain '../' glob escaped without any link

## Root cause
fs_search never confined the per-candidate resolved path to the resolved workdir; the glob was treated as trusted.

## Relevant code paths
- bossman-core/bossman/toolkit/files.py:fs_search
- bossman-core/bossman/toolkit/files.py:_contains

## Fix strategy
Resolve each rglob candidate and skip it unless _contains(root, path) (relative-path containment, not string prefix).

## Alternatives considered
- reject globs containing '..' (bypassable via junction/symlink)
- chroot-like copy of workdir (too heavy)

## Why this fix was chosen
Containment on the resolved target catches ../ , junctions and symlinks uniformly; zero behaviour change for legitimate in-workdir globs.

## Files changed
- bossman-core/bossman/toolkit/files.py

## Tests added
- bossman-core/tests/test_tools.py::test_fs_search_glob_cannot_escape_workdir
- bossman-core/tests/test_tools.py::test_fs_search_still_finds_in_workdir

## Original reproduction after fix
blocked (PoC re-run after fix)

## Adversarial variants
- junction + default glob: blocked
- junction + explicit j/* glob: blocked

## Regression
bossman-core 1234 passed / 50 skipped / 0 failed (Fable Phase 2); re-run green on GLM 5.3 RunPod acceptance

## Fresh external verification
PoC and variants re-executed against fresh filesystem fixtures; independent RunPod regression run by GLM 5.3.

## Generalizable lessons
- Any model-controlled pattern that drives a filesystem walk is a path argument and needs containment on the RESOLVED result, not on the input string.
- String prefix checks are not containment.

## Teach local model
- Recognize: glob/rglob/os.walk fed by a tool argument
- Inspect first: is each yielded path resolved and compared to the resolved root?
- Avoid: filtering the input string for '..'
- Prefer: Path.resolve() + relative_to/parents containment per candidate
- Verify using: PoC with canary outside root + junction variant
- General invariant: model-supplied paths are untrusted until resolved and contained
