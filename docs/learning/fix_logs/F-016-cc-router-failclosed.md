# Learning Case: F-016-cc-router-failclosed

## Metadata
MODEL: claude-fable-5-1
AGENT: agent-C2+lead
START_SHA: 3ec4c81d72b4930e1ac9006541ac7ebd8036ab6a
END_SHA: 5e389ff
LEARNING_STATUS: VERIFIED
OUTCOME: FIXED
VERIFIED_BY: pytest:command-center/tests/test_secrem_router_failclosed.py
CONFIDENCE: 0.85
TAGS: {"domain": "security", "bug_class": "fail_open", "component": "bcc.features.router", "severity": "MEDIUM", "security_boundary": "egress"}
FINDINGS: F-016

## Task
command-center router: cloud default-allow, force_model_id bypass, local mislabel

## Symptom
No meta → cloud_allowed=True (budget-derived); meta.force_model_id returned any model; kind=local at a cloud provider passed the 'cloud disabled' filter.

## Reproduction
- command-center/tests/test_secrem_router_failclosed.py

## Evidence
- router._make_pick_hook: `cloud_allowed = budget is None or budget > 0` (pre-fix)
- forks._force_model_hook returned {'model_id': mid} with no checks (pre-fix)
- post-fix: cloud chosen only with strict True from task meta / agent permissions / router rule; fork API 403 before insert; mislabeled rows rejected as 'cloud disabled'

## Hypotheses considered
- allow-by-default derived from budget absence (root cause)
- locality read from one DB column
- forced model trusted meta verbatim

## Rejected hypotheses + why
- change disqualify's reason string (existing tests assert exact list elements)
- DNS in derive_local (pure per-candidate function)
- single-label hostnames as local (unproven = cloud)

## Root cause
Three independent fail-open defaults in the routing path.

## Relevant code paths
- command-center/bcc/features/router.py:cloud_policy
- command-center/bcc/features/router.py:check_forced_model
- command-center/bcc/features/forks.py:_force_model_hook
- command-center/bcc/v2/model_router.py:derive_local

## Fix strategy
Strict-True policy with explicit veto semantics; forced model reuses model_router.disqualify; derive_local requires local kind + local provider kind + loopback/RFC1918 host.

## Alternatives considered
- separate cloud check in forks (duplicate logic — reused disqualify instead)

## Why this fix was chosen
Single source of truth for disqualification; fail-closed everywhere.

## Files changed
- command-center/bcc/features/router.py
- command-center/bcc/features/forks.py
- command-center/bcc/v2/model_router.py

## Tests added
- command-center/tests/test_secrem_router_failclosed.py

## Original reproduction after fix
closed

## Adversarial variants
- cloud_allowed 'true'/1/'yes' strings
- budget 0/True/'5'/NaN
- IPv4-mapped host in base_url
- unknown model id

## Regression
router/forks/openrouter/adaptive suites 110 passed / 2 skipped

## Fresh external verification
pytest with seeded providers/models in SQLite through the real pick_model hook and fork endpoint.

## Generalizable lessons
- A budget is a limit, never a grant.

## Teach local model
- Recognize: permission derived from absence of a value
- Prefer: explicit strict-typed allow

## Limitations / follow-up
- /router/preview and /router/candidates keep explicit body defaults (execute nothing).
