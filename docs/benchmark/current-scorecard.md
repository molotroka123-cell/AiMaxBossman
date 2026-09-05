# Current scorecard (rendered from current-scorecard.json)

| # | Ось системы | Оценка | Статус | Уверенность | Улики |
|---|---|---:|---|---|---|
| 1 | Execution Truth | 8.5/10 | VERIFIED | HIGH | EH-01: улика verified=True доверяется только с HMAC-подписью доверенного signer'а; журнал подписывает закрытый шаг (000f331, bossman-core/tests/test_v3_evidence_signing.py); FL-01: fence движка — зомби-воркер не пишет receipt/статус, внешний эффект не повторяется (2487694, command-center/tests/test_fence_fl01.py); TaskJournal: finished = receipt ∧ verified (bossman-core/tests/test_v3_memory_kernel.py, test_v3_invariants.py, test_v3_compound_resume.py); FleetExecutionBridge отбрасывает поддельные verified-улики, пересобирая их из журнала (test_v3_fleet_e2e.py::forged evidence, 084ad3a); PLACED→VERIFIED запрещён в LEGAL_TRANSITIONS (test_v3_fleet_core.py); V2 action contract тесты (command-center/tests/test_action_contract.py); EH-05: FAIL гейта без явного requeue = сбой гейта (4c8fec2, command-center/tests/test_gate_contract_requeue.py) |
| 2 | Security | 8.0/10 | VERIFIED | HIGH | P0-A gateway loopback fail-closed при proxy-заголовках (cbdabf2, bossman-core/tests/test_gateway_loopback_proxy.py); P0-B монотонная алгебра политики DENY⊗X=DENY, hook-ASK⊗AUTO=ASK (eb0e969, command-center/tests/test_policy_algebra.py); Fleet: PRIVATE/LOCAL_ONLY — жёсткий гейт планировщика, CredentialBroker выдаёт только гранты (test_v3_fleet_core.py); Secret scan в каждом CI-прогоне (tools/ci_secret_scan.py); EH-01: ключ подписи улик 0600 вне модели; fail-closed без ключа (bossman_shared/evidence.py); OpenRouter только через env → ключ в vault, не в репозитории/логах/API (3e673d3, test_feat_openrouter_env_bootstrap.py) |
| 3 | Tooling / OS Integration | 7.0/10 | INTEGRATED | MEDIUM | V2 реестр инструментов/decide_effect/approvals заморожен на ffda281 и покрыт command-center/tests/test_v21_*; V3-порты → живой bcc (bossman-core/tests/test_v3_command_center_adapters.py) |
| 4 | Organization Layer | 7.0/10 | INTEGRATED | MEDIUM | OrganizationRuntime над V3ExecutionBridge/FleetExecutionBridge; ORG-03..07, MEM-02 закрыты (084ad3a); E2E: родитель не COMPLETE при непроверенном ребёнке, рестарт без дублей (bossman-core/tests/test_v3_organization_e2e.py); ORG-01/02: фича `/api/org/*` за флагами, агент организации → агент V2, задача+run V2 на контракт; PlannerPort/DeterministicPlanner; контракт без шагов → BLOCKED/no_executable_steps (efaa55f, test_v3_organization_planner.py, command-center/tests/test_feat_organization.py) |
| 5 | Fleet & Resources | 6.5/10 | INTEGRATED | MEDIUM | FleetStore/LeaseManager/WorkQueue CAS-claim, fencing, reclaim (bossman-core/tests/test_v3_fleet_core.py, 20 тестов); E2E #1–#4: размещение→исполнение, смерть узла→resume без дублей, приватность, двойной claim (test_v3_fleet_e2e.py); FL-01: task_runs.fence, условные записи и heartbeat, assert_fence до эффекта в V2 и в V3-адаптере (2487694, test_fence_fl01.py) |
| 6 | Memory / Context | 6.2/10 | IMPLEMENTED | MEDIUM | TaskJournal + FailureMemory + ContextAssembler с редакцией (bossman_v3/memory); ScopedKnowledge: явное наследование include_parents, экспорт по allowlist (MEM-02, 084ad3a) |
| 7 | Testing / CI | 7.0/10 | VERIFIED | MEDIUM | 4 workflow (root-ci, Bossman Core CI, Command Center CI, V2 Auto-Repair) зелёные по точному SHA 714bb01/fb201a4; Полный регресс ядра на стабильном HEAD; benchmark-тесты проверяют SHA; README_SCORECARD_CURRENT проверяется в root-ci (scripts/update_readme_scorecard.py --check); Пассивный benchmark overlay: 9 hard fail'ов, 5 стресс-бенчмарков над реальными Organization/Fleet/CompoundRunner, мост в scorecard `--from-benchmark` (bossman-core/tests/test_v3_benchmark_overlay.py, test_v3_org_benchmark.py) |
| 8 | Observability / CEO Control | 5.5/10 | PARTIAL | LOW | control_plane снимки организации и флота из durable store (bossman_v3/organization/control_plane.py, fleet/control_plane.py); GET /api/control-plane: organization/queue/treasury/fleet/slo/attention из durable-источников, снимок совпадает после рестарта (5709611, command-center/tests/test_feat_control_plane.py); AST-скан: события и run-лог не несут messages/prompt/api_key/cookie/token (test_no_private_fields_in_events.py) |
| 9 | Treasury / Cost | 6.5/10 | IMPLEMENTED | MEDIUM | TR-01/02/03: актуальные цены 5 семейств (provisional), токен-оценка по скрипту, потолок in·max(p_in,p_cw)+out·p_out (e724a44, tests/test_fable_budget_pricing.py); ResourceTreasury: INV-3 PartitionViolation, конверты org→dept→mission (test_v3_organization_core.py) |
| 10 | Mission UX / Command Center | 6.0/10 | IMPLEMENTED | MEDIUM | Command Center UI (command-center/ui) с approvals, задачами, инструментами; command-center/tests зелёные; Компактная навигация + OpenRouter Connect (исправлен appendChild) — ветка claude/v2-ui-sidebar-compact, 1401 passed, НЕ влита; Данные для страницы владельца доступны: `/api/control-plane`, `/api/org/snapshot` (UI-страница — TZ-10, не сделана) |

- **Current bottleneck:** EH-02/EH-04: улики подписаны (EH-01), fence движка закрывает дубли (FL-01), Organization доступна как продукт (ORG-01/02), но верификаторы пост-состояния есть лишь для 4 семейств и нет единой точки finalize() — Execution Truth не может стать ATTESTED; флот и SLO не подключены к Command Center (control-plane честно показывает enabled=false / NOT_IMPLEMENTED).
- **Next highest-value fix:** TZ-01 §2.2–2.3: ActionReceipt + детерминированные верификаторы пост-состояния (terminal/files/apps/github) и единый finalize() с grep-тестом; затем TZ-08 §2.2 гистограммы/SLO и ретеншн событий.
- **Last evidence SHA:** `4c8fec2c0ceada7f5085373e067e1f01b289398e` · **Current HEAD SHA:** `268460015e31` · **Evidence freshness:** PARTIALLY_STALE
- **Last scorecard update:** 2026-09-05
- **Benchmark hard failures:** none observed
- **Live hardware attestation:** PENDING
- **Exact-SHA CI:** NOT_RUN

_Среднее (вторично, не авторитетно): 6.8/10. 10.0 = ATTESTED; ни одна ось не ATTESTED без живой аттестации железа._

## Execution Truth — 8.5 · VERIFIED

- evidence: EH-01: улика verified=True доверяется только с HMAC-подписью доверенного signer'а; журнал подписывает закрытый шаг (000f331, bossman-core/tests/test_v3_evidence_signing.py)
- evidence: FL-01: fence движка — зомби-воркер не пишет receipt/статус, внешний эффект не повторяется (2487694, command-center/tests/test_fence_fl01.py)
- evidence: TaskJournal: finished = receipt ∧ verified (bossman-core/tests/test_v3_memory_kernel.py, test_v3_invariants.py, test_v3_compound_resume.py)
- evidence: FleetExecutionBridge отбрасывает поддельные verified-улики, пересобирая их из журнала (test_v3_fleet_e2e.py::forged evidence, 084ad3a)
- evidence: PLACED→VERIFIED запрещён в LEGAL_TRANSITIONS (test_v3_fleet_core.py)
- evidence: V2 action contract тесты (command-center/tests/test_action_contract.py)
- evidence: EH-05: FAIL гейта без явного requeue = сбой гейта (4c8fec2, command-center/tests/test_gate_contract_requeue.py)
- blocker: EH-02: верификаторы пост-состояния только для file/db/browser/app (TZ-01 §2.2)
- blocker: EH-04: 7 независимых записей completed, нет finalize() (TZ-01 §2.3)
- blocker: bcc.v2.verification не подписывает (V2 заморожен — через адаптер)
- tests: bossman-core/tests/test_v3_evidence_signing.py
- tests: command-center/tests/test_fence_fl01.py
- tests: bossman-core/tests/test_v3_fleet_e2e.py
- tests: bossman-core/tests/test_v3_organization_e2e.py
- tests: command-center/tests/test_action_contract.py
- last_verified_sha: `4c8fec2c0ceada7f5085373e067e1f01b289398e` · last_verified_at: 2026-09-05 · live_attestation: NOT_REQUIRED · regression_delta: +0.5

## Security — 8.0 · VERIFIED

- evidence: P0-A gateway loopback fail-closed при proxy-заголовках (cbdabf2, bossman-core/tests/test_gateway_loopback_proxy.py)
- evidence: P0-B монотонная алгебра политики DENY⊗X=DENY, hook-ASK⊗AUTO=ASK (eb0e969, command-center/tests/test_policy_algebra.py)
- evidence: Fleet: PRIVATE/LOCAL_ONLY — жёсткий гейт планировщика, CredentialBroker выдаёт только гранты (test_v3_fleet_core.py)
- evidence: Secret scan в каждом CI-прогоне (tools/ci_secret_scan.py)
- evidence: EH-01: ключ подписи улик 0600 вне модели; fail-closed без ключа (bossman_shared/evidence.py)
- evidence: OpenRouter только через env → ключ в vault, не в репозитории/логах/API (3e673d3, test_feat_openrouter_env_bootstrap.py)
- blocker: TZ-02: scan 2.0 (энтропия), rate-limit шлюза, hash-chain журнала
- blocker: Tailscale-Serve руководство оператора
- tests: bossman-core/tests/test_gateway_loopback_proxy.py
- tests: command-center/tests/test_policy_algebra.py
- tests: bossman-core/tests/test_v3_fleet_core.py
- last_verified_sha: `4c8fec2c0ceada7f5085373e067e1f01b289398e` · last_verified_at: 2026-09-05 · live_attestation: NOT_REQUIRED · regression_delta: +0.8

## Tooling / OS Integration — 7.0 · INTEGRATED

- evidence: V2 реестр инструментов/decide_effect/approvals заморожен на ffda281 и покрыт command-center/tests/test_v21_*
- evidence: V3-порты → живой bcc (bossman-core/tests/test_v3_command_center_adapters.py)
- blocker: TZ-03: CapabilitySpec, выдача инструмента на run, детектор no-progress
- tests: command-center/tests/test_v21_tool_loop.py
- tests: bossman-core/tests/test_v3_command_center_adapters.py
- last_verified_sha: `4c8fec2c0ceada7f5085373e067e1f01b289398e` · last_verified_at: 2026-09-05 · live_attestation: NOT_REQUIRED · regression_delta: +0.0

## Organization Layer — 7.0 · INTEGRATED

- evidence: OrganizationRuntime над V3ExecutionBridge/FleetExecutionBridge; ORG-03..07, MEM-02 закрыты (084ad3a)
- evidence: E2E: родитель не COMPLETE при непроверенном ребёнке, рестарт без дублей (bossman-core/tests/test_v3_organization_e2e.py)
- evidence: ORG-01/02: фича `/api/org/*` за флагами, агент организации → агент V2, задача+run V2 на контракт; PlannerPort/DeterministicPlanner; контракт без шагов → BLOCKED/no_executable_steps (efaa55f, test_v3_organization_planner.py, command-center/tests/test_feat_organization.py)
- blocker: ORG-08: saga-компенсации
- blocker: модельный планировщик (TZ-03 §2.5)
- blocker: HTTP-E2E исполнения через V2 с реальным агентом
- blocker: usd как view над fable_budget (TR-04)
- tests: bossman-core/tests/test_v3_organization_core.py
- tests: bossman-core/tests/test_v3_organization_e2e.py
- tests: bossman-core/tests/test_v3_organization_company_bridge.py
- tests: bossman-core/tests/test_v3_organization_planner.py
- tests: command-center/tests/test_feat_organization.py
- last_verified_sha: `4c8fec2c0ceada7f5085373e067e1f01b289398e` · last_verified_at: 2026-09-05 · live_attestation: NOT_REQUIRED · regression_delta: +0.5

## Fleet & Resources — 6.5 · INTEGRATED

- evidence: FleetStore/LeaseManager/WorkQueue CAS-claim, fencing, reclaim (bossman-core/tests/test_v3_fleet_core.py, 20 тестов)
- evidence: E2E #1–#4: размещение→исполнение, смерть узла→resume без дублей, приватность, двойной claim (test_v3_fleet_e2e.py)
- evidence: FL-01: task_runs.fence, условные записи и heartbeat, assert_fence до эффекта в V2 и в V3-адаптере (2487694, test_fence_fl01.py)
- blocker: RemoteNodeTransport: REMOTE_TRANSPORT_PRODUCTION_READY=NO
- blocker: нет живого многоузлового прогона (recovery-время не измерено)
- blocker: TZ-05 §3 реестр/планировщик в V2-пути (BCC_FLEET_ENABLED нет), UNIQUE idem-индекс
- tests: bossman-core/tests/test_v3_fleet_core.py
- tests: bossman-core/tests/test_v3_fleet_e2e.py
- tests: command-center/tests/test_fence_fl01.py
- last_verified_sha: `4c8fec2c0ceada7f5085373e067e1f01b289398e` · last_verified_at: 2026-09-05 · live_attestation: PENDING · regression_delta: +0.5

## Memory / Context — 6.2 · IMPLEMENTED

- evidence: TaskJournal + FailureMemory + ContextAssembler с редакцией (bossman_v3/memory)
- evidence: ScopedKnowledge: явное наследование include_parents, экспорт по allowlist (MEM-02, 084ad3a)
- blocker: TZ-06: решётка скоупов по дереву, ScopeToken, эмбеддинги, токен-оценка контекста
- blocker: слияние с bossman.context_engine (колонка scope)
- tests: bossman-core/tests/test_v3_organization_core.py
- tests: bossman-core/tests/test_v3_memory_kernel.py
- last_verified_sha: `4c8fec2c0ceada7f5085373e067e1f01b289398e` · last_verified_at: 2026-09-05 · live_attestation: NOT_REQUIRED · regression_delta: +0.0

## Testing / CI — 7.0 · VERIFIED

- evidence: 4 workflow (root-ci, Bossman Core CI, Command Center CI, V2 Auto-Repair) зелёные по точному SHA 714bb01/fb201a4
- evidence: Полный регресс ядра на стабильном HEAD; benchmark-тесты проверяют SHA
- evidence: README_SCORECARD_CURRENT проверяется в root-ci (scripts/update_readme_scorecard.py --check)
- evidence: Пассивный benchmark overlay: 9 hard fail'ов, 5 стресс-бенчмарков над реальными Organization/Fleet/CompoundRunner, мост в scorecard `--from-benchmark` (bossman-core/tests/test_v3_benchmark_overlay.py, test_v3_org_benchmark.py)
- blocker: TZ-07: windows-job отсутствует, coverage gate, реестр skips
- blocker: CI по текущему HEAD ещё не наблюдался (NOT_RUN ≠ PASS)
- blocker: benchmark-report.json ещё не пишется регулярным прогоном (только тестами)
- tests: tests/test_readme_scorecard.py
- tests: .github/workflows/*.yml
- tests: bossman-core/tests/test_v3_benchmark_overlay.py
- tests: bossman-core/tests/test_v3_org_benchmark.py
- last_verified_sha: `fb201a4` · last_verified_at: 2026-09-05 · live_attestation: NOT_REQUIRED · regression_delta: +0.0

## Observability / CEO Control — 5.5 · PARTIAL

- evidence: control_plane снимки организации и флота из durable store (bossman_v3/organization/control_plane.py, fleet/control_plane.py)
- evidence: GET /api/control-plane: organization/queue/treasury/fleet/slo/attention из durable-источников, снимок совпадает после рестарта (5709611, command-center/tests/test_feat_control_plane.py)
- evidence: AST-скан: события и run-лог не несут messages/prompt/api_key/cookie/token (test_no_private_fields_in_events.py)
- blocker: TZ-08 §2.1 ретеншн событий
- blocker: §2.2 гистограммы задержек, SLO, burn-rate алерт
- blocker: §2.3 span'ы
- blocker: §2.4 цепочка INV-5
- blocker: §2.6 dead-click
- blocker: UI-страница control-plane (TZ-10)
- tests: command-center/tests/test_feat_control_plane.py
- tests: command-center/tests/test_no_private_fields_in_events.py
- tests: bossman-core/tests/test_v3_organization_core.py
- last_verified_sha: `4c8fec2c0ceada7f5085373e067e1f01b289398e` · last_verified_at: 2026-09-05 · live_attestation: NOT_REQUIRED · regression_delta: +0.5

## Treasury / Cost — 6.5 · IMPLEMENTED

- evidence: TR-01/02/03: актуальные цены 5 семейств (provisional), токен-оценка по скрипту, потолок in·max(p_in,p_cw)+out·p_out (e724a44, tests/test_fable_budget_pricing.py)
- evidence: ResourceTreasury: INV-3 PartitionViolation, конверты org→dept→mission (test_v3_organization_core.py)
- blocker: TR-04: usd как view над ledger
- blocker: TR-05: локальный GPU
- blocker: TR-06: burn-rate
- blocker: цены provisional (AS_OF 2026-09-05)
- tests: tests/test_fable_budget_pricing.py
- tests: command-center/tests/test_fable_hard_cap.py
- tests: bossman-core/tests/test_v3_organization_core.py
- last_verified_sha: `4c8fec2c0ceada7f5085373e067e1f01b289398e` · last_verified_at: 2026-09-05 · live_attestation: NOT_REQUIRED · regression_delta: +0.5

## Mission UX / Command Center — 6.0 · IMPLEMENTED

- evidence: Command Center UI (command-center/ui) с approvals, задачами, инструментами; command-center/tests зелёные
- evidence: Компактная навигация + OpenRouter Connect (исправлен appendChild) — ветка claude/v2-ui-sidebar-compact, 1401 passed, НЕ влита
- evidence: Данные для страницы владельца доступны: `/api/control-plane`, `/api/org/snapshot` (UI-страница — TZ-10, не сделана)
- blocker: TZ-10: статусы blocked/capability_unavailable, aria, страница control-plane
- blocker: дизайн-ветка не влита в control
- tests: command-center/tests/test_ux2_pages_sweep.py
- tests: command-center/tests/test_command_bar_ui.py
- last_verified_sha: `4c8fec2c0ceada7f5085373e067e1f01b289398e` · last_verified_at: 2026-09-05 · live_attestation: NOT_REQUIRED · regression_delta: +0.0

## Deterministic counters

- false_success_count: 0
- duplicate_side_effect_count: 0
- privacy_violation_count: 0
- permission_bypass_count: 0
- source: bossman-core/tests/test_v3_org_benchmark.py (A–E), test_v3_benchmark_overlay.py, test_v3_fleet_e2e.py, command-center/tests/test_fence_fl01.py at last_evidence_sha; deterministic suites, no live run recorded
