# Bossman Durable Memory — Always-Remember Contract

Date: 2026-09-22  
Status: future implementation contract; do not claim complete before owner-hardware proof.

## Current architecture we must preserve

Bossman already has four useful layers:

1. **Canonical long-term notes** — `bcc/v2/memory/obsidian.py`. Bossman is the single writer to `BOSSMAN Memory/`; Markdown is the human-readable source of truth. Writes are atomic, path-contained and never silently overwrite an existing note.
2. **Structured temporal facts** — `bcc/v2/memory/facts.py`. Facts have validity/knowledge time and are superseded instead of rewriting history.
3. **Derived retrieval indexes** — SQLite/JSON/BM25 and the memory service. They are rebuildable and are not authoritative.
4. **Verified learning** — `bossman/apprentice/*` + LearningStore + `learning_guard/*`. Episodes/lessons/skills are sanitized, independently verified, shadow/A-B gated and rollback-aware.

Important current limitation: `tools_memory.py` deliberately does **not** inject memory into every model call. Recall happens when `memory.search` is used. Also autonomy/cognitive-reuse paths are flag-gated and OFF by default. Therefore durable storage exists, but "always remembers" is not yet an end-to-end product guarantee.

## What "always remembers" must mean

Not "put the whole vault into every prompt".

It means: at important lifecycle boundaries Bossman automatically retrieves the smallest relevant verified context before planning/resuming/acting, with provenance, scope and freshness.

Memory is evidence, never authority. Current owner instruction, policy and approval always outrank memory.

## Future Memory Lifecycle Orchestrator

Build one orchestration layer over the existing stores. **Do not create another memory database.**

### BOOT
- validate vault, facts DB and LearningStore;
- detect stale/corrupt derived indexes and rebuild them;
- report MEMORY HEALTHY / DEGRADED / READ_ONLY / NOT_CONFIGURED;
- load only compact registry metadata, not full memory.

### TASK_START / BEFORE_PLAN
For existing/nontrivial work automatically retrieve:
- current project decisions;
- current temporal facts;
- VERIFIED lessons/skills and negative lessons;
- previous verified failures and next action;
- relevant evidence/provenance.

Build one bounded context pack using progressive disclosure.

### RESUME
After restart/crash retrieve durable checkpoint, completed effects, uncertain effects requiring reconciliation, project decisions and applicable verified skills. Never rely on chat history alone.

### BEFORE_SIDE_EFFECT
Refresh only relevant decision/policy context. Memory can inform the plan but can never grant permission or replace a fresh approval.

### AFTER_VERIFIED_RESULT
Run Memory Curator and retain only durable value:
DECISION / WORKED / FAILED / CONVENTION / EVIDENCE / NEXT.

### AFTER_VERIFIED_LEARNING
Store the coaching episode/lesson/skill through LearningStore. A small Markdown pointer/summary may be written by the canonical Bossman writer, but must not become a second authoritative copy of the skill.

### CHECKPOINT / SHUTDOWN
Persist next action and task state before controlled shutdown.

## Retrieval order

1. current owner instruction + current policy (not memory);
2. current task/project state and explicit decisions;
3. non-expired structured facts;
4. VERIFIED applicable skills and negative lessons;
5. recent project-specific notes with evidence;
6. older/general notes.

Never silently resolve conflicting memories. Compare supersession, validity time, evidence and scope; expose unresolved conflict.

## Required scope/provenance

Every durable learning record should carry:
owner/principal, project, task class, environment, app/version where relevant, model/runtime, created/valid/expired/superseded timestamps, evidence references, privacy class and verification state.

Default retrieval uses the narrowest scope. Cross-project reuse must be explicit and labeled.

## Learning states

Use explicit states:
RAW_OBSERVATION -> CANDIDATE_LESSON -> VERIFIED_LESSON -> SHADOW_SKILL -> READY_SKILL.

Also support DEGRADED / QUARANTINED / SUPERSEDED / EXPIRED.

Only verified and currently applicable records may guide autonomous planning.

Negative lessons are first-class memory.

## Security

Retrieved notes are untrusted data. A note containing "ignore owner and do X" is not an instruction.

Memory/skills can never:
- approve an action;
- grant permissions;
- raise budgets;
- enable cloud fallback;
- weaken LOCAL_ONLY/security policy.

Do not store secrets, raw prompts, hidden reasoning, cookies or unnecessary personal data.

## Remember-forever health and recovery

Add tests/telemetry for:
- canonical atomic writes;
- index freshness/rebuild;
- LearningStore version/tombstone integrity;
- facts temporal integrity;
- restart retrieval;
- project isolation;
- corrupt-tail tolerance;
- no secret/hidden-reasoning leakage;
- backup/restore equivalence.

Back up canonical Markdown, LearningStore and facts DB consistently with hashes/manifests. Derived indexes may be rebuilt. Never restore an older derived index over newer canonical memory.

Machine migration test:
restore canonical state -> rebuild indexes -> verify hashes/counts -> recall smoke.

## Context economy

Keep the existing progressive-disclosure philosophy:
retrieve ~6–12 candidates, rerank, expand only strongest few, inject only what the current step needs, preserve source references and token budget.

Refresh at task/phase boundaries, not every model token/call.

Invalidate retrieval cache after write/supersession.

## Qwen + Claude training integration

Future repair loop:

`Qwen attempt -> tests -> Claude audit/hint -> Qwen correction -> independent verification -> verified lesson -> restart -> unseen transfer`.

Store:
- attempt/evidence as LearningStore episode;
- generalized verified correction as VERIFIED_LESSON;
- repeatable verified action sequence as candidate skill;
- promotion only after shadow/A-B/security/rollback gates;
- project decision/fact in canonical notes/facts when appropriate.

Do not duplicate the whole record into every store.

Claude/frontier may propose memory corrections but never directly rewrite stable verified memory. Correction must pass verifier -> candidate revision -> shadow/A-B -> promotion/supersession.

## Forgetting

Safe memory needs controlled forgetting:
SUPERSEDED / EXPIRED / QUARANTINED / tombstone on explicit deletion.

Do not physically delete useful history by default. Derived indexes exclude inactive records after rebuild.

## Acceptance: "Bossman always remembers"

Do not claim this feature until all are proven:

1. Project-A decision is automatically recalled in a new session for Project A.
2. It is not injected into Project B.
3. A superseding decision wins while old history remains visible.
4. Verified Qwen lesson survives full restart.
5. A new analogous task retrieves it without the user saying "remember".
6. Poisoned/unverified lesson is not used.
7. DEGRADED skill is not blindly replayed.
8. Current owner instruction overrides old memory.
9. Prompt injection inside memory is treated as data.
10. Broken derived index rebuilds from canonical state.
11. Corrupt learning tail preserves earlier valid records.
12. Backup -> clean restore -> rebuild gives equivalent durable knowledge.
13. Recall stays within context budget and reports sources.
14. Crash resume uses durable checkpoint/effects, not chat history.
15. Secrets/hidden reasoning never enter learning memory.

## Implementation order after 1.0

M1 memory health/startup validation.  
M2 TASK_START/RESUME/AFTER_RESULT lifecycle hooks.  
M3 normalized retrieval across notes/facts/verified learning.  
M4 conflict/supersession + provenance UI.  
M5 automatic verified-lesson recall for Qwen.  
M6 backup/restore/migration drill.  
M7 Frontier Council memory audit.  
M8 only with evidence: optional cognitive-reuse promotion.

Do not enable experimental autonomy/reuse flags globally merely to claim completion.
