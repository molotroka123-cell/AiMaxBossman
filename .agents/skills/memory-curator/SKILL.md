---
name: memory-curator
description: Convert completed work into concise durable memory containing decisions, lessons, successful procedures, failures worth remembering and next actions without storing raw transcript noise.
compatibility: BOSSMAN, OpenCode, Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.1"
  supersedes: "1.0 (obsidian memory addon)"
  category: memory
---

# Memory Curator

Use after significant work, especially:
- completed missions
- architecture changes
- difficult bugs
- expensive experiments
- model/provider evaluations
- repeated failures
- user-approved workflow decisions

## Goal

Store the durable value of a session, not the whole session.

## Capture

### DECISIONS
What was decided and why.

### WORKED
Approaches, commands, models or tools that actually succeeded.

### FAILED
Approaches that failed and why, when the lesson is reusable.

### CONVENTIONS
Project rules future agents should respect.

### EVIDENCE
Tests, commits, metrics, artifacts or sources supporting the conclusion.

### NEXT
Remaining blockers and exact next action.

## Never store

- API keys
- passwords
- private keys
- wallet seed phrases
- raw credential files
- unnecessary personal data
- entire chat transcripts
- giant logs
- temporary debugging noise

## Memory quality test

Before writing, ask:

> Would this information save a future agent time or prevent a real mistake?

If no, omit it.

## Write policy

Use canonical `memory.write`.

Default AI write destination:
`BOSSMAN Memory/`

Do not overwrite arbitrary human-authored Obsidian notes.

## Suggested durable note format

```markdown
# <Project / Subject>

## Decision
...

## Evidence
...

## Worked
...

## Failed
...

## Convention
...

## Next
...
```

After writing, ensure the note becomes searchable.
