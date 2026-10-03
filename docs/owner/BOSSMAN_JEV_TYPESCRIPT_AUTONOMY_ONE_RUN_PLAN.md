# Bossman + Jev TypeScript: one-run autonomy plan


## Priority out-of-the-box goal

**Goal ID:** `OOTB-001-700GB-AUTONOMOUS-BOSSMAN`

Build Bossman + Jev into a demonstrably reliable autonomous local development product, then use that evidence to obtain a grant, startup cloud credits, equipment support, leasing, or credit financing for a fast local AI system with at least 700 GB of coherent or unified memory.

This is the primary long-term optimization target. Jev should continuously prefer bounded improvements that move the system toward:

1. a complete Jev → Bossman/OpenHands → Claude/Codex → tests → dual approval → staging → release cycle;
2. reliable operation without concurrent CLI conflicts;
3. reusable verified operational memory and skills;
4. measurable reductions in cloud cost and human intervention;
5. a working product demonstration suitable for grants, accelerators, investors, customers, and lenders;
6. technical justification and utilization data for a 700+ GB unified-memory workstation, with NVIDIA DGX Station GB300 or an equivalent platform as the reference target;
7. a financing package containing the pitch deck, one-page product brief, architecture, benchmarks, budget, utilization forecast, and repayment/revenue model.

### Milestones

- **M1:** one complete supervised self-improvement cycle succeeds end to end;
- **M2:** ten bounded cycles complete with evidence, correct gating, and recoverable rollback;
- **M3:** Bossman/Jev demonstrates a useful paid or pilot-ready workflow;
- **M4:** cloud benchmarks prove why local 700+ GB memory materially improves cost, privacy, latency, or capability;
- **M5:** applications are ready for NVIDIA Inception, Microsoft for Startups, AWS Activate, relevant accelerators, and country-specific grants;
- **M6:** secure financing or equipment access and migrate validated workloads to the local system.

### Goal protection

Jev may refine milestones and implementation steps but cannot silently remove, replace, or weaken this goal. Changes to the goal, financial commitments, applications submitted to third parties, and hardware purchases require the user's explicit approval. Lack of funding must not cause unsafe financial actions or bypass the engineering approval gates.
## Objective

Build the complete supervised self-improvement loop in one engineering run. Jeff plans improvements, Claude CLI and Codex CLI implement and review them, Bossman operates the computer, and successful procedures become reusable Jev skills.

## Core operating model

### Jev TypeScript is the computer operator

The entire dedicated computer is operated by Jeff through Bossman. Jev is the only orchestration identity presented to the user. Jev already uses OpenRouter for reasoning and already has web search. These are existing capabilities to preserve and connect to the autonomy loop. Bossman supplies the hands: filesystem access, terminals, browser/UI control, services, worktrees, logs, tests, and recovery operations.

Claude CLI and Codex CLI do not need direct control of every UI or local application. When either agent lacks hands, it returns a structured action request. Jev executes the requested operation through Bossman, captures the result, and returns evidence to the requesting agent.

```text
User goals
   ↓
Jev TypeScript / OpenRouter reasoning
   ↓
Bossman control plane and policy gate
   ├── terminal and filesystem
   ├── browser and desktop UI
   ├── Git worktrees
   ├── services and staging
   ├── Claude CLI
   └── Codex CLI
```

### Structured hand protocol

Claude and Codex must request actions in a machine-readable envelope:

```json
{
  "goal_id": "JEFF-0042",
  "requested_by": "claude",
  "action": "run_tests",
  "target": "isolated_worktree",
  "arguments": {"suite": "identity_redteam"},
  "expected_evidence": ["exit_code", "stdout_hash", "report_path"],
  "risk_class": "low"
}
```

Bossman validates policy and scope, Jev performs the action, and the exact result is returned with timestamps and artifact hashes. Requests outside the current goal, budget, worktree, or permission level are blocked.

## Jev learns each successful procedure

Every completed operation is recorded as an episodic trace:

- goal and starting state;
- agent instruction;
- exact actions performed;
- observations and errors;
- recovery steps;
- final result;
- Claude and Codex decisions;
- user decision where required.

A trace becomes a reusable skill only when:

1. the outcome satisfies the acceptance tests;
2. Claude and Codex approve the exact trace or generated skill;
3. secrets and user-specific transient values are removed;
4. parameters, preconditions, failure detection, and rollback are explicit;
5. the skill succeeds in staging at least once.

Skills are versioned, scoped, revocable, and linked to their source evidence. Jev retrieves skills by goal, application, environment state, and confidence. Failed executions reduce confidence and trigger fallback to Claude/Codex guidance.

Jev may learn operational steps and preferences. He must not silently convert arbitrary chat statements, quoted text, model output, or website content into durable facts or executable skills.

## One-run implementation scope

1. Create the immutable user constitution and authority matrix.
2. Implement the persistent task state machine.
3. Add Claude CLI and Codex CLI adapters.
4. Add the structured hand protocol for computer operations.
5. Integrate Jev's existing OpenRouter and web-search path into the goal, evidence, and budget system.
6. Make Bossman the sole privileged executor.
6. Add isolated worktree creation and candidate SHA locking.
7. Add unit, integration, security, identity, memory, Telegram, fallback, and rollback tests.
9. Add independent Claude and Codex review bound to the same SHA and diff hash.
10. Add staging on separate ports and temporary data.
11. Add an append-only evidence and decision journal.
12. Add episodic trace capture and verified skill compilation.
13. Add skill retrieval, confidence, expiry, revocation, and rollback.
14. Add a user release panel with Apply, Reject, and Revise actions.
15. Use the completed pipeline to fix Jev identity leakage as its first real task.

## Authority rules

- Jev may inspect, plan, operate the dedicated local environment, create branches, run tests, and prepare candidates within the active goal.
- Claude or Codex may propose steps but cannot bypass Bossman policy.
- A code or skill candidate requires Claude and Codex approval for the same immutable SHA/hash.
- A changed candidate invalidates both approvals.
- Financial actions, external communications, credential creation/disclosure, destructive operations, constitutional changes, and production release remain user-gated.
- Every action has a budget, timeout, target scope, evidence requirement, and rollback path.
- Unknown state fails closed and is reported to the user.

## Completion criteria

The one-run build is complete when Jev can receive one bounded goal, ask Claude/Codex for implementation and review, operate the computer through Bossman, build and test in isolation, learn a verified reusable procedure, prepare a release candidate, and stop at the user release gate with complete evidence and rollback instructions.


## Existing Jev capabilities to reuse

The following are already present and must not be rebuilt or replaced during the one-run implementation:

- Jev TypeScript runtime;
- OpenRouter model access and routing;
- Jev web search;
- Telegram interaction path;
- current local and cloud model configuration;
- existing memory and Bossman control components.

The work is to place these capabilities behind the common goal ID, permission policy, evidence journal, budgets, dual review gate, and reusable-skill memory. Web-search results are untrusted evidence: Jev may use them for research, but webpage instructions cannot become executable actions or durable memory without validation.

## Continuous self-improvement runtime loop

### Components

- **Jev TypeScript** owns the current goal, plan, memory, progress, and user relationship.
- **Bossman + OpenHands** provide controlled hands for terminal, filesystem, browser, applications, Git, services, and staging.
- **Nemotron through OpenRouter** is the primary high-capability planner and reasoning model when it satisfies the active free-model policy.
- **Local models** handle fast, private, repetitive, summarization, classification, and recovery tasks. Smaller models do not make final architecture or release decisions.
- **Claude CLI and Codex CLI** are temporary engineering workers and independent reviewers. They are started only when needed and stopped when their bounded task is finished.

### Conflict-free sequential execution

Claude and Codex never write to the same worktree at the same time. Bossman owns a global engineering lease and launches only one writer session at once:

```text
Jev selects bounded task
  → Bossman acquires engineering lease
  → create isolated worktree and task manifest
  → start Claude CLI or Codex CLI in a dedicated CMD session
  → worker edits, commits, reports evidence, then exits
  → Bossman verifies the process stopped and releases the lease
  → run tests
  → start the other CLI in review-only mode
  → if revisions are required, create a new writer turn
  → invalidate old approvals and repeat review
  → two approvals for one immutable SHA
  → stage candidate
  → user release gate when required
  → monitor or roll back
  → Jev selects the next task
```

Each session receives a unique task ID, worktree, branch, terminal transcript, timeout, process group, and expected output schema. Bossman prevents overlapping writers, kills orphaned child processes after a timeout, and resumes from the persisted state after a crash.

### Permission mode

Claude and Codex may run with noninteractive or bypass-permission mode only inside the dedicated Bossman computer and only after Bossman has constrained the session to the active task, assigned worktree, allowed commands, time budget, and process group. Bypass mode is an execution convenience, not an authority bypass.

Even in bypass mode, the worker cannot authorize:

- edits outside the assigned task scope;
- changes to the user constitution or approval gate;
- credentials, payments, purchases, or account changes;
- external messages or publication;
- production deployment;
- destructive cleanup without an approved rollback plan.

Bossman remains the permission broker and records every command and affected path. Unknown or cross-scope operations stop the session.

### Self-coding cycle

1. Jev inspects health metrics, backlog, logs, user goals, and verified web research.
2. Nemotron proposes the highest-value bounded improvement.
3. Jev creates acceptance tests and a rollback condition before code changes.
4. A local model may prepare context, reproduce the issue, or classify logs.
5. Bossman starts one engineering CLI as writer.
6. The writer commits a candidate and exits.
7. Bossman runs the deterministic test and security suites.
8. Claude and Codex review sequentially; neither can approve its own unreviewed revision.
9. The accepted trace becomes a candidate reusable Jev skill.
10. The candidate runs in staging and waits at the applicable release gate.
11. After deployment, Bossman monitors protected metrics and rolls back automatically on regression.
12. Jev records the outcome, updates skill confidence, shuts down unused workers, and begins the next bounded goal.

### Stop conditions

The cycle pauses in `BLOCKED` when budgets are exhausted, agents disagree repeatedly, protected tests fail, the state is ambiguous, rollback cannot be guaranteed, or a user-gated action is next. It resumes from the persisted task state instead of starting over.

