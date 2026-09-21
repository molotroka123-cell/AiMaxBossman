# BOSSMAN 1.0 — Image Generation + Next Dashboard

Status: implementation contract for the owner release line.

## Goal
BOSSMAN must treat image generation as a first-class agent capability, not a mock demo. A user can type a natural-language image request in the main command surface, generate a real image, see progress, cancel, retry, reuse the prompt, and reopen the asset from the library.

## Existing foundation verified on release/bossman-owner
- Native Images API, queue, library, collections, transforms and protected file serving.
- Local ComfyUI adapter using native nodes only.
- Honest mock labeling and provider-unavailable errors.
- Image Studio UI and job cancellation.
- Main dashboard already exposes system, apps, agents, tasks, approvals, compute and health.

## Image capability contract
1. Intent routing: commands such as "сгенерируй/нарисуй/создай картинку" route to Images instead of a generic text task.
2. Real-by-default: never silently fall back from a requested real provider to mock-image.
3. Provider ladder:
   - local ComfyUI when configured and healthy;
   - configured cloud image provider through an explicit adapter;
   - otherwise return OWNER_REQUIRED with a human-readable setup reason.
4. One provider interface: render(spec,index) -> bytes, MIME, measured metadata.
5. Preserve prompt, negative prompt, model/provider, dimensions, seed, source/reference asset IDs and provenance.
6. Verify returned bytes before marking an asset ready. Never infer MIME from filename.
7. Editing is non-destructive: source image remains immutable and derived assets record provenance.
8. Agent tool schema:
   generate_image(prompt, negative_prompt?, aspect_ratio?, width?, height?, count?, provider?, source_asset_id?, reference_asset_ids?)
   returns job_id immediately; status and resulting asset IDs are observable.
9. Cancellation must release the BOSSMAN worker promptly.
10. No provider secrets in events, logs, prompts, asset metadata or UI.

## ChatGPT-like behavior
The conversational layer converts a short user request into a generation spec but does not invent unsupported provider features. It should preserve identity/reference intent when source assets are supplied, choose sensible dimensions from aspect ratio, keep the original user prompt in provenance, and return the generated asset directly into the conversation/library when complete.

## Dashboard Next
The home screen becomes an owner cockpit rather than a wall of cards.

### Above the fold
- universal command composer, with explicit mode chips: Ask / Build / Image / Video / Research / Computer;
- live execution strip showing current task, agent, model/provider, elapsed time, cost and cancel;
- owner-attention rail for approvals, failed jobs, degraded providers and budget warnings.

### Main workspace
- Mission timeline: queued -> planning -> running -> verifying -> done.
- Agent swarm: active agents with current job and model, expandable rather than permanently dense.
- Model router: local/cloud route, health, context usage, latency and fallback reason.
- Compute: CPU, RAM/unified memory, GPU, model residency and queue pressure.
- Recent outputs: images, video, files, sites and code as visual cards.
- Apps become a searchable launcher, not the visual center of the home page.

### UX rules
- progressive disclosure; owner sees decisions first and engineering detail on demand;
- desktop responsive at 1024/1366/1440/1920 and mobile 390;
- keyboard-first command bar; Ctrl/Cmd+Enter runs;
- no fake percentages, fake health or fabricated hardware values;
- every destructive/external action keeps the existing never/ask/allowed policy;
- optimistic UI only for reversible local state; server truth wins for execution state;
- dark/light themes share tokens; avoid one-off inline visual systems.

## Acceptance
- Natural-language image request from Home reaches a real configured image provider.
- If no real provider exists, UI clearly says what is missing; no mock result is presented as generated AI art.
- Generated image is byte-verified, persisted, previewable, reopenable and reusable.
- Cancel/retry works.
- Dashboard remains usable with one subsystem down.
- Browser smoke covers all target viewports.
- Existing owner scenarios and exact-SHA release gates remain green.

## Non-goals for 1.0
Do not download model weights automatically, install arbitrary ComfyUI custom nodes, expose ComfyUI remotely, or claim image editing/reference support for a provider that only implements text-to-image.
