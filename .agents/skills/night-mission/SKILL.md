---
name: night-mission
description: Convert a long autonomous goal into a persistent overnight mission with checkpoints, bounded retries, resource and cloud budgets, review gates, and a clear morning handoff.
compatibility: BOSSMAN, OpenCode, Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
---

# Night Mission

Use when the user wants work to continue for hours while away.

## Mission config

Define:
- goal
- success criteria
- duration/end time
- max workers
- local model policy
- cloud fallback policy
- cloud budget
- checkpoint interval
- approval policy
- stop conditions

## Execution

1. Plan milestones.
2. Create persistent tasks with dependencies.
3. Save checkpoint before risky transitions.
4. Use local models by default.
5. Review important outputs with a different agent/model when possible.
6. If the same error repeats, replan instead of looping.
7. If user approval is required, mark waiting_approval and continue independent work when possible.
8. Persist task state outside any single chat/model session.

## Morning report

Return:
- completed items
- verified items
- failed/blocked items
- commits/artifacts
- cloud spend
- model/runtime failures
- approvals needed
- recommended next step
