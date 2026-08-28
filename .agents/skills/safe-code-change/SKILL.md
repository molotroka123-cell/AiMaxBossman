---
name: safe-code-change
description: Implement a code change safely in an isolated branch/worktree, verify it with focused tests and browser QA when relevant, and prepare a clean commit without unsafe repository operations.
compatibility: BOSSMAN, OpenCode, Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
---

# Safe Code Change

Use for any non-trivial implementation.

## Before editing

1. Read project instructions.
2. `git status`.
3. Identify the current branch and remote.
4. Use an isolated branch/worktree for parallel agent work.
5. Do not overwrite unrelated user changes.
6. Define acceptance criteria before coding.

## During implementation

- Make the smallest coherent changes.
- Reuse existing abstractions.
- Keep secrets out of code/logs.
- Do not modify migrations concurrently with other agents without coordination.
- Run focused tests after each logical milestone.
- If UI changes, run the app and inspect real behavior rather than trusting static markup.

## Git policy

Allowed without extra approval inside an agent branch:
- status
- diff
- log
- add
- local commit

Ask before:
- push
- merge to protected/shared branch
- changing remote
- deleting a branch/worktree with unmerged changes

Deny:
- force push
- destructive reset of user work
- rewriting unrelated history
- committing secrets or model weights

## Definition of Done

A change is done only when:
- implementation exists
- relevant tests pass
- failure path was exercised where practical
- UI action calls real backend logic
- diff is reviewed
- no unrelated changes are included
- report names known limitations
