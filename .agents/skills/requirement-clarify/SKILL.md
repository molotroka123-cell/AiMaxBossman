---
name: requirement-clarify
description: Resolve an ambiguous requirement before any work starts, by separating what was actually stated from what is being assumed, and either asking one blocking question or proceeding under a written assumption.
compatibility: BOSSMAN, OpenCode, Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
  category: planning
---

# Requirement Clarify

Use before starting work whose scope, target or success condition can be read in
more than one way.

## Principle

A guessed requirement costs more than a question.

But a question asked for every uncertainty is its own failure: it stalls
autonomous work and pushes thinking back onto the owner. The skill exists to
separate the two cases, not to make asking the default.

## Ambiguity classes

Check the request against each. Most ambiguity is one of these:

1. **Scope** — which parts are in, which are explicitly out.
2. **Target** — which file, service, environment, repository, branch.
3. **Success condition** — what makes it done: a test, a number, an observed
   behavior, an owner's opinion.
4. **Data source of truth** — when two stores disagree, which one wins.
5. **Reversibility** — is any step hard to undo (deploy, send, delete, pay).
6. **Silent scale** — "all of them" over 5 items or over 5000 items.

## Decision rule

For each ambiguity found:

- Different readings lead to **materially different work** → it blocks.
- Different readings lead to the **same work** → it does not block; record the
  reading and move on.
- The step is **irreversible or outward-facing** → it always blocks, even if
  the readings are close. Never guess on send, deploy, delete, or pay.

## Workflow

1. Quote the request verbatim.
2. List what was actually stated. No inference in this list.
3. List what you are about to assume. Everything not in step 2 belongs here.
4. Classify each assumption against the ambiguity classes.
5. Apply the decision rule.
6. If nothing blocks: write the assumptions down, then do the whole task under
   them and surface them with the result.
7. If something blocks: do every part that does not depend on the answer first,
   then ask ONE question covering the blocking ambiguities, with the options you
   see and your recommendation.

## Never

- ask a question you can answer from the repository, the task history or memory
- ask several questions when one covers them
- stop all work because one branch is unclear
- proceed silently on an irreversible action
- record an assumption only in your own reasoning and not in the output

## Output

```text
Stated:
Assumed:
Blocking ambiguity (or: none):
Question (only if blocking, exactly one):
Recommended reading:
Proceeding with:
Deferred until answered:
```
