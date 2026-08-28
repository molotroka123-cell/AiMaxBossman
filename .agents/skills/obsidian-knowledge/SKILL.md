---
name: obsidian-knowledge
description: Use the configured Obsidian vault as a read-mostly local knowledge base, with agent writes restricted to the BOSSMAN Memory folder.
compatibility: BOSSMAN, OpenCode, Claude Code
metadata:
  version: "1.0"
---

# Obsidian Knowledge

Obsidian markdown is human-owned source material.

Rules:
- search selected vault folders
- cite file + heading
- never edit arbitrary existing notes automatically
- write new AI memory only into configured BOSSMAN Memory folder
- excluded/private folders are never indexed
- do not assume all vault notes are current truth; consider dates and conflicts
