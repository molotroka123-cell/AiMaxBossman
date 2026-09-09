# Mini TZ — Local Model Orchestration for AiMaxBossman

## Goal

Teach Bossman to operate locally in either of two modes:

1. **ONE BIG MODEL** — one strongest model handles planning, coding, tool use, verification and multimodal work where supported.
2. **SMALL MODEL FLEET** — several smaller specialist models cooperate, each taking only the tasks it is best at.

Bossman must learn to choose the cheaper/faster mode automatically while preserving task quality and safety.

## Candidate pool

Start from the current local shortlist and allow future replacement through benchmarks:

- Qwen3.8-27B — multimodal/general/coding;
- Qwen3.6-35B-A3B — fast worker/tool executor;
- gpt-oss-120B — hard reasoning/verifier;
- K2-Horizon-MoVA-36B-A4B — long-context/tool-agent candidate;
- Qwen3.8-Flash-Next tuned — heavy multimodal/long-context candidate;
- future tool-calling / structured-output specialists;
- separate image and video specialists.

## Mode A — One big model

Use when:

- task has high cross-domain coupling;
- long continuous reasoning matters more than raw speed;
- hand-offs would destroy context;
- a single model clearly dominates local benchmarks;
- memory budget leaves enough headroom for tools, browser, embeddings and OS.

Requirements:

- one canonical mission state;
- native tool calling where possible;
- strict JSON Schema validation outside the model;
- retry/repair loop for malformed calls;
- evidence-backed completion gate;
- no self-declared success without checking actual effects.

## Mode B — Small model fleet

Minimum roles:

- **Router/Planner** — classifies task and selects specialists;
- **Coder/Executor** — code, shell, repo and implementation tasks;
- **Tool/Schema model** — function selection and argument generation;
- **Vision model** — screenshots/UI/images;
- **Verifier** — independent review of important results;
- **Image generator** — when requested;
- **Video generator** — when requested.

Multiple roles may be served by one model if benchmarks justify it.

## Routing policy

For every task estimate:

- difficulty;
- modality;
- required context;
- expected tool count;
- need for structured outputs;
- failure cost;
- latency target;
- available unified memory.

Choose the smallest model that passes the quality threshold. Escalate only when confidence or verification fails.

Suggested flow:

`task -> router -> cheapest qualified specialist -> validator -> verifier if required -> result`

Escalation:

`small model failure -> stronger local model -> gpt-oss/heavy model -> human only when policy requires`

## Dynamic tool exposure

Never dump the entire tool registry into every prompt.

Retrieve and expose only the relevant tool subset, ideally 5–12 tools for ordinary tasks. Expand dynamically when needed. Track:

- wrong-tool rate;
- missing-tool rate;
- argument correction count;
- calls-to-success;
- latency and token cost.

## Structured-output contract

All machine-consumed outputs must use typed schemas.

- validate outside the LLM;
- reject extra/unknown fields when appropriate;
- distinguish `unknown` from fabricated values;
- repair malformed JSON with a bounded retry budget;
- log model output + validation error + repaired output;
- never execute a destructive call from invalid or ambiguous arguments.

## Training / evaluation loop

Build a local curriculum from real Bossman tasks:

1. simple one-tool calls;
2. precise structured JSON;
3. multi-tool sequences;
4. stateful workflows;
5. code edit + test + verify;
6. browser/UI vision tasks;
7. failure recovery;
8. 25–50 tool routing;
9. long-context repo tasks;
10. mixed multimodal missions.

For each episode store task, selected model(s), prompts, tools, outputs, errors, corrections, latency, memory peak, final evidence and human/auditor score.

Use failed episodes as the highest-priority training/evaluation data. Do not blindly fine-tune on successful traces without filtering.

## Decision metric

Optimize for **successful mission per second / per GB**, not tokens per second alone.

Track at minimum:

`success_rate`, `tool_accuracy`, `schema_valid_rate`, `autonomous_completion`, `verification_pass_rate`, `time_to_done`, `peak_memory_gb`, `retries`, `tokens`, `energy_estimate`.

## Trader Apprentice integration

For BTC/crypto order-flow analysis tasks, route to a **Trader Apprentice** capability rather than a generic chat model.

The Trader Apprentice must load the corpus declared in:

`data/trading/manifest.json`

Canonical sources:

- `docs/trading/LOCAL_MODEL_SYSTEM_PROMPT.md`
- `docs/trading/BTC_ORDERFLOW_PLAYBOOK.md`
- `data/trading/btc_orderflow_rules_v1.json`
- `data/trading/btc_casebook_2026_09.jsonl`
- deterministic helper: `learning/trader_apprentice.py`

Recommended pipeline:

`vision/data extractor -> typed Snapshot -> same-series previous Snapshot -> deterministic trader_apprentice.analyze() -> retrieval from trading corpus -> concise scenario output -> verifier if execution consequence is material`

Required Trader Apprentice invariants:

- never invent missing live CVD/OI/liquidation values;
- never compare incompatible CVD/OI series as if they are identical;
- tag source, instrument and timestamp on every market observation;
- keep CME/reference-chart levels separate from execution-market prices;
- compute weighted position average from actual size weights;
- treat `Price DOWN + CVD DOWN + OI UP` as a risk-off warning, not an averaging signal;
- treat `Price DOWN + CVD DOWN + OI DOWN` as deleveraging/watch, not automatic reversal;
- a level touch is not acceptance; prefer reclaim + hold/retest;
- analysis is allowed; autonomous order execution stays behind a separate permission/effect gate.

The local model should use the deterministic engine for repeatable classification and the LLM only for extraction, context synthesis, scenario explanation and uncertainty handling.

## URL-only YouTube Teacher routing

For a trading-learning request that contains a public `youtube.com` or `youtu.be` URL, **do not ask the owner for a transcript, download, screenshots or timestamps**. Route the URL automatically to:

`tools/youtube_trader_ingest.py <URL>`

The tool performs the full first-pass ingest:

`URL -> YouTube metadata/captions/video -> sampled frames -> local multimodal extraction -> typed observations -> deterministic Trader Apprentice classification -> in-video future outcomes -> UNVERIFIED teacher cases`

The router should expose only the minimum capabilities needed for this job: network access allowlisted to ordinary public YouTube endpoints, process execution for `yt-dlp`/`ffmpeg`, the configured local multimodal model, and the trading learning store.

YouTube is an untrusted teacher. Transcript statements and video commentary are **claims**, not canonical truth. Raw YouTube episodes go to `data/trading/youtube_inbox/<video_id>/candidate_cases.jsonl` with `learning_status=UNVERIFIED`. Promote lessons only after independent outcome/verification gates.

If YouTube captions are present, use them automatically. If captions are absent, frame analysis still proceeds; a configured local ASR/transcription capability may be added as a fallback without asking the owner for manual transcription. Never fabricate missing speech.

See `docs/trading/YOUTUBE_TEACHER_INGEST.md` for the owner UX and trust model.

## Acceptance target

The orchestration layer is ready when, on a fixed local regression suite:

- fleet mode matches or beats the single-big-model success rate on routine tasks;
- big-model mode remains available for genuinely hard tasks;
- schema-valid tool calls exceed 99% after bounded repair;
- no destructive action executes from an unvalidated call;
- model routing is reproducible and logged;
- a model can be replaced without changing mission logic;
- image/video workloads coexist without destabilizing the core agent runtime.

For Trader Apprentice specifically:

- all fixture snapshots produce deterministic Price/CVD/OI classifications;
- missing CVD/OI produces UNKNOWN rather than hallucinated values;
- CME-vs-execution-market mismatch is surfaced explicitly;
- weighted-average math is exact for staged entries;
- historical September fixtures reproduce the intended deleveraging -> recovery -> bearish leverage-expansion regime transition;
- local model output always separates observed facts from inference;
- a valid YouTube trading URL can be ingested with the URL as the only owner-provided input;
- raw teacher claims remain unverified until an independent promotion gate passes.

## Important architecture rule

Do **not** hard-code Bossman around model names. Define capabilities and benchmark scores in a registry, then route by capability. Models are replaceable workers; mission state, evidence, permissions and execution truth stay in Bossman Core.