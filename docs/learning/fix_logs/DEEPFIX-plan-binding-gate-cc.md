# Learning Case: DEEPFIX-plan-binding-gate-cc

## Metadata
MODEL: claude-fable-5-1
AGENT: fable-lead
START_SHA: 344ecfc
END_SHA: HEAD+1
LEARNING_STATUS: VERIFIED
OUTCOME: FIXED
VERIFIED_BY: pytest:command-center/tests/test_deep_fix_gate.py
CONFIDENCE: 0.8
TAGS: {"domain": "security", "bug_class": "verification_spoofing", "component": "bcc.features.deep_fix", "severity": "LOW", "security_boundary": "verification"}
FINDINGS: F-012

## Task
Wire the first Deep Fix gate into the command-center engine (flag OFF): verification plan hashed and bound before the patch (idea F4.1)

## Symptom
review.evidence could be rewritten mid-task; a worker (or anyone with the owner token) could move the goalpost to a file that already exists and complete the task with fresh evidence for the wrong plan.

## Reproduction
- command-center/tests/test_deep_fix_gate.py::test_moved_goalpost_cannot_complete

## Evidence
- with the flag ON, first run binds meta.deep_fix.plan_hash; after /api/review/enable swaps evidence to an existing file, gate_completion returns fail → waiting_approval with 'goalpost moved' escalation
- flag OFF: no meta written, task completes as before (27 passed incl. review/F-012 suites)

## Hypotheses considered
- plan mutable after start (root cause)

## Rejected hypotheses + why
- freeze /api/review/enable while a run exists (blocks legitimate replanning; escalation to a human is the right authority)

## Root cause
No binding between the verification plan and the run that must satisfy it.

## Relevant code paths
- command-center/bcc/features/deep_fix.py:plan_hash
- command-center/bcc/features/deep_fix.py:_gate
- command-center/bcc/features/review_gate.py

## Fix strategy
before_run binds sha256(evidence+criteria) once; gate_completion refuses completion when the current plan differs, escalating with both hashes; review_gate keeps deciding the fresh-evidence verdict.

## Alternatives considered
- store the plan hash in the checkpoint only (lost across restarts; meta persists)

## Why this fix was chosen
Smallest gate that makes 'verify what you promised' enforceable; composes with F-012 without touching it.

## Files changed
- command-center/bcc/features/deep_fix.py

## Tests added
- command-center/tests/test_deep_fix_gate.py

## Original reproduction after fix
blocked (waiting_approval, review_escalation with goalpost reason)

## Adversarial variants
- unchanged plan completes
- flag off no-op

## Regression
test_deep_fix_gate + test_feat_governor_review + test_secrem_f012_verification: 27 passed

## Fresh external verification
pytest through the real engine claim/execute with SQLite-backed task meta and real files

## Generalizable lessons
- Bind the acceptance criteria to the run before work starts; changing them is a human decision, not a worker convenience

## Teach local model
- Recognize: acceptance criteria stored in mutable task meta read at completion time
- Prefer: hash at start, compare at gate, escalate on mismatch

## Limitations / follow-up
- verifier isolation (F4.2) is by construction of verification.py, not additionally enforced here
