# Learning Case: F-017-discovery-taskxchange

## Metadata
MODEL: claude-fable-5-1
AGENT: agent-C2+lead
START_SHA: 3ec4c81d72b4930e1ac9006541ac7ebd8036ab6a
END_SHA: 3d1e005
LEARNING_STATUS: VERIFIED
OUTCOME: FIXED
VERIFIED_BY: pytest:command-center/tests/test_secrem_discovery.py
CONFIDENCE: 0.85
TAGS: {"domain": "security", "bug_class": "ssrf", "component": "bcc.discovery", "severity": "LOW", "security_boundary": "egress"}
FINDINGS: F-017, BUG-005

## Task
discovery extra_urls SSRF; taskxchange app_id/task_id traversal; silent-port hang

## Symptom
POST /api/models/discover probed any URL (metadata, file://); GET /taskxchange/result did mkdir/read under apps/<app_id> with '..'.

## Reproduction
- command-center/tests/test_secrem_discovery.py

## Evidence
- pre-fix: no validation on extra_urls; result() called mkdir before checking app registry
- post-fix: 8 blocked URL forms never reach the transport and appear as rejected=True; traversal app_id/task_id → 400/404 with zero directories created; silent port returns within PROBE_TIMEOUT with 'занят другим процессом'

## Hypotheses considered
- no target policy on owner-supplied URLs (root cause)
- path built from request segment before registry membership check

## Rejected hypotheses + why
- block all public IPs (LAN/remote runners are legitimate discovery targets)

## Root cause
Owner routes trusted request content for network targets and filesystem paths.

## Relevant code paths
- command-center/bcc/discovery.py:_reject_reason
- command-center/bcc/discovery.py:_address_reason
- command-center/bcc/features/task_exchange.py:is_safe_segment
- command-center/bcc/features/task_exchange.py:result

## Fix strategy
Scheme/userinfo/metadata-hostname checks then resolve every address (bounded) rejecting link-local/multicast/unspecified/reserved (loopback and RFC1918 allowed by design); safe-segment regex + registry membership before any FS call; same validator on the inbox write path.

## Alternatives considered
- validating the code-provided endpoints list (trusted constants)

## Why this fix was chosen
Minimal policy matching discovery's purpose (local model servers) while closing metadata/link-local pivots and traversal.

## Files changed
- command-center/bcc/discovery.py
- command-center/bcc/features/task_exchange.py

## Tests added
- command-center/tests/test_secrem_discovery.py

## Original reproduction after fix
blocked

## Adversarial variants
- http://2852039166/
- [::ffff:169.254.169.254]
- [64:ff9b::a9fe:a9fe]
- fe80::1%eth0
- LOCALHOST.
- %2e%2e%2f encoded traversal via HTTP

## Regression
discovery/task_exchange suites green (part of 110 passed)

## Fresh external verification
pytest with MockTransport recording zero probes; real asyncio silent server for BUG-005 timing.

## Generalizable lessons
- Owner-authenticated does not mean owner-intended: validate targets and paths on every route.

## Teach local model
- Recognize: mkdir/open on a path containing a request segment
- Prefer: validate segment + membership before touching the FS

## Limitations / follow-up
- DNS rebinding between resolve and probe not defended.
