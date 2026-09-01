# RUNPOD FUNCTION MATRIX (draft — Agent 3 в работе)
Формат: | Function | Production call-site | Real test possible here? | Test method | Result | Evidence | Bug |
Result возможные: LIVE_PROVEN / INTEGRATION_PROVEN / PARTIAL / HOST_NOT_APPLICABLE / GATED_NOT_ENABLED / BLOCKED / DEAD-UNWIRED

## Уже заполнено
| Function | Call-site | Testable | Method | Result | Evidence | Bug |
|---|---|---|---|---|---|---|
| Gateway inference (local provider) | bossman/gateway/app.py (bossman-core) | YES | реальный POST /v1/chat/completions | LIVE_PROVEN | /workspace/artifacts/gateway_e2e_resp.json; outcome=ok fallbacks=0 | - |
| Gateway auth (client key) | gateway/config.py clients.key_env | YES | curl с ключом/без | LIVE_PROVEN (успешный запрос с ключом; негативный кейс pending) | gateway_e2e.log | - |
| Model alias resolution | gateway config aliases | YES | alias bossman-fast → qwen2.5:7b | LIVE_PROVEN | gateway log model=qwen2.5:7b | - |
| Cloud policy / local-only | bossman/llm.py CloudDenied | YES | env без ключей + fallbacks=0 | INTEGRATION_PROVEN (zero keys present + fallbacks=0; негативный CloudDenied-тест pending) | gateway_e2e.log | - |
| Direct vs Bossman (SMALL tier) | tools/local_hardware_ab.py | YES | 6 классов x 3 | PASS: 0.667 vs 0.667, Retention 1.0 | /workspace/benchmarks/ab_small_qwen25_7b.log | - |
| Direct vs Bossman (MEDIUM tier) | tools/local_hardware_ab.py | YES | 6 классов x 3 | PASS: 1.0 vs 1.0, Retention 1.0, reasoning/long_context решены 14b | /workspace/benchmarks/ab_medium_qwen25_14b.log | - |
| Task engine (real task) | bossman/runner.py run_task | YES | bossman task → analyst → OK | LIVE_PROVEN: tasks 1-3 done, WM persisted | psql tasks/runs + gateway log | - |
| Memory restart/restore | db + working_memory | YES | PG service restart + serve restart | LIVE_PROVEN: rows intact, new task done | RUNPOD_METRICS.json memory_restart | - |
| Model Router (real decisions) | command-center/bcc/v2/model_router.py | YES | route() с живыми метриками, 6 сценариев | INTEGRATION_PROVEN: память-констрейнт, falsified-caps, cloud-denied работают | /workspace/benchmarks/router_real.json | BCC engine HTTP path pending |
| Task engine (real task) | bossman/runner.py run_task | YES | bossman task → analyst → OK | LIVE_PROVEN: 108 runs, tasks done | psql tasks/runs + gateway log | - |
| Working/Decision Memory + restart | db + working_memory | YES | PG restart + serve restart | LIVE_PROVEN: rows intact, WM 1/task | RUNPOD_METRICS memory_restart | - |
| Context Engine (real stress) | runner ContextBuilder vs direct | YES | 8k/16k/32k needles | LIVE_PROVEN: деградация 0.0 vs direct | /workspace/artifacts/stress_*.json | 32k start-loss = потолок 7b |
| Files intelligence | bossman/file_intel.py parse_file | YES | реальные csv/json/md/zip + corrupt + unsupported | LIVE_PROVEN (+11 unit) | /workspace/artifacts/files/files_real.json | - |
| Artifact Engine | bossman/artifacts_engine.py | YES | register x2 → версии, hash, reopen | LIVE_PROVEN | psql artifact_registry | - |
| MCP (real SDK) | command-center fixtures + mcp SDK | YES | stdio fixture server, 15 тестов | LIVE_PROVEN 15/15 на Linux | pytest test_v21_mcp.py | - |
| Browser | bossman/toolkit/browser.py | YES | headless Chromium: 18 repo тестов + example.com | LIVE_PROVEN | pytest + render | - |
| Security: egress redact | bossman/obs.redact | YES | canary sk-… | LIVE_PROVEN: «REDACTED» | script output | - |
| Security: sandbox secrets | bossman/sandbox/secrets.py | YES | grant/redeem/revoke + cross-sandbox | LIVE_PROVEN: SecretDenied | script output | - |
| Security: CyberSec firewall | bossman/cybersec/guards.py | YES | OFF по умолчанию | GATED_NOT_ENABLED (честно) | env unset | - |
| Recovery | runner + failures | YES | provider kill → failed → restore → done | LIVE_PROVEN | failures table | runner 429-backoff = FUTURE_LOCAL |
| Scheduler | bossman/schedule_runner.py | YES | cron точность + продакшн тик | LIVE_PROVEN | task #108 source=schedule | NOTE-002 wiring |
| Flight Recorder/Explain | bossman/flight_recorder.py | YES | explain_task(2) | LIVE_PROVEN, без секретов | script output | - |
| Concurrency | runner queue | YES | 4 одновременных | LIVE_PROVEN 4/4 за 4s | gateway log | - |
| Long-run stability | 50 задач | YES | статусы + latency first/last + VRAM/RAM | LIVE_PROVEN 50/50, деградации нет, утечек нет | /workspace/artifacts/gateway.log | BUG-003 rate limit (fixed config) |
| Cloud policy / local-only | bossman/llm.py CloudDenied | YES | 0 ключей + fallbacks=0 везде | LIVE_PROVEN (structural + runtime) | gateway log | - |
| Computer GUI (Windows) | computer_operator | NO (Linux headless) | - | HOST_NOT_APPLICABLE | - | future-local retest list |
| V3 gated (UCA/SIL/Guardian…) | V3 modules | NO (по правилам) | контракты только | GATED_NOT_ENABLED | - | - |

## Очередь заполнения (A3 строит по коду)
Core (task execution, sessions, EventBus, approvals, policy/scopes, verifier, cost, Secret Store, Flight Recorder, explain) — pending
Models (registry, router, uncertainty, adaptive compute, retry/replan, portfolio metrics) — pending
Memory/context (Working/Decision/Failure Memory, Context Engine, holdout, Learning Guard) — pending
Skills/planning (discovery, task compiler, DAG, mission) — pending
Tools (sandbox, approvals, analysis.run, file.parse, artifact.create, browser, MCP) — pending
Files/artifacts/research (CSV/JSON/MD/ZIP/DOCX/XLSX/PPTX/PNG/PDF, Artifact Engine, Evidence Graph, Research QUICK/STANDARD/DEEP) — pending
Automation (scheduler, overlap guard, budget) — pending
Computer (capability discovery, typed actions, loop guard; Windows GUI = HOST_NOT_APPLICABLE) — pending
Security (injection firewall, ingest/egress guard, IDS, vault, scanners) — pending
Media/voice (probe, honest unavailable) — pending
V3 gated (UCA, Visual State, Self-Healing, Skill Factory, Recovery Kernel, SIL, Guardian) — contracts only
