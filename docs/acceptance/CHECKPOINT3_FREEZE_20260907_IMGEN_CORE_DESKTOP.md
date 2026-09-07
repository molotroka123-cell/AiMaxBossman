# CHECKPOINT-3 — freeze-20260907-130104 — Image gen, core bring-up, desktop operator live

TESTED_RUNNING_SHA: 931584d72983ba205a5bf9f0b04c71a1b3326cf9 (BCC :8800) + bossman-core started from same worktree (core :8700 + gateway :8765, temp env-only config)
PREV: 05ee139 (CHECKPOINT-2). PUSH_CONFIRMED: yes

## IMAGE GENERATION VIA OPENROUTER — NOT_IMPLEMENTED (owner request, live-tested, $0 spent)

- bcc image feature is wired to a MOCK provider only: `images.py:35 PROVIDER = MockImageProvider()`; worker policy comment images.py:519-525 "only the deterministic mock provider is executable here. Real image providers are added later".
- LIVE: POST /api/images/jobs with model_alias="z-ai/glm-5.3" → job FAILED: "image provider для 'z-ai/glm-5.3' пока не подключён". GET /api/images/models lists ONLY mock-image. UI cannot even offer a real image model.
- Verdict: IMGEN_OPENROUTER = NOT_IMPLEMENTED. No real spend path exists (the $0.5 cap was not needed). Pipeline itself works (mock job → completed, deterministic SVG asset).

## CORE BRING-UP CHAIN — 5 consecutive setup landmines found (all reproduced live)

1. ENV-001 (P1, packaging): `bossman-core` does not declare dependency on repo-root `bossman-shared`, but `toolkit/net.py:38` imports it → core cannot start from its own directory / cannot be installed standalone ("Core без Postgres не работает" + "Core без repo-root тоже не работает"). Fix required PYTHONPATH hack (env-only, no repo change).
2. ENV-002 (P1, setup): DB schema hard-requires **pgvector** extension → plain postgres:16 = DEPENDENCY_UNAVAILABLE at startup (fail-fast, honest — but no docs/compose in-repo provide the right image; compose.core.yaml references non-repo bossman-infra with litellm/llama-swap/opt-bossman paths). Used pgvector/pgvector:pg16.
3. ENV-003 (P1, doc-code mismatch): README says cloud_policy `never/ask/allow`, code requires `CLOUD_POLICIES = ("never", "ask", "allowed")` (agents.py:13) → agent.yaml with documented `allow` breaks task execution with ValueError at load_all() (task → 500).
4. ENV-004 (P2, env contract): core requires Redis (runner queue), default `redis://redis:6379/0` (docker hostname) → 500 on task enqueue on non-docker host; must override REDIS_URL; not documented in bossman-core README.
5. ENV-005 (P2, env contract): `BOSSMAN_GATEWAY_URL` must include `/v1` (GatewayClient appends `/chat/completions`, gateway route is `/v1/chat/completions`) — without it every chat 404s with cryptic httpx error. Default inside client.py:55 includes /v1, config default/README do not mention.

Positive: gateway→OpenRouter backend healthy (268ms probe); device-token auth + profiles gate work fail-closed.

## CORE ROUTE TEST — PASS (after env-only workarounds)

- Task via core API (agent accept, alias bossman-smart → z-ai/glm-5.3 via OpenRouter) → STATUS=answered, result contains exact token CORE_GLM53_ROUTE_TEST_freeze-20260907-130104. Latency 77.6s for a trivial one-shot (includes gateway probes/retries — slow-path finding, PERF-003 P2).
- Auth chain: device token via bootstrap_remote_device.py → Bearer → scopes enforced (chat/approve/admin) — works.
- Profiles gate: fail-closed for non-local device (CapabilityDenied until profile bound + toggle enabled) — GOOD security behavior. Minor: PATCH /profiles/{id}/toggles silently no-ops on flat body (expects {"toggles":{...}}; extra keys ignored, no 422) — PR-002 P2.

## SCENARIO A (desktop) — FAIL, root cause proven live (DO-001 confirmed)

- POST /computer/tasks (OBSERVE_ONLY, harmless notepad goal) → FAILED: foreground error 'ModuleNotFoundError' (pywinauto/pyautogui not declared in any requirements), ui_tree=unavailable, **replans_used=21 (21 LLM calls burned) → "planner replan budget"**.
- FINDING DO-017 (P1): no fail-fast when desktop backend unimportable/unavailable — full replan budget (≈21 model calls) spent before honest FAIL. Cost + time hazard on every misconfigured host.
- Scenario A verdict: FAIL (acceptance-blocking), root cause = undeclared deps + no preflight.

## STATUS DELTA

- IMGEN = NOT_IMPLEMENTED (mock only)
- CORE_CHAT_ROUTE = PASS (live, GLM 5.3)
- SCENARIO_A = FAIL (live, DO-001 + DO-017)
- OPEN_P0=7 P1=24 (+ENV-001..003, DO-017) P2=46 (+ENV-004..005, PR-002, PERF-003)
- FREEZE_STATUS=NOT_READY_FOR_FREEZE

Cleanup: test containers (pg/redis), core+gateway processes stop after push; BCC dashboard stays running for owner.
