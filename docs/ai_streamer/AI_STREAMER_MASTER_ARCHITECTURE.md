# BOSSMAN AI STREAMER / AUTONOMOUS MEDIA STUDIO

## Goal

Build a persistent AI media system that can plan, generate, QA, package and continuously refresh a 24/7 stream/content feed using Bossman as the authority layer, OpenHands as the coding/repair worker, local models for cheap continuous work, low-cost cloud models for creative/semantic work, and browser-driven Higgsfield generation when the normal API/MCP route is unavailable.

This is an additive subsystem. It must not weaken V4-V7 permission, evidence, budget, secret, approval, sandbox, or post-state gates.

## Reference use case

A continuously running virtual creator/streamer with recurring scenes, a stable persona, generated visual inserts, reactive segments, shorts/reels, thumbnails, captions, chat-aware transitions and automated recovery. The system should be able to keep producing content while the owner is away, but should stop and request human help whenever a login challenge, CAPTCHA, account-policy block or ambiguous external action appears.

## Core architecture

```text
OWNER GOAL
  -> V7 Reality Compiler / Mission IR
  -> AI Stream Director
      -> Trend & Topic Scout
      -> Persona / Continuity Memory
      -> Script & Hook Planner
      -> Shot / Scene Planner
      -> Model Router
          -> deterministic/local model
          -> cheap OpenRouter model
          -> frontier escalation
      -> Generation Broker
          -> local image/video model
          -> Higgsfield Browser Worker
          -> other approved generators
      -> Visual / Audio QA
      -> Package / Timeline Builder
      -> Video Studio / OBS / Stream output
  -> post-state evidence
  -> analytics / learning
```

## Authority boundaries

- Bossman owns mission completion, budgets, permissions and evidence.
- OpenHands may modify code in isolated worktrees; it cannot deploy or mark a media mission complete by itself.
- Browser workers may interact only with explicitly allowlisted generation workflows.
- Higgsfield authentication stays in the owner's normal browser profile. Credentials/cookies are never copied into prompts, logs, Git or cloud-agent evidence.
- CAPTCHA, anti-bot challenge, re-authentication, payment/plan change or policy warning => `NEEDS_OWNER`; no bypass attempt.
- The worker must respect account/service rate limits and normal product controls. Browser automation is not a mechanism for bypassing product restrictions.

## Model economy

Default routing philosophy: cheapest measured-capable path first.

1. deterministic code: parsing, scheduling, templates, file operations, hashing, dedup;
2. small local model: tagging, classification, rough captions, prompt cleanup, continuity checks;
3. medium local / cheap OpenRouter: scripts, hooks, scene ideas, prompt variants, semantic QA;
4. frontier cloud: difficult creative planning, ambiguous recovery, high-impact final review;
5. Higgsfield/browser or local generation model: actual media synthesis.

The V7 router should consider expected quality, latency, monetary cost, local RAM pressure, provider health, generation queue depth and mission priority.

## Persistent state

Each show/persona gets a `StreamerProfile` with:

- persona rules and speaking style;
- prohibited claims/topics;
- visual identity references;
- recurring cast/locations;
- continuity facts;
- approved sponsor/brand rules;
- language variants;
- segment templates;
- media asset catalogue;
- performance metrics and experiment history.

Do not let a model silently rewrite identity constraints. Changes to persistent persona policy require an explicit versioned update.

## Content loop

```text
OBSERVE
 -> choose topic/segment
 -> draft hook/script
 -> fact/claim check when needed
 -> create shot list
 -> generate prompts
 -> choose generator
 -> submit generation job
 -> wait / poll with bounded retry
 -> inspect output
 -> reject/regenerate or accept
 -> package into timeline/scene
 -> verify output exists and is playable
 -> publish/stream only through existing authority gate
 -> collect metrics
 -> update experiment memory
```

## 24/7 stream mode

The stream must not depend on a single long-lived generation call. Use a rolling content buffer:

- `READY`: minimum 30-60 minutes of approved segments;
- `LOW`: under target buffer; increase generation priority;
- `CRITICAL`: under safe floor; fall back to evergreen approved segments;
- `EMPTY`: never improvise an uncontrolled external action; use safe slate/loop and alert owner.

Generation and broadcast are decoupled. Higgsfield/browser failure must never kill the live stream.

## Evidence per generation job

Persist only sanitized metadata:

- job id;
- mission id;
- provider adapter and browser profile id (not credentials);
- prompt hash plus safe prompt copy if policy permits;
- input asset hashes;
- requested format/duration/aspect;
- timestamps;
- bounded attempts;
- result file path under approved media workspace;
- result SHA-256;
- QA verdicts;
- screenshots/status receipts without secrets;
- failure classification.

## Failure classes

`AUTH_REQUIRED`, `HUMAN_CHALLENGE`, `RATE_LIMITED`, `GENERATION_FAILED`, `TIMEOUT`, `UI_CHANGED`, `DOWNLOAD_FAILED`, `OUTPUT_INVALID`, `PROVIDER_UNAVAILABLE`, `RESOURCE_PRESSURE`, `POLICY_BLOCKED`.

No generic infinite retry. Each class has a bounded recovery policy.

## Integration targets

- `apps/social-farm`: planning, account/content scheduling and browser primitives already exist; extend them rather than build a second social system.
- `command-center`: expose status, queue, buffer health, pending human actions and current generator/model route.
- `Video Studio`: final assembly, clip/timeline/export.
- `OpenHands`: selector repair, adapter development, tests, prompt-pack evolution, local-generation integration.
- V7 World State: provider health, browser readiness, buffer depth, local RAM, model residency and asset availability become observations used by strategy selection.

## Delivery phases

### Phase A — browser generation worker
Implement Higgsfield-specific adapter contract, browser-state detection, job queue, evidence receipts and human-challenge stop state.

### Phase B — media director
Persona, scene planner, model router, continuity QA, generation broker and rolling buffer.

### Phase C — local generation fleet
Adapters for the selected local image/video models; V7 resource-aware routing chooses local vs browser/cloud.

### Phase D — autonomous streamer
Scene scheduler, reactive segment planning, chat/stream signals, Video Studio/OBS integration, watchdog and analytics learning.

### Phase E — measured optimization
Use cost, latency, output acceptance rate and engagement metrics to tune routing without allowing engagement optimization to override safety/policy.

## Acceptance target

A 6-hour unattended fixture run must produce a rolling queue of valid segments without deadlocks, secret leakage or uncontrolled retries. Browser challenges must pause only the affected generator, not the whole stream. A deterministic replay must prove recovery from UI change, provider timeout, local-model OOM rejection and corrupted output.