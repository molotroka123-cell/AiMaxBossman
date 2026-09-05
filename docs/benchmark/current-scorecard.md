# Current scorecard (rendered from current-scorecard.json)

| # | Ось системы | Оценка | Статус | Уверенность | Улики |
|---|---|---:|---|---|---|
| 1 | Execution Truth | 8.0/10 | VERIFIED | HIGH | TaskJournal: finished = receipt ∧ verified (bossman-core/tests/test_v3_memory_kernel.py, test_v3_invariants.py, test_v3_compound_resume.py); FleetExecutionBridge отбрасывает поддельные verified-улики, пересобирая их из журнала (test_v3_fleet_e2e.py::forged evidence, 084ad3a); PLACED→VERIFIED запрещён в LEGAL_TRANSITIONS (test_v3_fleet_core.py); V2 action contract тесты (command-center/tests/test_action_contract.py) |
| 2 | Security | 8.0/10 | VERIFIED | HIGH | P0-A gateway loopback fail-closed при proxy-заголовках (cbdabf2, bossman-core/tests/test_gateway_loopback_proxy.py); P0-B монотонная алгебра политики DENY⊗X=DENY, hook-ASK⊗AUTO=ASK (eb0e969, command-center/tests/test_policy_algebra.py); Fleet: PRIVATE/LOCAL_ONLY — жёсткий гейт планировщика, CredentialBroker выдаёт только гранты (test_v3_fleet_core.py); Secret scan в каждом CI-прогоне (tools/ci_secret_scan.py) |
| 3 | Tooling / OS Integration | 7.0/10 | INTEGRATED | MEDIUM | V2 реестр инструментов/decide_effect/approvals заморожен на ffda281 и покрыт command-center/tests/test_v21_*; V3-порты → живой bcc (bossman-core/tests/test_v3_command_center_adapters.py) |
| 4 | Organization Layer | 6.5/10 | INTEGRATED | MEDIUM | OrganizationRuntime над V3ExecutionBridge/FleetExecutionBridge; ORG-03..07, MEM-02 закрыты (084ad3a); E2E: родитель не COMPLETE при непроверенном ребёнке, рестарт без дублей (bossman-core/tests/test_v3_organization_e2e.py) |
| 5 | Fleet & Resources | 6.0/10 | INTEGRATED | MEDIUM | FleetStore/LeaseManager/WorkQueue CAS-claim, fencing, reclaim (bossman-core/tests/test_v3_fleet_core.py, 20 тестов); E2E #1–#4: размещение→исполнение, смерть узла→resume без дублей, приватность, двойной claim (test_v3_fleet_e2e.py) |
| 6 | Memory / Context | 6.2/10 | IMPLEMENTED | MEDIUM | TaskJournal + FailureMemory + ContextAssembler с редакцией (bossman_v3/memory); ScopedKnowledge: явное наследование include_parents, экспорт по allowlist (MEM-02, 084ad3a) |
| 7 | Testing / CI | 7.0/10 | VERIFIED | MEDIUM | 4 workflow (root-ci, Bossman Core CI, Command Center CI, V2 Auto-Repair) зелёные по точному SHA 714bb01/fb201a4; Полный регресс ядра на стабильном HEAD; benchmark-тесты проверяют SHA; README_SCORECARD_CURRENT проверяется в root-ci (scripts/update_readme_scorecard.py --check) |
| 8 | Observability / CEO Control | 5.0/10 | PARTIAL | LOW | control_plane снимки организации и флота из durable store (bossman_v3/organization/control_plane.py, fleet/control_plane.py) |
| 9 | Treasury / Cost | 6.5/10 | IMPLEMENTED | MEDIUM | TR-01/02/03: актуальные цены 5 семейств (provisional), токен-оценка по скрипту, потолок in·max(p_in,p_cw)+out·p_out (e724a44, tests/test_fable_budget_pricing.py); ResourceTreasury: INV-3 PartitionViolation, конверты org→dept→mission (test_v3_organization_core.py) |
| 10 | Mission UX / Command Center | 6.0/10 | IMPLEMENTED | MEDIUM | Command Center UI (command-center/ui) с approvals, задачами, инструментами; command-center/tests зелёные; Компактная навигация + OpenRouter Connect (исправлен appendChild) — ветка claude/v2-ui-sidebar-compact, 1401 passed, НЕ влита |

- **Current bottleneck:** FL-01: лизинг/fence флота не проверяется на стороне V2-движка (двойное исполнение при split-brain доказуемо только на уровне FleetStore), плюс EH-01: улики журнала не подписаны (HMAC) — Execution Truth и Fleet не могут подняться выше VERIFIED.
- **Next highest-value fix:** TZ-05 §2: fence-проверка при каждом побочном эффекте в адаптере V3→bcc (отказ с STALE_FENCE, тест split-brain на 2 узла), затем TZ-01 подпись улик.
- **Last evidence SHA:** `eb0e969b275141873bc404704d44eafc1f5a22e6` · **Current HEAD SHA:** `eb0e969b2751` · **Evidence freshness:** FRESH
- **Last scorecard update:** 2026-09-05
- **Benchmark hard failures:** none observed
- **Live hardware attestation:** PENDING
- **Exact-SHA CI:** NOT_RUN

_Среднее (вторично, не авторитетно): 6.6/10. 10.0 = ATTESTED; ни одна ось не ATTESTED без живой аттестации железа._

## Execution Truth — 8.0 · VERIFIED

- evidence: TaskJournal: finished = receipt ∧ verified (bossman-core/tests/test_v3_memory_kernel.py, test_v3_invariants.py, test_v3_compound_resume.py)
- evidence: FleetExecutionBridge отбрасывает поддельные verified-улики, пересобирая их из журнала (test_v3_fleet_e2e.py::forged evidence, 084ad3a)
- evidence: PLACED→VERIFIED запрещён в LEGAL_TRANSITIONS (test_v3_fleet_core.py)
- evidence: V2 action contract тесты (command-center/tests/test_action_contract.py)
- blocker: EH-01: улики без подписи/hash-chain (TZ-01)
- blocker: EH-02: независимые верификаторы не вынесены (TZ-01)
- tests: bossman-core/tests/test_v3_fleet_e2e.py
- tests: bossman-core/tests/test_v3_organization_e2e.py
- tests: command-center/tests/test_action_contract.py
- last_verified_sha: `eb0e969b275141873bc404704d44eafc1f5a22e6` · last_verified_at: 2026-09-05 · live_attestation: NOT_REQUIRED · regression_delta: +0.0

## Security — 8.0 · VERIFIED

- evidence: P0-A gateway loopback fail-closed при proxy-заголовках (cbdabf2, bossman-core/tests/test_gateway_loopback_proxy.py)
- evidence: P0-B монотонная алгебра политики DENY⊗X=DENY, hook-ASK⊗AUTO=ASK (eb0e969, command-center/tests/test_policy_algebra.py)
- evidence: Fleet: PRIVATE/LOCAL_ONLY — жёсткий гейт планировщика, CredentialBroker выдаёт только гранты (test_v3_fleet_core.py)
- evidence: Secret scan в каждом CI-прогоне (tools/ci_secret_scan.py)
- blocker: TZ-02: scan 2.0 (энтропия), rate-limit шлюза, hash-chain журнала
- blocker: Tailscale-Serve руководство оператора
- tests: bossman-core/tests/test_gateway_loopback_proxy.py
- tests: command-center/tests/test_policy_algebra.py
- tests: bossman-core/tests/test_v3_fleet_core.py
- last_verified_sha: `eb0e969b275141873bc404704d44eafc1f5a22e6` · last_verified_at: 2026-09-05 · live_attestation: NOT_REQUIRED · regression_delta: +0.8

## Tooling / OS Integration — 7.0 · INTEGRATED

- evidence: V2 реестр инструментов/decide_effect/approvals заморожен на ffda281 и покрыт command-center/tests/test_v21_*
- evidence: V3-порты → живой bcc (bossman-core/tests/test_v3_command_center_adapters.py)
- blocker: TZ-03: CapabilitySpec, выдача инструмента на run, детектор no-progress
- tests: command-center/tests/test_v21_tool_loop.py
- tests: bossman-core/tests/test_v3_command_center_adapters.py
- last_verified_sha: `eb0e969b275141873bc404704d44eafc1f5a22e6` · last_verified_at: 2026-09-05 · live_attestation: NOT_REQUIRED · regression_delta: +0.0

## Organization Layer — 6.5 · INTEGRATED

- evidence: OrganizationRuntime над V3ExecutionBridge/FleetExecutionBridge; ORG-03..07, MEM-02 закрыты (084ad3a)
- evidence: E2E: родитель не COMPLETE при непроверенном ребёнке, рестарт без дублей (bossman-core/tests/test_v3_organization_e2e.py)
- blocker: ORG-01: HTTP-фича/точка входа продукта
- blocker: ORG-02: планировщик миссий
- blocker: ORG-08: saga-компенсации
- tests: bossman-core/tests/test_v3_organization_core.py
- tests: bossman-core/tests/test_v3_organization_e2e.py
- tests: bossman-core/tests/test_v3_organization_company_bridge.py
- last_verified_sha: `eb0e969b275141873bc404704d44eafc1f5a22e6` · last_verified_at: 2026-09-05 · live_attestation: NOT_REQUIRED · regression_delta: +0.0

## Fleet & Resources — 6.0 · INTEGRATED

- evidence: FleetStore/LeaseManager/WorkQueue CAS-claim, fencing, reclaim (bossman-core/tests/test_v3_fleet_core.py, 20 тестов)
- evidence: E2E #1–#4: размещение→исполнение, смерть узла→resume без дублей, приватность, двойной claim (test_v3_fleet_e2e.py)
- blocker: FL-01: fence не проверяется V2-движком (TZ-05 §2)
- blocker: RemoteNodeTransport: REMOTE_TRANSPORT_PRODUCTION_READY=NO
- blocker: нет живого многоузлового прогона (recovery-время не измерено)
- tests: bossman-core/tests/test_v3_fleet_core.py
- tests: bossman-core/tests/test_v3_fleet_e2e.py
- last_verified_sha: `eb0e969b275141873bc404704d44eafc1f5a22e6` · last_verified_at: 2026-09-05 · live_attestation: PENDING · regression_delta: +0.0

## Memory / Context — 6.2 · IMPLEMENTED

- evidence: TaskJournal + FailureMemory + ContextAssembler с редакцией (bossman_v3/memory)
- evidence: ScopedKnowledge: явное наследование include_parents, экспорт по allowlist (MEM-02, 084ad3a)
- blocker: TZ-06: решётка скоупов по дереву, ScopeToken, эмбеддинги, токен-оценка контекста
- blocker: слияние с bossman.context_engine (колонка scope)
- tests: bossman-core/tests/test_v3_organization_core.py
- tests: bossman-core/tests/test_v3_memory_kernel.py
- last_verified_sha: `eb0e969b275141873bc404704d44eafc1f5a22e6` · last_verified_at: 2026-09-05 · live_attestation: NOT_REQUIRED · regression_delta: +0.0

## Testing / CI — 7.0 · VERIFIED

- evidence: 4 workflow (root-ci, Bossman Core CI, Command Center CI, V2 Auto-Repair) зелёные по точному SHA 714bb01/fb201a4
- evidence: Полный регресс ядра на стабильном HEAD; benchmark-тесты проверяют SHA
- evidence: README_SCORECARD_CURRENT проверяется в root-ci (scripts/update_readme_scorecard.py --check)
- blocker: TZ-07: windows-job отсутствует, coverage gate, реестр skips
- blocker: CI по текущему HEAD ещё не наблюдался (NOT_RUN ≠ PASS)
- tests: tests/test_readme_scorecard.py
- tests: .github/workflows/*.yml
- last_verified_sha: `fb201a4` · last_verified_at: 2026-09-05 · live_attestation: NOT_REQUIRED · regression_delta: +0.0

## Observability / CEO Control — 5.0 · PARTIAL

- evidence: control_plane снимки организации и флота из durable store (bossman_v3/organization/control_plane.py, fleet/control_plane.py)
- blocker: TZ-08: span'ы, dead-click, страница control-plane в Command Center
- blocker: нет единого владельческого дашборда по миссиям
- tests: bossman-core/tests/test_v3_organization_core.py
- last_verified_sha: `eb0e969b275141873bc404704d44eafc1f5a22e6` · last_verified_at: 2026-09-05 · live_attestation: NOT_REQUIRED · regression_delta: +0.0

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
- last_verified_sha: `eb0e969b275141873bc404704d44eafc1f5a22e6` · last_verified_at: 2026-09-05 · live_attestation: NOT_REQUIRED · regression_delta: +0.5

## Mission UX / Command Center — 6.0 · IMPLEMENTED

- evidence: Command Center UI (command-center/ui) с approvals, задачами, инструментами; command-center/tests зелёные
- evidence: Компактная навигация + OpenRouter Connect (исправлен appendChild) — ветка claude/v2-ui-sidebar-compact, 1401 passed, НЕ влита
- blocker: TZ-10: статусы blocked/capability_unavailable, aria, страница control-plane
- blocker: дизайн-ветка не влита в control
- tests: command-center/tests/test_ux2_pages_sweep.py
- tests: command-center/tests/test_command_bar_ui.py
- last_verified_sha: `eb0e969b275141873bc404704d44eafc1f5a22e6` · last_verified_at: 2026-09-05 · live_attestation: NOT_REQUIRED · regression_delta: +0.0

## Deterministic counters

- false_success_count: 0
- duplicate_side_effect_count: 0
- privacy_violation_count: 0
- permission_bypass_count: 0
- source: bossman-core/tests/test_v3_fleet_e2e.py, test_v3_organization_e2e.py at last_evidence_sha (deterministic suites; no live benchmark run recorded)
