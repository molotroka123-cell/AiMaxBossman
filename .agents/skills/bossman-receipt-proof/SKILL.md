---
name: bossman-receipt-proof
description: Rules for turning a capability-tree leaf green only from deterministic receipts (tools/tree_apply_evidence.py). Use when applying evidence, retiring dead leaves, restamping shas, or editing leaf sources and details in capability_tree_seed.json.
compatibility: BOSSMAN, Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
  category: verification
  learned_from: green-leaves run 2026-10-06
---

# Receipt proof for capability-tree leaves

A leaf is green only because a machine-checkable receipt says so. Prose, a lane's own claim or a loaded SKILL.md never turns a leaf green.

## A receipt counts only if all hold
- Verdict is `PASS` and the command exit code is `0`.
- The recorded sha is an ancestor of `HEAD` (`git merge-base --is-ancestor <sha> HEAD`).
- The receipt carries the sha256 of the captured output, and that output file still hashes to it.
- Apply with `tools/tree_apply_evidence.py`; never hand-edit a status to green.

## What is NOT green
- Gate-only receipts (a policy or scan gate passed, nothing ran the capability).
- Skill-load receipts (the SKILL.md parses and loads). The leaf stays `prepared` or `recorded`.
- A module PASS means: it imports and the tests that mention it pass. It is not semantic certification. Word the detail that way.

## Authored tests
- If a lane wrote the test that proves the leaf, flag it `authored_by_lane`. Self-written proof is weaker and must be visible.
- A test that cannot fail on the old code proves nothing; show it failing first.

## Proven-dead leaves
- When a leaf points at code that no longer exists or never will, issue a RETIRE receipt with the evidence (grep result, removal commit). Do not leave it red forever or fake it green.

## Orphan shas
- A receipt whose sha is no longer an ancestor of HEAD (rewritten or merged-away branch) may be restamped to a current sha only after proving the product code is tree-identical: `git diff --stat <old> <new> -- <code paths>` is empty. If it is not empty, re-run the check instead.

## Seed data shape
- `sources` entries are objects `{path, branch, sha, kind}`. Never plain strings; strings break readers and the audit.
- A green leaf must not keep boilerplate such as "not verified" or "SKILL.md found, not wired" in `detail`. Run `refresh_detail` so the text matches the status.
- After any seed edit run `test_capability_tree.py` and `test_tree_apply_evidence.py`.

## Report honestly
- State counts as `green / prepared / recorded / retired`; list what was left unproven and why.
