# Learning Case: SWEEP-sibling-boundary-gaps

## Metadata
MODEL: claude-fable-5-1
AGENT: fable-lead
START_SHA: f43f8fb
END_SHA: HEAD+1
LEARNING_STATUS: VERIFIED
OUTCOME: FIXED
VERIFIED_BY: pytest:command-center/tests/test_secrem_sibling_sweep.py, pytest:bossman-core/tests/test_secrem_sibling_sweep.py
CONFIDENCE: 0.85
TAGS: {"domain": "security", "bug_class": "ssrf", "component": "cross-boundary", "severity": "MEDIUM", "security_boundary": "egress"}
FINDINGS: F-003, F-004, F-010, F-017

## Task
Extract SECREM boundary mutators into shared libraries and sweep every component sharing a boundary (intelligence idea F8.4)

## Symptom
Each SSRF/path fix carried its own hand-written variant table; components sharing the boundary were never checked with the same counterexamples.

## Reproduction
- command-center/tests/test_secrem_sibling_sweep.py (egress: browser/discovery/plugins; path: task_exchange; text: MCP sanitizer)
- bossman-core/tests/test_secrem_sibling_sweep.py (egress: http tool; path: fs.*/media)

## Evidence
- first run: plugin_security.validate_url accepted metadata.google.internal and numeric IPv4 forms 2130706433/0x7f000001/127.1 (4 failures)
- first run: media._path_arg_ok('..') returned True — bare '..' resolves to the workdir parent (F-003 residue)
- after fixes: cc sweep 43 passed; core sweep + test_tools 50 passed

## Hypotheses considered
- each component implemented its own literal parser (root cause)
- tests written per-finding, not per-boundary

## Rejected hypotheses + why
- the gaps were exploitable only with DNS: numeric IPv4 forms are literal, no DNS needed

## Root cause
Boundary checks were duplicated per component with different coverage; nothing enforced parity.

## Relevant code paths
- command-center/bcc/plugin_security.py:validate_url
- command-center/bcc/plugin_security.py:_literal_ip
- bossman-core/bossman/toolkit/media.py:_path_arg_ok
- command-center/tests/_secrem/mutators.py
- bossman-core/tests/_secrem/mutators.py

## Fix strategy
Shared mutator catalogue (EGRESS_ALWAYS_BLOCKED, EGRESS_PRIVATE, PATH_TRAVERSAL_SEGMENTS, path_escapes(), INJECTION_STRINGS) + one parametrized sweep per app; close the two gaps found (metadata hostnames + inet_aton literal parsing in validate_url; component-based '..' check in media).

## Alternatives considered
- single shared policy module across both apps (apps share no code by design)

## Why this fix was chosen
Parity is enforced by tests, not by hoping each component copies the others; adding a component means adding one line to the sweep.

## Files changed
- command-center/bcc/plugin_security.py
- bossman-core/bossman/toolkit/media.py

## Tests added
- command-center/tests/test_secrem_sibling_sweep.py
- bossman-core/tests/test_secrem_sibling_sweep.py
- tests/_secrem/mutators.py (both apps)

## Original reproduction after fix
4 + 2 sweep failures → 0 after fixes

## Adversarial variants
- userinfo-disguised host example.com@169.254.169.254
- IPv4-mapped IPv6
- javascript: scheme
- nested ../ through own name
- symlink file and dir escapes
- bidi/zero-width/ANSI injections

## Regression
cc plugin/browser/discovery + sweep: 117 passed (2 pre-existing order-dependent failures in test_plugin_security.py when run in isolation, identical on base); core test_tools + sweep 50 passed

## Fresh external verification
pytest, literal checks without DNS; fresh symlinks created on disk per test

## Failed approaches / recovery lessons
- Run new sweeps on the base commit too (git stash) before attributing failures to your change

## Generalizable lessons
- A boundary fix is incomplete until every sibling component passes the same counterexamples
- Numeric IPv4 forms (decimal/hex/short) bypass ipaddress.ip_address; parse with inet_aton

## Teach local model
- Recognize: two components parsing URLs/paths with different helper functions
- Prefer: one mutator catalogue + parametrized sweep
- Verify using: 2130706433, 0x7f000001, 127.1, bare '..'

## Limitations / follow-up
- sweeps are literal (no DNS); resolved-address parity covered per component in their own tests
