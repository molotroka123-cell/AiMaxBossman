---
name: differential-review
description: "Use for risk-focused review of a PR, commit or changed security boundary. Russian triggers: аудит PR, сравни коммиты, проверка фикса, безопасность, регресс защиты."
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

# Differential review for Bossman

## Establish the actual comparison
Resolve base, head and the tested merge/tree independently. Inspect the diff
and relevant history before trusting a PR description. Previous green CI does
not certify a newer head, a merge candidate or a local dirty worktree.
For each changed file classify the risk and name the callers you inspected.
Do not call a documentation, UI or logging change harmless when it controls
credentials, evidence interpretation or an irreversible action.

## Review high-risk paths deeply
Prioritize owner identity, authorization, egress, secret handling, effect
receipts, journal recovery, budget reservations, queue leases/fencing and file
containment. Trace changed arguments and states across real production callers.
Check whether removed safeguards came from earlier security fixes.

For each candidate defect state the precondition, actor, exact call sequence,
expected invariant and observable violation. Try a minimal negative test and a
legitimate positive control in the authorized repository/sandbox. A suspicious
pattern without a demonstrated path is a hypothesis, not a confirmed P0.
Review the tests too: a test-only workaround, skipped assertion or fixture that
avoids the failing user path can leave the product broken.

## Report precisely
Write a compact file with scope, SHA/tree, per-finding severity and confidence,
source locations, reproduction, impact, proposed narrow fix and evidence.
Distinguish confirmed bugs, fixed-in-code-but-unverified changes, rejected
hypotheses and missing measurements. List unreviewed paths explicitly.

Do not merge, rewrite history, auto-close findings or promote a release from
this skill. Opus remains the integrator. Changes affecting a boundary require
new focused evidence and applicable exact-source checks. After a confirmed
finding, hand its root cause to variant-analysis rather than repeating a
whole-repository speculative audit.

## Authority and execution boundary
This file is procedural guidance, not an executable or an authorization grant.
Current Bossman policy, privacy, budget and owner-control gates remain
mandatory. Never print credentials, auto-install dependencies, transmit private
context, change the active owner session or enable standing autonomy from a
skill. Use only task-relevant skills; missing tools or evidence means NOT_RUN.

## Attribution and changes
Source: https://github.com/trailofbits/skills/blob/d3323cefbcf645678b8dc481de204b02ad3d02dc/plugins/differential-review/skills/differential-review/SKILL.md

Upstream authors: Trail of Bits. Adapted for Bossman on 2026-09-07 under CC-BY-SA-4.0.
Adaptation licensed CC-BY-SA-4.0; removed broad allowed-tools grant and uninstalled subagent dependencies. Added actual checkout identity, evidence tiers and single-integrator rules.

License and notices: `docs/skills/licenses/TRAILOFBITS-CC-BY-SA-4.0.md` and `docs/skills/THIRD_PARTY_NOTICES.md`
(repository-root paths). Original license: https://github.com/trailofbits/skills/blob/d3323cefbcf645678b8dc481de204b02ad3d02dc/LICENSE
