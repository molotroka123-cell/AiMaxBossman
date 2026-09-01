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
