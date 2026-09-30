# STAGE 9 STATUS

HEAD: 7080c38 → stage9-коммиты поверх (см. git log)
BRANCH: claude/bossman-control-v03-43igbk

## DONE
- OpenRouter live smoke (`tests/test_feat_openrouter_smoke.py`, SKIP без ключа) + zero-inference контракт Connect/catalog/TTL (`test_connect_and_sync_never_infer`)
- Gateway E2E (`bossman-core/tests/test_stage9_gateway_e2e.py`, 7): never→zero cloud calls, ask→NeedsCloudApproval, allowed→200, 4xx no-failover / 5xx failover, breaker fast-503 NO_BACKENDS_AVAILABLE, correlation req/run_id в логах, X-Bossman-Cloud-Allowed контракт (DEC-004)
- Sandbox SAFE E2E (`test_stage9_sandbox_e2e.py`): fail-closed OFFLINE/HOSTILE на любой ОС; live lifecycle/timeout/quarantine — POSIX-only (SKIP на Windows)
- Resource Brain stress (`test_stage9_resource_stress.py`, 6): no-overcommit, exhaustion→release, double-release, TTL (управляемые часы), restart без утечек, pressure/disk_reserve
- Restart/recovery (`test_stage9_recovery.py`, 3): sandbox recover идемпотентен + аренда отпущена, state.json .bak, video checkpoint контракт (INTERRUPTED+reconcile)
- Full agent smoke (`test_stage9_agent_smoke.py`): Task→Context compile→Gateway ASGI→fs tool→journal→result; опциональный live-local через BOSSMAN_LIVE_LOCAL_URL
- Продуктовые фиксы: fs.* и journal utf-8 (cp1252 молча ломал кириллицу-поиск), gateway 400 на битый JSON, sandbox import fail-closed на nt

## VERIFIED
- 4xx не escalate, 5xx failover; breaker fast-fail; политика держится Gateway'ем
- fs.search/fs.read/fs.write корректны с UTF-8 на Windows

## NOT VERIFIED
- Live OpenRouter inference (нет ключа) — `OPENROUTER_API_KEY=<key> python -m pytest tests/test_feat_openrouter_smoke.py`
- Live sandbox lifecycle на Windows — POSIX-only по дизайну
- Полный bossman-core прогон в CI (workflows правки не входили в этот заход)

## BLOCKED BY HOST
- runsc/KVM, netns+nftables (egress direct-socket блок), Postgres для runner-level E2E

## OPEN P0
- нет

## OPEN P1
- Core API требует Postgres при старте (нет SQLite-фолбэка для локального смоука)

## TESTS
- `cd command-center && .venv/Scripts/python -m pytest tests/test_feat_openrouter.py tests/v2/test_openrouter.py tests/test_v21_openrouter_router.py tests/test_feat_openrouter_smoke.py -q` → 26 passed, 1 skipped
- `cd command-center && .venv/Scripts/python -m pytest ../bossman-core/tests/test_stage9_*.py -q` → 20 passed, 4 skipped

## LIVE TESTS
- OpenRouter: SKIP (нет ключа)
- SAFE runtime: PASS (fail-closed) / SKIP (live, Windows)
- local model: PASS (Ollama 11435 E2E ранее); live-local smoke = SKIP без env

## NEXT
1. CI job для bossman-core suites
2. SQLite-фолбэк Core API для локальных smoke
3. На Ai Max: BOSSMAN_SANDBOX_ENABLED=1 + BOSSMAN_LIVE_LOCAL_URL + OPENROUTER_API_KEY
