# Bossman 1.5 — final closure contract

Date: 2026-09-25  
Target: `feat/bossman-1.5-economy-orchestrator-20260924` → immutable RC → owner run.

## North Star

Bossman 1.5 is closed when the owner can give a goal and Bossman owns the loop:

`goal → plan → team → cheapest capable route → execute → verify → repair if needed → learn → retry/finish → report`

without routine hand-holding by an external coding model.

This is an **AGI-style personal operator target**, not a scientific claim that general AGI has been achieved.

## Five mandatory autonomy pillars

### 1. Scientific Self-Improvement

Implemented surfaces:
- existing bounded evolution loop;
- `bossman_v3.self_improvement.lab`;
- `bossman_v3.self_improvement.scientist`;
- runtime self-repair inbox;
- isolated repair candidates.

Required closure evidence:

`hypothesis → baseline → candidate → benchmark → regression → independent verifier → unseen transfer → promote/reject`.

A model saying DONE is never evidence. Stable is never rewritten directly by the learner.

### 2. Persistent Agent Society

Persistent roles survive tasks/restarts and retain:
- specialization;
- verified task-class performance;
- skill references;
- memory references;
- verifier rejection history;
- cost/latency history.

Jev may assemble an authorized team; Jev does not grant permissions.

### 3. Skill Compiler

A complex successful workflow may become a reusable skill only after:
- fully verified trace;
- independent verifier;
- unseen transfer PASS;
- source/evidence identity.

Compilation creates an EXPERIMENTAL candidate. Existing shadow/reliability/security gates control promotion.

### 4. Personal Operating Graph

Local structured graph covers:
- projects;
- companies;
- people;
- files;
- tasks;
- money;
- models/providers;
- workflows;
- markets;
- branches;
- agents;
- benchmarks;
- skills/artifacts.

Relationships keep provenance and temporal history. Superseded facts are not silently erased.

### 5. Autonomous Resource Manager

Route decisions jointly consider:
- measured quality lower bound;
- cost;
- latency;
- energy estimate;
- local/cloud locality;
- real unified-memory capacity;
- health/tool support.

Unknown cloud price is not free. Existing Cost Governor and policy remain authoritative.

## Owner supervision

The owner supervises Bossman rather than micromanaging it.

When an unattended task lacks human-only data, Bossman creates an OWNER_REQUIRED request and sends the required fields/instructions through the existing Telegram companion. The owner can answer from the phone; Bossman then resumes the same task.

Bossman must NOT:
- create external provider accounts automatically;
- accept Terms of Service;
- solve CAPTCHA or bypass provider controls;
- create API credentials on behalf of the owner;
- create multiple accounts to evade quotas;
- auto-recharge a provider.

External account onboarding is always owner work.

## External auditor role

The external auditor/controller is not the routine coder.

Its primary 1.5 owner-run mission is:
1. verify the installed candidate;
2. launch Bossman's own self-improvement bootstrap;
3. prove that Bossman created/ran its own bounded campaign;
4. attack boundaries and evidence;
5. stay outside the learner loop as independent auditor.

Routine fixes should move to Bossman's coding path and its own worker society.

## Self-improvement launch gate

Minimum success tomorrow:

- Bossman installed candidate starts;
- coding path ready;
- Jev route visible;
- provider pool preflight complete;
- runtime self-repair enabled;
- `tools/bossman_15_self_improve.py start` creates a real campaign;
- at least one ATTEMPT reaches independent verification;
- failed candidate is rejected/rolled back or successful candidate is retained only as a candidate;
- learning record survives restart;
- an unseen transfer task is attempted;
- stable source is not rewritten automatically.

Primary status target:

`SELF_IMPROVEMENT_PROCESS_STARTED`

Stronger target:

`SELF_REPAIR_SINGLE_CYCLE_PASS`

Best-day target:

`TRANSFER_MEASURED_GAIN`

Do not rename a weaker status into a stronger one.

## Provider capacity

Use existing configured zero-cost capacity first. If one provider is unavailable or rate-limited, the provider pool may offer another already-owner-configured provider.

If additional capacity requires an account/key, emit OWNER_REQUIRED with:
- provider;
- official signup URL;
- fields/actions required from owner;
- expected environment/secret reference;
- reason this capacity helps;
- whether a free tier was actually confirmed.

No automated registration.

## Money/economy principle

Use deterministic code/local models/free capacity before paid cloud. A paid finalizer is a bounded exception for unresolved verified blockers. Unknown price or unknown reported cost blocks further paid calls.

The goal is **cost per verified result**, not raw token volume.

## Trading learning

Trading learning remains:
`collect → verify → replay/backtest → paper → measure`.

No live exchange execution is part of Bossman 1.5 closure.

Teacher/video claims start UNVERIFIED and cannot promote themselves.

## Evidence matrix

| Surface | Code required | Automated tests | Owner-live evidence |
|---|---|---|---|
| Scientific improvement | yes | yes | required |
| Persistent society | yes | yes | restart + task history |
| Skill compiler | yes | yes | unseen transfer |
| Operating graph | yes | yes | graph continuity/retrieval |
| Resource manager | yes | yes | real resource/model route |
| Runtime self-repair | yes | yes | planted + natural failure |
| Telegram owner input | yes | yes | phone round-trip |
| Economy/provider routing | yes | yes | free-first + paid cap negative |
| YouTube training | yes | yes | real public videos |
| 1.0 regression compatibility | unchanged frozen 1.0 | Core/Command/root regression | installed candidate |

## Release verdicts

- `IMPLEMENTED` — code exists.
- `CONTRACT_PASS` — deterministic tests pass.
- `LOCAL_LIVE_PASS` — real local runtime works.
- `OWNER_LIVE_PASS` — owner-installed end-to-end trace exists.
- `RELEASE_CERTIFIED` — one immutable 1.5 SHA has all required evidence.

Documentation alone never upgrades a verdict.
