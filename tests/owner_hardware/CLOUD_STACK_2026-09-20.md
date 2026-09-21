# Bossman Cloud Model Layer — 2026-09-20

This document defines the **cloud augmentation layer** for Bossman. Local execution remains primary. Cloud is a bounded auxiliary resource for public/non-sensitive work, overflow capacity, specialist verification and media fallback.

Pricing/model notes in this document are an owner-provided 2026-09-20 snapshot and MUST be revalidated against the provider's current official pricing/catalog before any billing decision or automatic routing rule is enabled.

## Core principle

Bossman routing order:

LOCAL FIRST
→ FREE CLOUD when the task is public/non-sensitive and policy allows
→ BUDGET CLOUD only within an explicit budget
→ PREMIUM CLOUD only for selected high-value review/escalation
→ CLOUD MEDIA only when local media is unavailable/too slow and policy allows.

Cloud failure must NOT silently trigger a more expensive provider.

## Data policy

### LOCAL_ONLY / PRIVATE
Never send outside the machine.

Examples:
- personal documents;
- immigration/residency data;
- passwords/secrets;
- financial/account data;
- private source repositories unless explicitly authorized;
- Telegram/account tokens;
- screenshots containing private UI state.

### PUBLIC / NON-SENSITIVE
May be eligible for free/budget cloud workers under policy.

### ASK
Any ambiguous task or task containing owner data must pause for explicit approval before leaving the machine.

## Cost control

- every cloud route has a per-task and daily budget;
- free→paid escalation requires explicit policy, never implicit fallback;
- premium Claude routes require a separate approval class;
- provider auto-recharge must be disabled unless the owner explicitly enables it;
- record provider, model, input/output tokens, tool fees, cache usage, and total estimated cost;
- thinking/reasoning tokens are counted as billable output where applicable;
- media generation cost is logged per artifact.

## Cloud Free pool

Primary free candidates:

- OpenCode Zen Nemotron 3.5 Lightning Free — short coding/tool jobs, lightweight worker;
- OpenCode Zen Nemotron 3 Ultra Free — planning, more complex review;
- OpenCode Zen MiMo-V2.5 Free — alternate free worker.

Optional reserve:
- Ling 3.0 Flash Fin Free;
- Big Pickle;
- Jev 1.13 Free for typed/router decisions;
- Z.AI GLM-4.7-Flash;
- Z.AI GLM-4.6V-Flash.

Do not enable data-sharing/training-tied free endpoints by default. Any endpoint whose terms allow prompt/response training must be opt-in and clearly labeled.

Free endpoints are for public, non-confidential work only unless provider terms explicitly allow otherwise and the owner approves.

## Cloud Budget pool

### GLM-5.3-Flash
Primary budget executor for public code, repairs, general agent tasks and cheap background workers.

### Qwen3.8-Flash
Very low-cost alternate worker for bulk transformations, short coding and low-cost parallel tasks.

### DeepSeek V4.1 Flash
Reasoning / long-context / code / tool route for hard public tasks.

### MiniMax M3
Alternative coding/agent worker for long tasks and multimodal work where appropriate.

### GPT-5.6 Luna
Specialized budget route for function calling, structured outputs and image understanding.

### Gemini 3.8 Flash
Multimodal validator for screenshots, documents, video/audio review and multimodal verification.

Provider-specific prices vary. Store pricing as configuration metadata with an as_of date, never as hard-coded permanent truth.

## Cloud Premium pool

### Claude Haiku 4.5
Cheaper Claude-family worker.

### Claude Sonnet 5
Primary premium code/architecture verifier.

### Claude Opus 5
High-value difficult escalation.

### Claude Fable 5.1
Rare deepest final review.

Premium policy:
- never first-line default;
- send only the minimum necessary context/diff/evidence;
- explicit budget approval;
- avoid uploading entire private repositories unless owner explicitly authorizes.

## Cloud Media

### Image
- GLM-Image — budget generation candidate;
- Gemini 3.1 Flash Lite Image / Nano Banana 2 Lite — generation + edit candidate.

### Video
- CogVideoX-3 — budget cloud video candidate.

Cloud media is a fallback/capacity lane. Local image/video remains the preferred default whenever quality/time is acceptable.

## Gateway / Router contract

Every provider adapter must expose:
- provider id;
- model id;
- privacy class;
- input/output/modalities;
- tool support;
- structured-output support;
- max context;
- current configured price snapshot;
- rate limits where known;
- current health;
- current budget state.

Every routing decision must record:
- task class;
- privacy classification;
- chosen provider/model;
- why local was not used or why cloud was preferred;
- expected cost;
- actual cost;
- fallback;
- whether owner approval was required;
- completion evidence.

## Acceptance tests

### CLOUD-01 — Local-first behavior
Given a task the local fleet can complete within policy/budget, Bossman stays local.

### CLOUD-02 — Free public worker
A synthetic PUBLIC task is routed to the selected free worker with no private context attached.

### CLOUD-03 — Private-data block
A task containing private owner data cannot be sent to free/budget cloud without explicit ASK approval.

### CLOUD-04 — No silent paid escalation
Force the free endpoint to fail. Bossman must NOT automatically call a paid provider unless routing policy explicitly permits it.

### CLOUD-05 — Budget ceiling
Exceed the per-task budget. The task must pause/fail honestly rather than continue spending.

### CLOUD-06 — Premium approval
Premium Claude route requires explicit premium approval and shows expected cost/context before dispatch.

### CLOUD-07 — Minimal-context premium review
Send only a diff/test logs/question, not the full repository, when the task can be reviewed from those artifacts.

### CLOUD-08 — Structured output
Compare raw model output versus runtime schema enforcement. Malformed JSON/tool args must fail closed.

### CLOUD-09 — Provider failure
Timeout/rate-limit/auth failure triggers bounded fallback and preserves task state.

### CLOUD-10 — Cost accounting
Verify token/media cost accounting is written once, attributed to the right task, and survives restart.

### CLOUD-11 — Media fallback
When local video/image capacity is occupied, a policy-allowed public media task may route to cloud without unloading the local resident fleet.

### CLOUD-12 — Terms/privacy guard
A provider flagged as training/data-sharing-sensitive is disabled unless owner explicitly opts in.

## Recommended minimal initial cloud set

- Nemotron 3.5 Lightning Free;
- Nemotron 3 Ultra Free;
- GLM-5.3-Flash;
- Gemini 3.8 Flash;
- Claude Sonnet 5.

Everything else is a fallback/A-B candidate until measured.

## Hardware interplay

The cloud layer exists partly to protect local resources.

Acceptance should include simultaneous:
- local media render;
- one cloud public worker task;
- one local private task;
- router/budget/accounting checks.

## EVO 1.0 relationship

EVO 1.0 may later recommend model/provider/fallback changes based on evidence.

It may NOT silently enable a provider, send private data, enable paid escalation, enable auto-recharge or increase premium budgets.

Any cloud-stack change remains:
proposal → isolated benchmark → evidence → owner approval → rollout → rollback if regression.