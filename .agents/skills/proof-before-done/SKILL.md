---
name: proof-before-done
description: Require concrete runtime evidence before an agent, reviewer or mission marks work complete, preventing UI-only, mock-only or self-reported success from becoming false DONE status.
compatibility: BOSSMAN, OpenCode, Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
  category: verification
---

# Proof Before Done

Use whenever an agent is about to claim a meaningful task is complete.

## Principle

"Implemented" is not proof that it works.

A completion claim requires evidence appropriate to the task.

## Evidence ladder

Possible evidence:

1. source exists
2. static validation passes
3. unit test passes
4. integration test passes
5. real API action works
6. persistence verified
7. browser/UI behavior observed
8. failure behavior verified
9. restart/recovery verified
10. real external provider/system tested

Not every task needs all ten.

But a task must use enough levels to support its claim.

## Examples

### UI feature
Insufficient:
- page file exists

Better:
- page loads
- real API call occurs
- expected state changes
- browser console clean
- mobile layout checked if required

### External integration
Insufficient:
- SDK imports
- mock server passes

Required before REAL DONE:
- actual external integration smoke test succeeded

If only mock passes:
mark external integration PARTIAL.

### Coding task
Require:
- relevant test
- diff inspection
- acceptance criteria check

## Workflow

1. Restate the completion claim.
2. Map each acceptance criterion to evidence.
3. Run/check evidence.
4. Identify unproven claims.
5. Classify:
   - DONE
   - PARTIAL
   - FAILED
6. Save proof artifact paths/log references.

## Never

- trust the implementing agent's "done" message alone
- call mock integration real
- hide skipped tests
- convert unavailable test into PASS

## Output

```text
Claim:
Acceptance criteria:
Evidence:
Skipped/unavailable verification:
Known limitation:
Final status: DONE / PARTIAL / FAILED
```
