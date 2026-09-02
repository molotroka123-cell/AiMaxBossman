# Learning Case: F-015-self-asserted-approval

## Metadata
MODEL: claude-fable-5-1
AGENT: fable-lead
START_SHA: 3ec4c81d72b4930e1ac9006541ac7ebd8036ab6a
END_SHA: 341bbee
LEARNING_STATUS: VERIFIED
OUTCOME: FIXED
VERIFIED_BY: pytest:command-center/tests/test_secrem_f015_self_assert.py, pytest:command-center/tests/test_feat_terminal_map.py
CONFIDENCE: 0.88
TAGS: {"bug_class": "authz_bypass", "component": "bcc.features.terminal", "domain": "security", "security_boundary": "approval", "severity": "LOW"}
FINDINGS: F-015

## Task
owner routes trusted approved:true and actor from the request body

## Symptom
POST /api/terminal/run and /api/browser/sessions/{id}/act executed ask-level actions when the body said approved: true.

## Reproduction
- command-center/tests/test_secrem_f015_self_assert.py::test_repro_terminal_self_asserted_approved_is_refused
- ::test_repro_browser_self_asserted_approved_is_refused

## Evidence
- `approved = bool(body.get('approved'))` in both routes (pre-fix)
- post-fix: approved:true → 403; approval_id for a different preview → 202; matching approved record → executes; replay of the consumed id → 202; approvals row status=consumed

## Hypotheses considered
- approval modelled as a flag instead of a record (root cause)

## Rejected hypotheses + why
- keep the flag but require owner token (single-token surface: every caller is 'owner')

## Root cause
No binding between the approval decision and the action being executed.

## Relevant code paths
- command-center/bcc/approvals.py:Approvals.consume
- command-center/bcc/features/terminal.py:run
- command-center/bcc/features/browser.py:act

## Fix strategy
Approvals.consume(id, kind, preview): approved row with identical deterministic preview is consumed once; routes create the approval on ask and only execute with a consumed match; actor restricted to agent|human.

## Alternatives considered
- HMAC-signed approval tokens (adds key management; DB row already authoritative)

## Why this fix was chosen
Reuses the existing approvals table and UI flow; anti-replay comes free from the status transition.

## Files changed
- command-center/bcc/approvals.py
- command-center/bcc/features/terminal.py
- command-center/bcc/features/browser.py

## Tests added
- command-center/tests/test_secrem_f015_self_assert.py

## Original reproduction after fix
refused

## Adversarial variants
- approval for another command/cwd/mode
- replay
- actor=root

## Regression
terminal/browser feature suites 26 passed after migrating one test to the record flow

## Fresh external verification
pytest through the HTTP routes with the real approvals table.

## Generalizable lessons
- An approval is a record about a specific action, never a boolean in the request that asks for the action.

## Teach local model
- Recognize: body.get('approved')
- Prefer: approval id → row → same preview → consume once

## Limitations / follow-up
- actor=human on owner routes remains owner authority (single-token surface) — per-capability HTTP authz is ACCEPTED_RISK_REQUIRES_OWNER.
