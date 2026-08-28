---
name: memory-recall
description: Retrieve only the relevant historical decisions, notes and project context from BOSSMAN/Obsidian memory using progressive local search.
compatibility: BOSSMAN, OpenCode, Claude Code
metadata:
  version: "1.0"
---

# Memory Recall

Use when the task may depend on prior decisions, project history, previous failures, conventions or user-approved memory.

Do not load whole memory directories.

Workflow:

1. `memory.search` with the current question.
2. Review top result summaries.
3. `memory.expand` only the strongest few if more context is needed.
4. Prefer current/explicit facts over old conflicting memories.
5. Include source citations in conclusions.
6. Keep injected context small.
7. If memory is ambiguous, say so rather than guessing.

Do not retrieve secrets or excluded paths.
