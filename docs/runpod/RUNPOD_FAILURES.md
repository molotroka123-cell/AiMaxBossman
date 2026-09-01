# RUNPOD FAILURES / GAPS LOG

## GAP-001: A/B RSS sampler under-reports Ollama memory
- TIME=2026-09-01T18:15Z PHASE=5_SMALL AB RESOURCES
- SYMPTOM: peak_ollama_rss_mib=50.4 while a 7B model is loaded (VRAM 6894 MiB)
- ROOT_CAUSE: psutil matcher sees only the `ollama` serve/router process; model tensors live in the spawned runner subprocess
- CLASS: metric gap in benchmark harness, NOT a Bossman product bug
- REMAINING_RISK: low; honest footprint = VRAM
- STATUS=OPEN (VRAM is the primary metric)

## BUG-002: pgvector extension missing + superuser privilege (ENV SETUP, not product bug)
- TIME=2026-09-01T18:25Z PHASE=FIRST_REAL_TASK
- REAL_REPRO: fresh Ubuntu 24.04 postgresql-16 → schema fail "extension vector not available"; after pgvector install → InsufficientPrivilege
- ROOT_CAUSE: canonical deploy assumes pgvector-enabled PG (docker image has it)
- MINIMAL_FIX: apt install postgresql-16-pgvector; CREATE EXTENSION vector/pg_trgm/pgcrypto as postgres; app role non-superuser
- TEST_EVIDENCE: schema applied (21 tables), serve up, tasks done
- STATUS=CLOSED (environment)

## DISC-001: fail-fast agent alias validation (WORKS AS DESIGNED)
- run_task worker honest fail on missing gateway alias; queued tasks retried after serve restart (verified)
- MINIMAL_FIX (config): added bossman-coder alias
- NOTE: worker exception stops loop until restart — future-local improvement (worker resilience)

## BUG-003: gateway client rate limit default → 429 burst (CONFIG CLASS)
- TIME=2026-09-01T19:07Z PHASE=LONG_RUN_50
- REAL_REPRO: 50-task burst → 31× HTTP 429 → tasks failed fast (9s), failures recorded
- ROOT_CAUSE: client rate limits not set in config (defaults low, burst=10); runner lacks bounded-backoff on 429
- MINIMAL_FIX: config-only requests_per_minute: 6000, burst: 200; rerun 50/50 done, real 429 = 0
- REMAINING_RISK: runner 429-backoff = FUTURE_LOCAL item
- STATUS=CLOSED (config)

## BUG-004: bossman-core auth redteam 3 failures on pod (TEST-INFRA, OPEN)
- TIME=2026-09-01T22:00Z PHASE=FINAL_REGRESSION
- SYMPTOM: tests/test_stage13_auth_redteam.py b3/b4/b7 fail with "RuntimeError: Task got Future attached to a different loop" (asyncpg pool vs pytest-asyncio 1.4 loop scopes); focused run: 2 failed alone; 29/31 pass incl. revocation positive paths
- ATTEMPTED FIX: db.pool() loop-tag recreation (c1c44df) — did NOT fix (3→4 failed) → REVERTED honestly (86836fa)
- ROOT_CAUSE: not pool singleton; likely DeviceService/remote_client fixture-level loop binding — needs deeper repro
- CLASS: test infrastructure; product auth logic covered by 29/31 redteam + real live negative case (AUTH_DENIED without bearer, verified via explain endpoint HTTP)
- SECURITY_EFFECT: none proven bypass; failures are loop errors, NOT authorization bypasses
- REMAINING_RISK: medium-low; MUST be reproduced/fixed in future-local session (next session item)
- STATUS=OPEN

## BUG-005: discovery silent-port test hangs on Linux pod (120s timeout kill, OPEN)
- TIME=2026-09-01T21:50Z PHASE=FINAL_REGRESSION
- SYMPTOM: command-center tests/test_discovery.py::test_open_port_that_stays_silent_is_not_called_absent hangs (epoll select, killed at 120s); other 16/17 discovery tests pass on pod incl. closed-port fix test
- CLASS: environment-sensitive test (silent-accept port semantics under pytest-asyncio 1.4 on Linux); original product bug (wrong diagnostic for busy port) remains proven fixed on Windows + closed-port case green on Linux
- REMAINING_RISK: low; future-local repro with the _RUNNER_HANG marker approach
- STATUS=OPEN

## NOTE-001: ollama keep_alive
- Models auto-evict after keep_alive expiry (verified: VRAM 2 MiB after long-run) — no leak

## NOTE-002: scheduler task #108 wiring
- source='schedule' task created via production path and completed; exact runner instance ambiguity noted for future-local recheck

## FINDING-001: model answer discipline (NOT a Bossman bug)
- Browser mission: engine executed real browser.open/extract (checkpoint.json: title "Example Domain" saved), but final model answers were poor (7b garbled tool-call echo; 14b meta-text instead of the heading)
- Router per-class evidence: 32b coding 0/3 vs 14b 3/3 — never select by size; 14b = coder/smart sweet spot
