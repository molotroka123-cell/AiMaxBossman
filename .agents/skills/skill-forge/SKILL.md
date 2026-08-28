---
name: skill-forge
description: Create or improve reusable agent skills from repeated successful workflows and explicit user requirements, keeping them portable across BOSSMAN, OpenCode and Claude-compatible skill directories.
compatibility: BOSSMAN, OpenCode, Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
---

# Skill Forge

Use when a conversation or completed task reveals a reusable workflow.

## Do not create a skill for everything

Create/update a skill when at least one is true:
- the workflow is likely to repeat
- the user explicitly asks to remember a procedure as a skill
- several agents need identical rules
- a failure exposed a missing reusable checklist
- a stable tool sequence reduces cost/errors

Do not encode:
- one-time facts
- temporary secrets
- volatile credentials
- private data that belongs in project memory
- huge transcripts

## Portable location

Preferred canonical location:

`.agents/skills/<skill-id>/SKILL.md`

OpenCode can discover this location, while BOSSMAN should also scan it.

Optionally mirror/import:
- `.opencode/skills`
- `.claude/skills`
- `~/.config/opencode/skills`
- `~/.claude/skills`
- `~/.agents/skills`

Do not scan the entire computer. Configure explicit skill source roots.

## Frontmatter

Keep portable fields:
- name
- description
- compatibility
- metadata

Use lowercase kebab-case directory IDs.

## Workflow

1. Identify the repeated goal.
2. Extract stable rules from the conversation/task.
3. Separate instructions from facts.
4. Define triggers: when the agent should use this skill.
5. Define required tools/permissions in the body or BOSSMAN sidecar metadata.
6. Define a deterministic workflow.
7. Define output/acceptance criteria.
8. Add tests/fixtures when the skill drives code or external actions.
9. Version the skill.
10. Record provenance: which task/run motivated the update.

## Update policy

Prefer improving an existing skill over creating near-duplicates.

Never silently weaken permission or safety rules when updating a skill.

The Skill Library UI should show:
- current version
- diff from previous version
- agents using it
- source
- last successful runs
- proposed update awaiting approval if it changes permissions
