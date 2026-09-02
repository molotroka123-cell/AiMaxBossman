# Learning Case: F-008-F-016-gateway-failclosed

## Metadata
MODEL: claude-fable-5-1
AGENT: agent-B+lead
START_SHA: 3ec4c81d72b4930e1ac9006541ac7ebd8036ab6a
END_SHA: ec604ab
LEARNING_STATUS: VERIFIED
OUTCOME: FIXED
VERIFIED_BY: pytest:bossman-core/tests/test_secrem_f008_gateway_failclosed.py, pytest:bossman-core/tests/test_gateway_cloud_policy.py
CONFIDENCE: 0.88
TAGS: {"bug_class": "fail_open", "component": "bossman.gateway", "domain": "security", "security_boundary": "egress", "severity": "MEDIUM"}
FINDINGS: F-008, F-016

## Task
gateway cloud header fail-open; embeddings unchecked; audit by alias prefix; unbounded 429

## Symptom
Missing x-bossman-cloud-allowed meant allowed; /v1/embeddings ignored policy; llm.py counted cloud_calls by alias prefix so capability aliases routed to cloud were unaudited.

## Reproduction
- bossman-core/tests/test_secrem_f008_gateway_failclosed.py
- bossman-core/tests/test_secrem_429_retry.py

## Evidence
- _cloud_allowed: headers.get(..., '1') (pre-fix)
- post-fix: missing/unknown header → 403 and zero upstream hits; explicit 1 → 200 with x-bossman-cloud: 1 and cloud_requests_total=1
- GatewayClient retries 429/503/connect with capped jittered backoff, raises last error when exhausted

## Hypotheses considered
- default chosen for direct third-party clients (root cause: convenience default)
- embeddings route added later without the policy call

## Rejected hypotheses + why
- treat loopback-unauthenticated as trusted for cloud (UNKNOWN ≠ LOCAL)

## Root cause
Fail-open default plus audit derived from a name instead of the resolved route.

## Relevant code paths
- bossman-core/bossman/gateway/app.py:_cloud_allowed
- bossman-core/bossman/gateway/app.py:run_json
- bossman-core/bossman/gateway/client.py:_post_with_retry
- bossman-core/bossman/llm.py:resolved_cloud

## Fix strategy
Explicit-only allow; embeddings gated; response header + counter from route.is_cloud; client default closed; bounded idempotent retry.

## Alternatives considered
- retry inside gateway (would retry cloud spend without caller policy visibility)

## Why this fix was chosen
Fail-closed by default with an explicit opt-in that the core already sends.

## Files changed
- bossman-core/bossman/gateway/app.py
- bossman-core/bossman/gateway/client.py
- bossman-core/bossman/gateway/main.py
- bossman-core/bossman/llm.py

## Tests added
- bossman-core/tests/test_secrem_f008_gateway_failclosed.py
- bossman-core/tests/test_secrem_429_retry.py

## Original reproduction after fix
denied

## Adversarial variants
- header 'maybe'/'': denied
- stream header upper bound
- embeddings without header: denied

## Regression
bossman-core focused 247 passed; cost-governor tests migrated to explicit header

## Fresh external verification
pytest ASGI app with MockTransport counting upstream hits.

## Generalizable lessons
- Missing policy signal is a denial, never a default grant.
- Audit what actually happened (resolved route), not what was asked for (alias).

## Teach local model
- Recognize: headers.get(x, '1')
- Prefer: explicit truthy set; unknown → closed
