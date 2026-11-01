# Readiness, 01.11.2026 (owner master prompt, item 1)

**Codex snapshot (historical):** on c0f6d2e4, `/health` returned 503 with models/providers UNHEALTHY and browser UNKNOWN.

**Root cause:** `BOSSMAN_MODEL_FRESHNESS=0` was set in the owner's Windows **user** environment. The freshness tick had been switched off by hand because its probes of cold local models made Ollama load tens of GB.
- As a result, no model was re-measured: every health record aged past 30 min (`STALE_AFTER_SECONDS`), and readiness depended on someone happening to use a model.
- Live `/api/model-freshness`: `enabled: false`, 0 cycles, 1/110 fresh.

**Fix (500244ac):**
- The freshness tick probes a local Ollama model only when `GET /api/ps` shows it resident. It never loads a model, and an unreadable `/api/ps` means skip.
- Free cloud models and already-running local servers are probed as before; paid models never.
- The user env override was removed.
- Test `test_a_cold_ollama_model_is_never_loaded_by_a_probe` covers three cases: cold → skipped, unreadable → skipped, resident → probed.

**Runtime source:** the old runtime checkout `wt-bossman-20-argon` had foreign uncommitted UI edits (mobile `app.css`, `index.html`, `sw.js`, `test_glass_ui.py`, mtime 01:34–01:35). They were left untouched. Runtime now runs from a clean checkout, `wt-runtime-20261101` (detached at main).

**Live proof** (runtime 500244ac, source_identity PASS, 01.11 01:52):
- freshness: enabled, cycle 1 probed 3 models:
  - nvidia nemotron-3-ultra (free): healthy;
  - local llama-server :8081 and :8090: provider_down. The servers are really off.
- browser: a real headless Chromium session 28 → https://example.com/ («Example Domain») in 4.52 s. `/health` browser = HEALTHY while it was open. A navigation to 127.0.0.1 was refused with 403 by the SSRF policy, which is correct.
- local request: task 896, agent 52 (owner-local-gemma4-uncensored-ctx32k, resident). «19*7» → «133», completed in 34.7 s.
- `/health`: HTTP 200, `ready: true`, status DEGRADED. Honest: in-use local servers :8081 and :8090 are down, and most of the 110 models are unused or stale.

**Not changed:** the 30 min shelf life, the readiness rules, the browser «unknown until a live context» rule.
