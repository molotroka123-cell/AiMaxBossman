---
name: context-builder
description: Assemble the smallest high-quality task context from memory, semantic code search, current files, tool results and user instructions while protecting the model context window from irrelevant material.
compatibility: BOSSMAN, OpenCode, Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
  category: context
---

# Context Builder

Use before difficult reasoning, coding, review or long autonomous work.

## Principle

More context is not automatically better context.

Optimize for:

`relevance × evidence quality × freshness / token cost`

## Sources

Possible sources:
- current user/task instruction
- active project instructions
- memory.search
- semantic code search
- exact relevant source files
- current git diff
- test failures
- tool outputs
- reviewer feedback

## Priority

1. Current explicit user instruction
2. Current task acceptance criteria
3. Current repository/project instructions
4. Relevant current source code
5. Verified recent memory
6. Relevant prior decisions
7. External/general background

## Workflow

1. Define the immediate decision or action.
2. Identify which sources can answer it.
3. Retrieve narrowly.
4. Deduplicate semantically overlapping evidence.
5. Discard low-value context.
6. Preserve citations/source paths.
7. Fit context to the model-specific budget.
8. Keep some headroom for reasoning and tool results.

## Token budgeting

Default suggested allocation:

- task/user intent: 10%
- project/repo instructions: 10%
- retrieved evidence/code: 45%
- recent tool results: 15%
- free reasoning/output headroom: 20%

These are guidelines, not hard constants.

## Progressive disclosure

Never open ten huge files when code search already identified two relevant functions.

Prefer:

`search → focused read → act → retrieve more only if needed`

## Compaction

When a long mission approaches context pressure:

1. write a compact checkpoint
2. preserve decisions, state, failures and next step
3. drop redundant raw history
4. rebuild context from checkpoint + current evidence

Do not depend on conversational history as the only state store.
