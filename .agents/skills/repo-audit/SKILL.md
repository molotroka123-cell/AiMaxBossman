---
name: repo-audit
description: Audit a software repository before changes: map architecture, identify real vs mock functionality, tests, risks, technical debt, and the smallest safe implementation path.
compatibility: BOSSMAN, OpenCode, Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
---

# Repository Audit

Use this skill before significant implementation work or when asked whether a feature "already works".

## Workflow

1. Read project instructions and the closest README files.
2. Inspect git status and current branch before making changes.
3. Map the minimum architecture:
   - entrypoints
   - API
   - persistence/database
   - queue/workers
   - model/provider layer
   - tools/permissions
   - frontend
   - tests
4. Search for the requested feature by:
   - entity/table names
   - API routes
   - UI labels
   - runtime implementation
   - tests
5. Classify every requested capability:
   - REAL: backend + persistence/runtime + usable UI or API + test evidence
   - PARTIAL: meaningful implementation exists but a required layer is missing
   - MOCK: UI/example/placeholder only
   - ABSENT
6. Never infer that a button works because it exists.
7. Prefer reading focused files instead of dumping the entire repo.
8. Record blockers and dependencies before proposing new architecture.
9. Recommend the smallest safe delta that reuses existing code.

## Output

Return:

- Current branch / commit
- Architecture summary
- Requested capabilities table: REAL / PARTIAL / MOCK / ABSENT
- Critical risks
- Reusable components
- Missing tests
- Recommended implementation order
- Files likely to change

Do not edit the repository unless the task explicitly asks for implementation.
