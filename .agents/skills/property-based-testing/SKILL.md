---
name: property-based-testing
description: "Use for invariants across parsers, normalizers, time arithmetic, serializers, ledgers or state transitions. Russian triggers: граничные случаи, инвариант, генеративный тест, Hypothesis, roundtrip."
license: CC-BY-SA-4.0
compatibility: BOSSMAN portable agent skills; instructions only
metadata:
  owner: bossman
  version: "1.0.0"
  adaptation: "Bossman-specific, modified from upstream; not the full upstream package"
  upstream: "trailofbits/skills"
  upstream_commit: "d3323cefbcf645678b8dc481de204b02ad3d02dc"
  release_scope: "freeze"
---

# Property-based testing for Bossman

## Choose a real property
Look for a roundtrip, idempotence, invariant, independently checkable result or
independent oracle. State the domain and assumptions. A restatement of the
implementation or an assertion that only says no exception is weak evidence.
Use existing property-test tooling; a new dependency requires an explicit
project decision. Bounded deterministic generated cases are useful without
claiming that they cover the whole domain.

## High-value targets
- Normalize an endpoint twice with the same result; preserve explicit valid
  versions and refuse inputs outside the accepted domain.
- Serialize/deserialize a valid state without losing identity or authority.
- Preserve rational frame boundaries across trim/range/Undo/Redo. A tolerance
  must come from source properties, not a wider sequence rate.
- Bind evidence to the correct objective/revision/expected value; spending it
  twice or after a restart must not produce a second authorized effect.
- Maintain budget and lease invariants under bounded operation sequences;
  stale owners/fences and out-of-order events cannot finalize foreign work.
- Hash source bytes consistently, including newline and Unicode edge cases.

Generate valid and invalid inputs deliberately, including empty, boundary and
malformed values. Do not discard most samples with filters or ignore all
exceptions. Keep domains bounded for CI and preserve the seed/settings.

## On a failure
Shrink to a minimal counterexample. Check the asserted property before blaming
the implementation; some operations intentionally lose information or require
scope. Store the minimized case as a named regression, fix narrowly, and rerun
the generated suite and nearby examples. Test a known bad variant when possible
to prove the property is not vacuous.

## Deliverable
Property and assumptions, generator bounds, example count/seed, minimized
counterexamples, actual tested code and results. Pure generated tests are not
live Windows, browser, private-egress or model-retention certification.

## Authority and execution boundary
This file is procedural guidance, not an executable or an authorization grant.
Current Bossman policy, privacy, budget and owner-control gates remain
mandatory. Never print credentials, auto-install dependencies, transmit private
context, change the active owner session or enable standing autonomy from a
skill. Use only task-relevant skills; missing tools or evidence means NOT_RUN.

## Attribution and changes
Source: https://github.com/trailofbits/skills/blob/d3323cefbcf645678b8dc481de204b02ad3d02dc/plugins/property-based-testing/skills/property-based-testing/SKILL.md

Upstream authors: Trail of Bits. Adapted for Bossman on 2026-09-07 under CC-BY-SA-4.0.
Adaptation licensed CC-BY-SA-4.0; self-contained workflow without missing reference files or automatic library installation. Added Bossman-specific invariant targets.

License and notices: `docs/skills/licenses/TRAILOFBITS-CC-BY-SA-4.0.md` and `docs/skills/THIRD_PARTY_NOTICES.md`
(repository-root paths). Original license: https://github.com/trailofbits/skills/blob/d3323cefbcf645678b8dc481de204b02ad3d02dc/LICENSE
