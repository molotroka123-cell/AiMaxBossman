---
name: failure-retrospective
description: Analyze a failed or repeatedly struggling task, identify the actual failure class, extract reusable lessons and propose bounded changes to routing, memory, skills or tooling.
compatibility: BOSSMAN, OpenCode, Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
  category: self-improvement
---

# Failure Retrospective

Use when:
- task fails
- same error repeats
- reviewer rejects repeatedly
- a tool/provider repeatedly misbehaves
- a mission consumes resources without progress
- human intervention was required unexpectedly

## Do not immediately create a new skill

First determine what actually failed.

Failure classes:

- missing knowledge/context
- wrong model selection
- bad skill/workflow
- insufficient permissions
- tool/runtime bug
- flaky external dependency
- acceptance criteria unclear
- resource exhaustion
- infinite/repeated reasoning loop
- unsafe action correctly blocked
- implementation bug
- reviewer/test problem

## Workflow

1. Collect the smallest relevant evidence:
   - task goal
   - last useful checkpoint
   - exact failure
   - relevant tool calls
   - reviewer result
   - retry history
2. Identify the earliest causal failure, not only the last visible error.
3. Decide whether the lesson belongs in:
   - memory
   - existing skill update
   - new skill
   - router score/model policy
   - tool/runtime backlog
   - permission policy
4. Propose the smallest change.
5. Define how to test whether the change improves behavior.
6. Never automatically weaken security permissions to "fix" a failure.

## Output

```text
Failure class:
Root cause:
Evidence:
Why retries did not help:
Reusable lesson:
Proposed change:
Expected benefit:
Regression risk:
Verification test:
```

If root cause is uncertain, say so.
