# Learning Case: F-012-verification-spoofing

## Metadata
MODEL: claude-fable-5-1
AGENT: fable-lead
START_SHA: 3ec4c81d72b4930e1ac9006541ac7ebd8036ab6a
END_SHA: fc903a2
LEARNING_STATUS: VERIFIED
OUTCOME: FIXED
VERIFIED_BY: pytest:command-center/tests/test_secrem_f012_verification.py, pytest:command-center/tests/test_feat_governor_review.py
CONFIDENCE: 0.88
TAGS: {"domain": "security", "bug_class": "verification_spoofing", "component": "bcc.features.review_gate", "severity": "MEDIUM", "security_boundary": "verification"}
FINDINGS: F-012

## Task
review gate accepted textual self-report as PASS

## Symptom
Task became completed when the worker echoed the criteria string or the LLM reviewer answered text starting with PASS.

## Reproduction
- command-center/tests/test_secrem_f012_verification.py::test_repro_text_claims_never_complete_without_evidence (6 spoof payloads)

## Evidence
- review_gate._verdict: `criteria.lower() in answer.lower()` → True; `text.upper().startswith('PASS')` → True (pre-fix)
- post-fix: all six spoofs → waiting_approval with review_escalation preview containing UNVERIFIED; real file effect → completed

## Hypotheses considered
- verdict derived from model text (root cause)
- reviewer model same family as worker (correlated self-report)

## Rejected hypotheses + why
- stronger reviewer prompt: still text authority; a compromised planner controls both sides

## Root cause
Completion depended on text the untrusted model controls; there was no fresh observation of external state.

## Relevant code paths
- command-center/bcc/v2/verification.py
- command-center/bcc/features/review_gate.py:_verdict
- command-center/bcc/features/review_gate.py:_gate

## Fix strategy
ExpectedState(file/db/browser) → fresh observe → compare; text can only veto (reviewer FAIL); UNVERIFIED escalates to a human; evaluations store evidence artifacts.

## Alternatives considered
- keep criteria substring as one of several signals (still upgradable by echo)
- LLM judge with tools (still self-report unless the judge's observations are the evidence)

## Why this fix was chosen
Makes 'fresh observation → verification' the only path to VERIFIED, matching the core invariant; minimal API change (review.evidence).

## Files changed
- command-center/bcc/v2/verification.py
- command-center/bcc/features/review_gate.py
- command-center/tests/test_feat_governor_review.py

## Tests added
- command-center/tests/test_secrem_f012_verification.py

## Original reproduction after fix
all spoof payloads fail to complete the task

## Adversarial variants
- 'PASS'
- 'PASS: criteria satisfied'
- exact criteria echo
- fake JSON success
- tool success=true without effect
- stale cached PASS
- expectation present but effect absent → FAILED then escalation
- file outside evidence roots → UNVERIFIED
- sha mismatch / content changed after first read → FAILED
- db table outside allowlist → UNVERIFIED

## Regression
command-center focused 88 passed / 1 skipped; migrated governor/review tests green

## Fresh external verification
pytest: real files created/reopened, real SQLite rows re-queried in fresh sessions.

## Generalizable lessons
- A model saying PASS is not evidence; only a re-observation of the world is.
- Text may veto, never approve.

## Teach local model
- Recognize: verdict computed from answer text or from an LLM's first word
- Prefer: expected-vs-observed comparison with fresh reads
- Verify using: an echo of the criteria with no side effect must NOT complete

## Limitations / follow-up
- browser evidence path requires a live session; covered only by UNVERIFIED-on-no-session test.
