# Learning Case: F-004-http-ssrf

## Metadata
MODEL: claude-fable-5-1
AGENT: agent-B+lead
START_SHA: 3ec4c81d72b4930e1ac9006541ac7ebd8036ab6a
END_SHA: b0ed072
LEARNING_STATUS: VERIFIED
OUTCOME: FIXED
VERIFIED_BY: pytest:bossman-core/tests/test_secrem_f004_http_ssrf.py
CONFIDENCE: 0.85
TAGS: {"bug_class": "ssrf", "component": "bossman.toolkit.net", "domain": "security", "security_boundary": "egress", "severity": "MEDIUM"}
FINDINGS: F-004

## Task
http tool had no egress policy (SSRF)

## Symptom
http(url=...) fetched loopback/RFC1918/metadata/file:// targets and followed redirects.

## Reproduction
- bossman-core/tests/test_secrem_f004_http_ssrf.py::test_repro_blocked_targets_never_hit_network (14 targets)

## Evidence
- net.http passed args['url'] straight to httpx with default redirects (pre-fix)
- post-fix: MockTransport records zero requests for all blocked targets; public host fetched once

## Hypotheses considered
- reliance on network topology ('only LiteLLM sees outside')
- missing resolve-and-check of every A/AAAA record
- auto redirects bypass any pre-check

## Rejected hypotheses + why
- block only literal private IPs: hostname → private record and redirect-to-private bypass it

## Root cause
Tool trusted the model-supplied URL and the network perimeter instead of enforcing egress policy itself.

## Relevant code paths
- bossman-core/bossman/toolkit/net.py:check_url
- bossman-core/bossman/toolkit/net.py:_request_checked

## Fix strategy
Scheme allowlist, no userinfo, resolve all addresses and reject private/link-local/metadata, manual redirects with per-hop check (max 3), DNS failure fails closed, owner env allowlists, confirm_default=True.

## Alternatives considered
- httpx event hook on redirect (still resolves after connect)
- IP pinning transport (deferred; documented residual DNS-rebind risk)

## Why this fix was chosen
Deterministic pre-connect policy with explicit owner escape hatches; refusal returned as data so the loop continues.

## Files changed
- bossman-core/bossman/toolkit/net.py

## Tests added
- bossman-core/tests/test_secrem_f004_http_ssrf.py

## Original reproduction after fix
blocked

## Adversarial variants
- hostname with one private A record
- redirect to 127.0.0.1
- redirect loop bounded
- metadata host stays blocked under ALLOW_PRIVATE

## Regression
bossman-core focused 247 passed

## Fresh external verification
pytest with MockTransport proving no network call; DNS faked deterministically.

## Generalizable lessons
- Egress policy belongs in the tool, not in the assumed network shape.
- Check every resolved address and every redirect hop.

## Teach local model
- Recognize: httpx/requests call with a model-controlled URL
- Verify using: metadata IP, loopback, file://, redirect-to-private, multi-record DNS

## Limitations / follow-up
- DNS rebinding between check and connect not closed (no IP pinning).
