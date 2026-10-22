---
name: north-star-tree-hygiene
description: Deterministic capability-tree and git worktree hygiene verification (reconciles HEAD SHA against tree-registry.json, enforces LF line endings, validates SKILL.md manifests, and runs dry-run receipt checks without fabricating green leaves).
license: MIT
compatibility: BOSSMAN, OpenCode, Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
  provenance: north-star-orchestra-20261023
required_tools: [code.status, terminal.run, memory.search]
permissions: [read, exec]
input_schema:
  type: object
  properties:
    repo_path:
      type: string
      description: Repository or worktree path to inspect
    strict_sha:
      type: boolean
      description: Require tree-registry.json head to match git HEAD
  required: [repo_path]
output_schema:
  type: object
  properties:
    verdict:
      type: string
      enum: [PASS, DRIFT_DETECTED, BLOCKED]
    head_sha:
      type: string
    registry_head_sha:
      type: string
    leaves_total:
      type: integer
    crlf_files:
      type: integer
  required: [verdict, head_sha, leaves_total]
---

# North Star Tree Hygiene (`north-star-tree-hygiene`)

Use this skill whenever the user or North Star Orchestrator asks to verify capability-tree synchronization, check 927-leaf registry alignment, audit git worktree/CRLF hygiene, or validate skill manifests before release.

## Triggers

- capability tree drift, `tree-registry.json`, or `927 leaves` verification
- checking `bossman_capability_scan.py`, `tree_registry_sync.py`, or `tree_apply_evidence.py`
- verifying exact git `HEAD` SHA alignment and LF line endings before a release checkpoint

## Deterministic Workflow

1. **Inspect Git & Worktree Status (`code.status`)**:
   - Check current `HEAD` SHA (`git rev-parse HEAD`) and working tree cleanliness.
2. **Verify Registry Alignment (`terminal.run`)**:
   - Read `docs/architecture/tree-registry.json` and compare `head` against current `HEAD` and leaf count (`927`).
   - Run `python tools/tree_apply_evidence.py --dry-run` to confirm deterministic receipt validation without mutating the seed.
3. **Validate Skill Manifests & Line Endings (`terminal.run`)**:
   - Validate `.agents/skills/*/SKILL.md` with `bcc.v2.skill_library.validate_manifest` and `bcc.v2.skill_catalog.scan_policy`.
   - Ensure zero CRLF contamination in staged files (`git diff --check`).
4. **Record & Recall Lessons (`memory.search`)**:
   - Cross-check prior self-improvement lessons so no leaf is ever marked green without a deterministic receipt.

## Safety & Permission Rules

- Never enable `bypass_permissions` (`AUTO`) from inside a skill.
- Never fabricate `ci` or `owner_pc` receipts; keep unverified levels as `NOT_RUN`.
