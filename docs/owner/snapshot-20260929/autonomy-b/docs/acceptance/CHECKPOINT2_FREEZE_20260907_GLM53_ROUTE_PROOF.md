# CHECKPOINT-2 — freeze-20260907-130104 — GLM 5.3 route proof + live catalog bugs

TESTED_RUNNING_SHA: 931584d72983ba205a5bf9f0b04c71a1b3326cf9 (same running instance, Command Center :8800)
PREV_CHECKPOINT: 773189e (CHECKPOINT-1)
PUSH_CONFIRMED: yes (this commit)

## MODEL IDENTITY PROOF — PASS (live)

- Agent id=3 "glm53-route-test" created with model_id=2 (z-ai/glm-5.3, cloud, provider id=2 OpenRouter, base_url https://openrouter.ai/api/v1)
- Route test: task 13, prompt "Return exactly: BOSSMAN_GLM53_ROUTE_TEST_freeze-20260907-130104" → **result matched exactly**, 5383 ms total, tokens 38/142, cost $0.000678
- Structured test: task 14 → **exact JSON** {"run_id":"freeze-20260907-130104","sum":5480,"status":"route-ok"}, 4997 ms, tokens 47/176, cost $0.00084
- Identity evidence: (1) DB task_runs.model_alias='z-ai/glm-5.3' both runs; (2) cloud cost charged per OpenRouter GLM pricing (local = $0); (3) local fallback IMPOSSIBLE — Ollama stopped, port 11435 dead (Test-NetConnection False); (4) exact echoed token/JSON rules out wrong-model guesswork
- PIN: POST /openrouter/2/pin {"remote_id":"z-ai/glm-5.3"} → model_id=2, pricing_known=true (price_in=1.4, price_out=4.4) — governance gate NOT triggered (fail-close risk unconfirmed for null-pricing promo models; GLM 5.3 has real pricing)

## NEW LIVE FINDINGS

- MR-001 (P1, transparency): task_runs DB row HAS model_alias, but GET /api/tasks/{id} returns runs with model=null, provider=null (`_run_public` strips it) and run events (run.started/step/completed) carry EMPTY data payloads → operator cannot see WHICH model answered, which provider, or whether fallback fired, anywhere in the UI. Campaign requirement "SELECTED_UI_MODEL→RESOLVED→RESPONSE_MODEL_ID" unobservable in product surface. OpenRouter's response model field is not captured anywhere.
- MR-002 (P1, catalog): server hard-clamps catalog to 200 rows (features/openrouter.py:148 `limit(min(limit, 200))`): live GET catalog?limit=2000 → 200 rows. Catalog total = 430 (verified live) → **more than half the catalog is unreachable via API, not just UI**; z-ai/* (17 glm models incl. glm-5.3) never appear in default view (ZAI_IN_DEFAULT200=0, first=aion-labs…, last=nvidia…); no total/has_more in response; UI has no "Showing X of N".
- MR-003 (P2, events): run events table (run_events.data) empty payloads for started/step/completed — audit trail has no model/provider/latency/tokens per step.
- MR-004 (info): run latency ~5s p50 for trivial one-shot GLM tasks (2 samples: 5383/4997 ms) — includes model TTFT; dashboard target "ordinary verified cycle p50<=1s" NOT met for model round-trips (expected for cloud; noted for V6 baseline).

## ALSO LIVE-VERIFIED THIS CHECKPOINT
- Desktop window launcher (bcc-desktop path) started against running server; server kept state across connections.
- Pin → engine → OpenRouter → answer chain fully wired and working end-to-end (the core product flow WORKS for chat once provider is correctly configured).

## STATUS DELTA
- OPENROUTER_GLM53=PASS (route-proof complete)
- MODEL_IDENTITY_PROOF=PASS (DB-level; UI-level = MR-001 failure)
- OPEN_P0=7 P1=20 (added MR-001, MR-002) P2=41 (added MR-003)
- FREEZE_STATUS=NOT_READY_FOR_FREEZE

Next: CHECKPOINT-3 = negative OpenRouter matrix (invalid key, duplicate provider, double connect) + Docker/core attempt for scenarios A-C.
