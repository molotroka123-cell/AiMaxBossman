# Learning Case: F-013-approval-identity

## Metadata
MODEL: claude-fable-5-1
AGENT: fable-lead
START_SHA: 3ec4c81d72b4930e1ac9006541ac7ebd8036ab6a
END_SHA: 043d3fa
LEARNING_STATUS: VERIFIED
OUTCOME: FIXED
VERIFIED_BY: pytest:command-center/tests/test_secrem_f013_approval_identity.py, pytest:command-center/tests/test_v21_tool_loop.py
CONFIDENCE: 0.9
TAGS: {"domain": "security", "bug_class": "toctou", "component": "bcc.engine", "severity": "MEDIUM", "security_boundary": "approval"}
FINDINGS: F-013

## Task
approved tool call re-resolved by name at resume; args_hash write-only

## Symptom
After approval, re-registering a tool under the same name (MCP refresh) executed a different implementation; tampering pending args was not detected.

## Reproduction
- command-center/tests/test_secrem_f013_approval_identity.py::test_repro_reregistered_impl_after_approval_is_denied

## Evidence
- _resume_pending_tool: `spec = TOOLS.get(pending['tool'])` then execute; args_hash stored but never compared (pre-fix)
- post-fix: rows[0].status == 'rejected', approved_by == 'system:identity_mismatch', neither handler ran

## Hypotheses considered
- approval keyed by name only (root cause)
- no registry generation to detect re-registration

## Rejected hypotheses + why
- freeze the registry during pending approvals (blocks legitimate refresh, race-prone)

## Root cause
Approval identity lacked the implementation and argument binding; resume trusted the current registry state.

## Relevant code paths
- command-center/bcc/tools.py:approval_digest
- command-center/bcc/tools.py:ToolSpec.impl_fingerprint
- command-center/bcc/tools.py:ToolRegistry.register
- command-center/bcc/engine.py:_resume_pending_tool

## Fix strategy
approval_digest = sha256(tool, impl_fingerprint incl. registration generation, normalized args, capability, agent/task); computed at ASK, recomputed at resume; mismatch → reject + new approval required. Cross-trust name collisions (mcp/plugin vs first-party) refused at register.

## Alternatives considered
- compare args_hash only (misses impl swap)
- pin handler object identity (breaks across restart)

## Why this fix was chosen
Content-addressed identity survives restarts and catches both impl swap and arg tampering with one check.

## Files changed
- command-center/bcc/tools.py
- command-center/bcc/engine.py
- command-center/bcc/plugin_security.py

## Tests added
- command-center/tests/test_secrem_f013_approval_identity.py

## Original reproduction after fix
denied

## Adversarial variants
- args tampered in pending checkpoint → not executed
- unchanged impl → executes exactly once
- mcp→builtin and builtin→mcp name squatting → ValueError

## Regression
command-center tool-loop 14 passed; focused 88 passed

## Fresh external verification
pytest end-to-end through engine claim/execute/approval watcher with DB-backed checkpoints.

## Generalizable lessons
- An approval must name WHAT will run (content), not a label that can be rebound.

## Teach local model
- Recognize: lookup-by-name after a human decision
- Prefer: digest over implementation + normalized args + context, recomputed at execution
- Verify using: swap the implementation between approve and resume
