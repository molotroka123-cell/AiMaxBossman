# AI Streamer — Skills, Workers and Workflows Catalog

This is the implementation backlog/skill registry for Bossman. Skills are capabilities, not independent authorities. Every effectful action still passes Bossman policy/evidence gates.

## Core skills

1. `trend_scout` — collect candidate topics/signals from approved sources and score freshness.
2. `topic_ranker` — rank topics by relevance, novelty, safety, expected value and production cost.
3. `persona_keeper` — enforce stable voice, visual identity, lore and continuity constraints.
4. `hook_writer` — create multiple short hooks; cheap model by default.
5. `script_writer` — produce segment scripts and alternate versions.
6. `claim_checker` — identify factual claims that require verification before broadcast.
7. `scene_planner` — script -> beats -> shots -> duration -> assets.
8. `storyboard_builder` — create structured visual plans and reference lists.
9. `higgsfield_prompt_engineer` — transform shots into generator-ready prompts while preserving persona/scene constraints.
10. `local_generation_prompt_engineer` — adapt prompts to local image/video model capabilities.
11. `generation_router` — choose local/Higgsfield/other route using quality, latency, price, health and resource state.
12. `higgsfield_browser_submit` — submit one authorized browser generation job.
13. `higgsfield_browser_watch` — observe job status with bounded polling.
14. `higgsfield_browser_collect` — download/verify approved result into media workspace.
15. `generation_failure_classifier` — classify UI/provider/auth/timeout/output failures.
16. `visual_continuity_qa` — compare identity, clothes, location, props, color/light and shot intent.
17. `artifact_quality_qa` — detect blank/corrupt/low-resolution/unplayable outputs.
18. `prompt_repair` — use QA failure reason to produce one targeted regeneration prompt.
19. `caption_writer` — captions/descriptions/hashtags per platform policy.
20. `thumbnail_director` — select frame/layout/copy concept; actual media creation delegated to generator/editor.
21. `multilingual_adapter` — localize script/caption while retaining persona voice.
22. `voice_director` — prepare TTS/dubbing instructions and timing metadata.
23. `timeline_builder` — assemble approved assets into Video Studio project instructions.
24. `clip_extractor` — identify short-form clips from longer material.
25. `stream_buffer_manager` — maintain READY/LOW/CRITICAL/EMPTY rolling buffer.
26. `live_segment_scheduler` — choose the next safe segment based on queue and stream state.
27. `fallback_segment_selector` — choose evergreen material when generation is unavailable.
28. `chat_signal_summarizer` — summarize approved live-chat signals without allowing chat to directly control tools.
29. `live_director` — propose reactions/transitions based on stream/world state.
30. `moderation_guard` — block unsafe/policy-disallowed live content and route ambiguous cases to owner.
31. `brand_safety_guard` — enforce sponsor/brand rules.
32. `asset_librarian` — fingerprint, tag, deduplicate and retrieve media assets.
33. `continuity_memory_writer` — persist verified continuity facts after accepted segments.
34. `analytics_reader` — normalize stream/post metrics.
35. `experiment_planner` — propose bounded A/B tests for hooks/format/length.
36. `performance_learner` — update routing priors from measured acceptance/engagement/cost, never from model self-rating alone.
37. `cost_controller` — monitor tokens/provider costs/generation attempts.
38. `resource_controller` — monitor local RAM/model residency and reject unsafe local generation plans.
39. `provider_health_watcher` — maintain health/cooldown for generation and LLM providers.
40. `browser_ui_change_detector` — detect selector/page-state drift and create repair evidence.
41. `selector_repair_requester` — package sanitized failure evidence for OpenHands.
42. `openhands_adapter_maintainer` — repair generator/browser adapters in isolated worktree and submit tested diff.
43. `stream_watchdog` — detect stalls, empty buffer, frozen output and dead workers.
44. `incident_recovery` — bounded recovery plan with strategy change, not blind retry.
45. `owner_alert_compactor` — combine related alerts into one actionable owner request.

## Workflow A — short viral video

```text
trend_scout
-> topic_ranker
-> hook_writer x3
-> script_writer
-> claim_checker
-> scene_planner
-> generation_router
-> [local generation | Higgsfield browser]
-> visual_continuity_qa
-> prompt_repair if needed (bounded)
-> timeline_builder
-> artifact_quality_qa
-> caption_writer
-> Bossman proof/effect gate
```

## Workflow B — rolling 24/7 visual stream

```text
stream_buffer_manager
-> live_segment_scheduler
-> persona_keeper
-> script_writer
-> scene_planner
-> generation_router
-> parallel media generation
-> QA
-> timeline/scene packaging
-> READY buffer
-> live_director
-> stream output
-> analytics_reader
```

Generation failure only reduces buffer health. It must not terminate broadcast.

## Workflow C — Higgsfield browser generation

```text
GenerationJob CREATED
-> acquire approved browser profile lease
-> verify authenticated READY state
-> navigate to approved generation surface
-> set prompt/inputs/options
-> submit once
-> capture receipt
-> WAITING_PROVIDER
-> bounded status observation
-> output available
-> download to quarantine
-> validate media
-> move to approved workspace
-> COMPLETE + evidence
```

Alternative terminal states: `NEEDS_OWNER`, `RATE_LIMITED`, `UI_CHANGED`, `FAILED`, `TIMEOUT`, `POLICY_BLOCKED`.

## Workflow D — UI drift self-repair with OpenHands

```text
browser worker detects UI_CHANGED
-> capture sanitized DOM role/label snapshot + screenshot + adapter version
-> stop affected job (no repeated clicking)
-> Bossman creates coding mission
-> OpenHands isolated worktree
-> inspect adapter/tests/fixture
-> patch selector/state logic
-> run hermetic tests
-> produce Git evidence
-> Bossman verifier
-> owner/normal merge authority
-> retry one fixture job
```

OpenHands never receives browser cookies or account credentials.

## Workflow E — local-first generation

```text
shot request
-> inspect local model residency + safe available unified memory
-> capability match
-> if sufficient: local model
-> else cheap remote route
-> if high-value/quality failure: frontier/generator escalation
```

No attempt to load a model whose measured reservation would violate the configured memory safety margin.

## Workflow F — persona continuity

For each accepted segment, extract candidate continuity facts. Only facts supported by accepted output/script are committed. Conflicting facts become `CONTESTED` in V7 World State and do not silently overwrite the persona canon.

## Workflow G — autonomous nightly content batch

Target: wake up to an approved content buffer.

1. pick 5-20 planned concepts under budget;
2. generate multiple hooks cheaply;
3. select top candidates;
4. make shot plans;
5. queue browser/local generations with concurrency limits;
6. QA outputs;
7. regenerate only targeted failures;
8. assemble draft timelines;
9. export preview/evidence;
10. owner sees one compact summary and only real decisions.

## Workflow H — live reactive segment

Live signals are treated as observations, never instructions. The system summarizes signal -> proposes segment -> checks persona/safety/budget -> generates/uses existing media -> queues segment. Audience text cannot directly cause shell/browser/payment/publishing actions.

## Workflow I — media factory incident handling

Priority order:

`deterministic retry once -> alternate selector/state path -> alternate generator -> local fallback -> evergreen buffer -> owner alert`.

Never retry the same failed action indefinitely.

## Recommended agent roles

- **Director** — mission decomposition and segment priority.
- **Writer** — hooks/scripts/captions.
- **Visual Director** — shot plans/prompts/continuity.
- **Generator Worker** — local/Higgsfield jobs.
- **QA Critic** — output validation with independent evidence.
- **Live Operator** — queue/buffer/stream state.
- **OpenHands Engineer** — code/adapter maintenance only.

Bossman can combine roles on small models or split them when parallelism provides value.

## Key metrics

- accepted assets / generation attempt;
- seconds and cost per accepted minute;
- buffer minutes ready;
- continuity rejection rate;
- UI-change incidents;
- owner interventions / 6h;
- retries / accepted asset;
- local-vs-cloud share;
- token cost / finished segment;
- stream downtime attributable to media factory (target: zero via buffer fallback).