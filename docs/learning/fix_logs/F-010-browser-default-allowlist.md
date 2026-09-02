# Learning Case: F-010-browser-default-allowlist

## Metadata
MODEL: claude-fable-5-1
AGENT: agent-A2+lead
START_SHA: 3ec4c81d72b4930e1ac9006541ac7ebd8036ab6a
END_SHA: 4311621
LEARNING_STATUS: VERIFIED
OUTCOME: FIXED
VERIFIED_BY: pytest:command-center/tests/test_secrem_browser_policy.py
CONFIDENCE: 0.85
TAGS: {"domain": "security", "bug_class": "ssrf", "component": "bcc.v2.browser_control", "severity": "MEDIUM", "security_boundary": "egress"}
FINDINGS: F-010

## Task
browser navigation had no default target policy

## Symptom
BrowserPolicy.from_dict({}) returned auto for http://169.254.169.254/, loopback, RFC1918, file://, user:pw@host.

## Reproduction
- command-center/tests/test_secrem_browser_policy.py::test_repro_default_policy_denies_private_and_metadata_targets

## Evidence
- domain_allowed returned True on an empty allowlist (pre-fix)
- post-fix: all 13 private/metadata/non-http targets deny; decimal/hex/short IPv4 literals refused without DNS; hostname with one private A record refused; BrowserManager.navigate raises before goto and re-checks the landed URL

## Hypotheses considered
- empty allowlist meant 'anything' (root cause)
- no notion of always-forbidden targets

## Rejected hypotheses + why
- reuse plugin_security.validate_url directly (raises a different exception type, drags httpx into a light module, lacks numeric IPv4 forms)

## Root cause
Policy expressed only as an owner allowlist; the absence of a list was treated as permission.

## Relevant code paths
- command-center/bcc/v2/browser_control.py:BrowserPolicy.navigation_refusal
- command-center/bcc/v2/browser_control.py:target_refusal
- command-center/bcc/v2/browser_control.py:BrowserManager.navigate

## Fix strategy
Literal target refusal (scheme, userinfo, non-global IPs incl. inet_aton forms, blocked hostname suffixes) before any list; DNS refusal if any record is non-public; owner override BCC_BROWSER_ALLOW_PRIVATE=1 for local dev; post-goto URL re-check.

## Alternatives considered
- DNS check on every click (slow; literal checks suffice for interactions)

## Why this fix was chosen
Default-deny with a single deliberate owner switch; existing loopback-server tests opt in via the flag.

## Files changed
- command-center/bcc/v2/browser_control.py

## Tests added
- command-center/tests/test_secrem_browser_policy.py

## Original reproduction after fix
denied

## Adversarial variants
- 2130706433
- 0x7f000001
- 127.1
- ::ffff:127.0.0.1
- fc00::1
- mixed public+private records
- NXDOMAIN
- javascript:
- example.com@127.0.0.1

## Regression
browser suites 41 passed with the override flag in their fixtures

## Fresh external verification
pytest with fake page proving goto never called; DNS faked deterministically.

## Generalizable lessons
- An empty allowlist is not 'allow all' — encode the default explicitly.

## Teach local model
- Recognize: `if not allowlist: return True`
- Prefer: forbidden-target check that precedes any list logic
- Verify using: metadata IP, numeric IPv4 forms, redirect landing on private

## Limitations / follow-up
- In-page sub-requests (XHR/iframes/img) not filtered — needs Playwright request interception; TTL-0 rebinding mid-page not covered.
