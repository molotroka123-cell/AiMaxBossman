---
name: memory-curator
description: Turn a completed work session into a concise durable markdown memory containing decisions, lessons, outcomes and unfinished work without dumping raw chat.
compatibility: BOSSMAN, OpenCode, Claude Code
metadata:
  version: "1.0"
---

# Memory Curator

At the end of significant work, save durable value rather than raw transcript.

Capture:
- decisions and why
- architecture changes
- successful commands/workflows
- failed approaches worth avoiding
- important file/branch/commit references
- unresolved blockers
- next action

Do not save:
- API keys
- passwords
- wallet data
- sensitive raw content
- repetitive conversation
- temporary debugging noise

Write through `memory.write` into the configured BOSSMAN-owned memory folder.
