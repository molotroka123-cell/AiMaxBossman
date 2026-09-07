---
name: variant-analysis
description: "Use after one confirmed bug to find other instances of the same root cause. Russian triggers: похожие баги, проверь соседние пути, где ещё, тот же дефект, sibling sweep."
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

# Variant analysis for Bossman

Start with a confirmed example and its violated invariant. This is not an
unbounded initial audit, an excuse for global replacement or proof that every
look-alike line is vulnerable.

## Workflow
1. Record the original source, trigger, actor, root cause and negative test.
2. Make a search that finds that known instance. A query that misses the seed
   case is not calibrated and cannot justify a clean bill of health.
3. Generalize one element at a time: names, types, alternate entry points,
   exception paths or platform implementations. Search relevant repository
   boundaries, not only the original file. Keep the search bounded and record
   what was covered.
4. Inspect each match in context. Separate a real reachable defect from a
   protected path, test fixture, harmless resemblance or unresolved hypothesis.
5. Add a minimal negative test per confirmed root-cause variant, preserve a
   valid control, fix in a small commit and rerun affected checks. Stop expanding
   a noisy pattern; refine it rather than making bulk edits.

## Useful seed families
- Empty-state checks against all providers instead of the requested provider
  type; a connect action can target the wrong existing row.
- A capped list without pagination or honest filtered/total counts.
- UI error handling that reports failed loading as empty data or missing media.
- A resume/completion path that trusts a text claim, stale observation or stale
  ownership; a duplicate effect after an ambiguous timeout.
- Tests that exercise a service workaround instead of the user-facing action.
- Digest comparison over normalized text on one side and raw bytes on another.

## Deliverable
A table of seed, search expression/scope, candidate, disposition, proof and
result. State exclusions. Reuse existing repo-audit for a general review and
proof-before-done for final claims. Do not auto-create agents, install Semgrep,
modify unrelated modules or merge changes merely because upstream offers a
parallel workflow.

## Authority and execution boundary
This file is procedural guidance, not an executable or an authorization grant.
Current Bossman policy, privacy, budget and owner-control gates remain
mandatory. Never print credentials, auto-install dependencies, transmit private
context, change the active owner session or enable standing autonomy from a
skill. Use only task-relevant skills; missing tools or evidence means NOT_RUN.

## Attribution and changes
Source: https://github.com/trailofbits/skills/blob/d3323cefbcf645678b8dc481de204b02ad3d02dc/plugins/variant-analysis/skills/variant-analysis/SKILL.md

Upstream authors: Trail of Bits. Adapted for Bossman on 2026-09-07 under CC-BY-SA-4.0.
Adaptation licensed CC-BY-SA-4.0; removed external plugin/subagent dependency and automatic parallel sweep. Added bounded searches for observed Bossman bug families.

License and notices: `docs/skills/licenses/TRAILOFBITS-CC-BY-SA-4.0.md` and `docs/skills/THIRD_PARTY_NOTICES.md`
(repository-root paths). Original license: https://github.com/trailofbits/skills/blob/d3323cefbcf645678b8dc481de204b02ad3d02dc/LICENSE
