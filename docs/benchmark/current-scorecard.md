# Current scorecard (rendered from current-scorecard.json)

| # | Ось системы | Оценка | Статус | Уверенность | Улики |
|---|---|---:|---|---|---|
| 1 | Execution Truth | 8.8/10 | VERIFIED | HIGH | EH-01: улика verified=True доверяется только с HMAC-подписью доверенного signer'а; журнал подписывает закрытый шаг (000f331, bossman-core/tests/test_v3_evidence_signing.py); FL-01: fence движка — зомби-воркер не пишет receipt/статус, внешний эффект не повторяется (2487694, command-center/tests/test_fence_fl01.py); TaskJournal: finished = receipt ∧ verified (bossman-core/tests/test_v3_memory_kernel.py, test_v3_invariants.py, test_v3_compound_resume.py); FleetExecutionBridge отбрасывает поддельные verified-улики, пересобирая их из журнала (test_v3_fleet_e2e.py::forged evidence, 084ad3a); PLACED→VERIFIED запрещён в LEGAL_TRANSITIONS (test_v3_fleet_core.py); V2 action contract тесты (command-center/tests/test_action_contract.py); EH-05: FAIL гейта без явного requeue = сбой гейта (4c8fec2, command-center/tests/test_gate_contract_requeue.py); ActionReceipt (fence/observation/freshness) + канонический bcc/finalize.py — единственная запись completed (TRUTH-003, d6260ad/db5defb, test_finalize_gate.py, test_no_direct_completed_writes.py); ASTRA-001..005: completion obligations Core, bind_plan/execution_binding журнала, durable intent до эффекта, crash после необратимого эффекта → без повторa (2077bbf, test_astra_remediation.py); Post-state verifiers terminal/github/memory/schedule/process; observe_pid: расхождение источников → UNVERIFIED (test_v2_poststate_verifiers.py) |
| 2 | Security | 8.5/10 | VERIFIED | HIGH | P0-A gateway loopback fail-closed при proxy-заголовках (cbdabf2, bossman-core/tests/test_gateway_loopback_proxy.py); P0-B монотонная алгебра политики DENY⊗X=DENY, hook-ASK⊗AUTO=ASK (eb0e969, command-center/tests/test_policy_algebra.py); Fleet: PRIVATE/LOCAL_ONLY — жёсткий гейт планировщика, CredentialBroker выдаёт только гранты (test_v3_fleet_core.py); Secret scan в каждом CI-прогоне (tools/ci_secret_scan.py); EH-01: ключ подписи улик 0600 вне модели; fail-closed без ключа (bossman_shared/evidence.py); OpenRouter только через env → ключ в vault, не в репозитории/логах/API (3e673d3, test_feat_openrouter_env_bootstrap.py); SEC-01 секрет-скан 2.0: 13 семейств паттернов, энтропия, ZIP по содержимому, запрещённые файлы (tests/test_ci_secret_scan.py); SEC-03 rate-limit/lockout на /api/login до сравнения токена (command-center/tests/test_login_rate_limit.py); SEC-101/102: pinned HTTP transport, CGNAT запрещён, DNS вне event loop, поток ≤2 МБ (test_astra_remediation.py); SEC-103: рекурсивный fail-closed ZIP-скан; SAST/SCA gate блокирует CI (tools/astra_security_gate.py, ffd7d25); CSRF 403 несёт code=csrf → повторный вход, политика не ослаблена (d114e30, test_browser_navigation_ui.py) |
| 3 | Tooling / OS Integration | 7.5/10 | INTEGRATED | MEDIUM | V2 реестр инструментов/decide_effect/approvals заморожен на ffda281 и покрыт command-center/tests/test_v21_*; V3-порты → живой bcc (bossman-core/tests/test_v3_command_center_adapters.py); OpenRouter как провайдер из окружения (ключ+модели — данные), тот же tool loop через фейковый провайдер детерминированно (command-center/tests/test_feat_openrouter_agent_flow.py); CapabilitySpec как узкий адаптер над ToolSpec: манифест /api/capabilities, правило выдачи capability ∧ policy ∧ runtime, неизмеренная предпосылка = отказ, инструмент без верификатора помечен provable=False (command-center/tests/test_capability_manifest.py) |
| 4 | Organization Layer | 7.3/10 | INTEGRATED | MEDIUM | OrganizationRuntime над V3ExecutionBridge/FleetExecutionBridge; ORG-03..07, MEM-02 закрыты (084ad3a); E2E: родитель не COMPLETE при непроверенном ребёнке, рестарт без дублей (bossman-core/tests/test_v3_organization_e2e.py); ORG-01/02: фича `/api/org/*` за флагами, агент организации → агент V2, задача+run V2 на контракт; PlannerPort/DeterministicPlanner; контракт без шагов → BLOCKED/no_executable_steps (efaa55f, test_v3_organization_planner.py, command-center/tests/test_feat_organization.py); O001–O007 закрыты: private→нет cloud даже в fallback, атомарный intake, невалидные ресурсы, veto риск-ревьюера, атомарный бюджет, владение scope (2077bbf, test_astra_remediation.py) |
| 5 | Fleet & Resources | 7.0/10 | INTEGRATED | MEDIUM | FleetStore/LeaseManager/WorkQueue CAS-claim, fencing, reclaim (bossman-core/tests/test_v3_fleet_core.py, 20 тестов); E2E #1–#4: размещение→исполнение, смерть узла→resume без дублей, приватность, двойной claim (test_v3_fleet_e2e.py); FL-01: task_runs.fence, условные записи и heartbeat, assert_fence до эффекта в V2 и в V3-адаптере (2487694, test_fence_fl01.py); 10 safety proofs (реестр + истёкшая аренда без власти + размещение не штрафует исполнителя); REMOTE_TRANSPORT_PRODUCTION_READY=NO, NODE_AUTH_PRODUCTION_READY=NO (test_v3_fleet_safety_proofs.py); F001–F006: shared/exclusive аренды, guard до мутации, claim_fence на ACK/COMPLETE, backoff/WAIT_HUMAN в SQLite, единый пул RAM+GPU, MINIMIZED-гейт (2077bbf, test_astra_remediation.py, test_v3_fleet_e2e.py) |
| 6 | Memory / Context | 6.5/10 | IMPLEMENTED | MEDIUM | TaskJournal + FailureMemory + ContextAssembler с редакцией (bossman_v3/memory); ScopedKnowledge: явное наследование include_parents, экспорт по allowlist (MEM-02, 084ad3a); Журнал: exclusive writer lock + CAS, устаревший writer не стирает durable intent (test_reaudit_journal_*); scope ownership по хранилищу (ASTRA-009); лимит V3 включает заголовок/сериализацию (ASTRA-010) |
| 7 | Testing / CI | 8.0/10 | VERIFIED | MEDIUM | 4 workflow (root-ci, Bossman Core CI, Command Center CI, V2 Auto-Repair) зелёные по точному SHA 714bb01/fb201a4; Полный регресс ядра на стабильном HEAD; benchmark-тесты проверяют SHA; README_SCORECARD_CURRENT проверяется в root-ci (scripts/update_readme_scorecard.py --check); Пассивный benchmark overlay: 9 hard fail'ов, 5 стресс-бенчмарков над реальными Organization/Fleet/CompoundRunner, мост в scorecard `--from-benchmark` (bossman-core/tests/test_v3_benchmark_overlay.py, test_v3_org_benchmark.py); Сквозной E2E миссия→организация→флот→узел→V3→файл→свежее чтение→подписанные улики→VERIFIED→ревью→COMPLETE→benchmark→scorecard (bossman-core/tests/test_v3_cross_layer_e2e.py); Реестр 10 доказательств безопасности флота, каждое привязано к существующему тесту (test_v3_fleet_safety_proofs.py); Windows portable job (astra-acceptance) + windows-paths job; skips registry 89/0 без причины (d7b3519); полный регресс на интегрированном дереве: Core 2047, CC 1471, root 152; VERIFY.py 5/5 PASS; Exact-SHA CI на 38c836b: root-ci PASS, V2 Auto-Repair PASS, ASTRA acceptance PASS (включая windows-latest portable), Solana safety PASS; Core/CC CI на том же SHA отменены более новым пушем (concurrency), не падением; Неснижаемый порог покрытия из измерения: bossman_v3 89% → gate 85% (проверено локально 88.89%), bcc 76% → gate 72% (docs/testing/COVERAGE_BASELINE.md) |
| 8 | Observability / CEO Control | 7.0/10 | PARTIAL | LOW | control_plane снимки организации и флота из durable store (bossman_v3/organization/control_plane.py, fleet/control_plane.py); GET /api/control-plane: organization/queue/treasury/fleet/slo/attention из durable-источников, снимок совпадает после рестарта (5709611, command-center/tests/test_feat_control_plane.py); AST-скан: события и run-лог не несут messages/prompt/api_key/cookie/token (test_no_private_fields_in_events.py); trace_id на весь цикл, ретеншн событий, latency-метрики, Fleet в /api/control-plane (f42bcff, 57f4f31, test_observability_trace.py); Пульт владельца: /api/control-plane → owner_view (КТО/ГДЕ/МОДЕЛЬ/ЧТО/СОСТОЯНИЕ/ПОЧЕМУ/ЦЕНА/ВНИМАНИЕ) и ui/pages/control.js; зелёный COMPLETE только после task.finalized (command-center/tests/test_owner_control_view.py, test_owner_control_ui.py — Playwright, выполнены) |
| 9 | Treasury / Cost | 6.8/10 | IMPLEMENTED | MEDIUM | TR-01/02/03: актуальные цены 5 семейств (provisional), токен-оценка по скрипту, потолок in·max(p_in,p_cw)+out·p_out (e724a44, tests/test_fable_budget_pricing.py); ResourceTreasury: INV-3 PartitionViolation, конверты org→dept→mission (test_v3_organization_core.py); PROD-002: неизвестная цена остаётся null до inference, старый нуль ≠ free; O005/O006 атомарный резерв/списание (test_astra_remediation_cc.py) |
| 10 | Mission UX / Command Center | 6.8/10 | IMPLEMENTED | MEDIUM | Command Center UI (command-center/ui) с approvals, задачами, инструментами; command-center/tests зелёные; Компактная навигация + OpenRouter Connect (исправлен appendChild) — ветка claude/v2-ui-sidebar-compact, 1401 passed, НЕ влита; Данные для страницы владельца доступны: `/api/control-plane`, `/api/org/snapshot` (UI-страница — TZ-10, не сделана); PROD-004 фильтрация миссии до LIMIT + пагинация; CSRF-403 → форма входа вместо мёртвой кнопки (d114e30); журнал тестового периода 51307af16b90: 8 ошибок разобраны; Страница «Пульт» в реестре UI; цвет = уровень доказанности, промпты на экран не выводятся |

- **Current bottleneck:** Полный зелёный набор всех шести workflow по ОДНОМУ SHA ещё не наблюдался: на 38c836b root-ci, V2 Auto-Repair, ASTRA acceptance (включая windows-latest) и Solana safety — PASS, а Core CI и Command Center CI отменены более новым пушем (отменён ≠ пройден). Branch protection выключена (ASTRA-CI-101, действие владельца); аттестация железа и реальный sandbox NOT_RUN; live-приёмка OpenRouter не проводилась (0.00 USD).
- **Next highest-value fix:** Включить branch protection (tools/astra_branch_protection.py --apply) и сделать четыре обязательные проверки required; затем Windows ACL ключей и live-приёмка на дешёвой модели по конфигурации; затем поднять покрытие Command Center по фичам с наименьшим покрытием.
- **Last evidence SHA:** `e26553e56cc3097fd3139a48e3fac8fa3db3ab75` · **Current HEAD SHA:** `e26553e56cc3` · **Evidence freshness:** FRESH
- **Last scorecard update:** 2026-09-05
- **Benchmark hard failures:** none observed
- **Live hardware attestation:** PENDING
- **Exact-SHA CI:** UNPROVEN

_Среднее (вторично, не авторитетно): 7.4/10. 10.0 = ATTESTED; ни одна ось не ATTESTED без живой аттестации железа._

## Execution Truth — 8.8 · VERIFIED

- evidence: EH-01: улика verified=True доверяется только с HMAC-подписью доверенного signer'а; журнал подписывает закрытый шаг (000f331, bossman-core/tests/test_v3_evidence_signing.py)
- evidence: FL-01: fence движка — зомби-воркер не пишет receipt/статус, внешний эффект не повторяется (2487694, command-center/tests/test_fence_fl01.py)
- evidence: TaskJournal: finished = receipt ∧ verified (bossman-core/tests/test_v3_memory_kernel.py, test_v3_invariants.py, test_v3_compound_resume.py)
- evidence: FleetExecutionBridge отбрасывает поддельные verified-улики, пересобирая их из журнала (test_v3_fleet_e2e.py::forged evidence, 084ad3a)
- evidence: PLACED→VERIFIED запрещён в LEGAL_TRANSITIONS (test_v3_fleet_core.py)
- evidence: V2 action contract тесты (command-center/tests/test_action_contract.py)
- evidence: EH-05: FAIL гейта без явного requeue = сбой гейта (4c8fec2, command-center/tests/test_gate_contract_requeue.py)
- evidence: ActionReceipt (fence/observation/freshness) + канонический bcc/finalize.py — единственная запись completed (TRUTH-003, d6260ad/db5defb, test_finalize_gate.py, test_no_direct_completed_writes.py)
- evidence: ASTRA-001..005: completion obligations Core, bind_plan/execution_binding журнала, durable intent до эффекта, crash после необратимого эффекта → без повторa (2077bbf, test_astra_remediation.py)
- evidence: Post-state verifiers terminal/github/memory/schedule/process; observe_pid: расхождение источников → UNVERIFIED (test_v2_poststate_verifiers.py)
- blocker: Live-прогон не записан: значения счётчиков в живой среде UNPROVEN
- tests: bossman-core/tests/test_v3_evidence_signing.py
- tests: command-center/tests/test_fence_fl01.py
- tests: bossman-core/tests/test_v3_fleet_e2e.py
- tests: bossman-core/tests/test_v3_organization_e2e.py
- tests: command-center/tests/test_action_contract.py
- last_verified_sha: `e26553e56cc3097fd3139a48e3fac8fa3db3ab75` · last_verified_at: 2026-09-05 · live_attestation: NOT_REQUIRED · regression_delta: +0.3

## Security — 8.5 · VERIFIED

- evidence: P0-A gateway loopback fail-closed при proxy-заголовках (cbdabf2, bossman-core/tests/test_gateway_loopback_proxy.py)
- evidence: P0-B монотонная алгебра политики DENY⊗X=DENY, hook-ASK⊗AUTO=ASK (eb0e969, command-center/tests/test_policy_algebra.py)
- evidence: Fleet: PRIVATE/LOCAL_ONLY — жёсткий гейт планировщика, CredentialBroker выдаёт только гранты (test_v3_fleet_core.py)
- evidence: Secret scan в каждом CI-прогоне (tools/ci_secret_scan.py)
- evidence: EH-01: ключ подписи улик 0600 вне модели; fail-closed без ключа (bossman_shared/evidence.py)
- evidence: OpenRouter только через env → ключ в vault, не в репозитории/логах/API (3e673d3, test_feat_openrouter_env_bootstrap.py)
- evidence: SEC-01 секрет-скан 2.0: 13 семейств паттернов, энтропия, ZIP по содержимому, запрещённые файлы (tests/test_ci_secret_scan.py)
- evidence: SEC-03 rate-limit/lockout на /api/login до сравнения токена (command-center/tests/test_login_rate_limit.py)
- evidence: SEC-101/102: pinned HTTP transport, CGNAT запрещён, DNS вне event loop, поток ≤2 МБ (test_astra_remediation.py)
- evidence: SEC-103: рекурсивный fail-closed ZIP-скан; SAST/SCA gate блокирует CI (tools/astra_security_gate.py, ffd7d25)
- evidence: CSRF 403 несёт code=csrf → повторный вход, политика не ослаблена (d114e30, test_browser_navigation_ui.py)
- blocker: Branch protection не включена (ASTRA-CI-101); Windows ACL ключей NOT_RUN
- tests: bossman-core/tests/test_gateway_loopback_proxy.py
- tests: command-center/tests/test_policy_algebra.py
- tests: bossman-core/tests/test_v3_fleet_core.py
- tests: tests/test_ci_secret_scan.py
- tests: command-center/tests/test_login_rate_limit.py
- last_verified_sha: `e26553e56cc3097fd3139a48e3fac8fa3db3ab75` · last_verified_at: 2026-09-05 · live_attestation: NOT_REQUIRED · regression_delta: +0.2

## Tooling / OS Integration — 7.5 · INTEGRATED

- evidence: V2 реестр инструментов/decide_effect/approvals заморожен на ffda281 и покрыт command-center/tests/test_v21_*
- evidence: V3-порты → живой bcc (bossman-core/tests/test_v3_command_center_adapters.py)
- evidence: OpenRouter как провайдер из окружения (ключ+модели — данные), тот же tool loop через фейковый провайдер детерминированно (command-center/tests/test_feat_openrouter_agent_flow.py)
- evidence: CapabilitySpec как узкий адаптер над ToolSpec: манифест /api/capabilities, правило выдачи capability ∧ policy ∧ runtime, неизмеренная предпосылка = отказ, инструмент без верификатора помечен provable=False (command-center/tests/test_capability_manifest.py)
- blocker: TZ-03: CapabilitySpec, выдача инструмента на run, детектор no-progress
- tests: command-center/tests/test_v21_tool_loop.py
- tests: bossman-core/tests/test_v3_command_center_adapters.py
- last_verified_sha: `e26553e56cc3097fd3139a48e3fac8fa3db3ab75` · last_verified_at: 2026-09-05 · live_attestation: NOT_REQUIRED · regression_delta: +0.5

## Organization Layer — 7.3 · INTEGRATED

- evidence: OrganizationRuntime над V3ExecutionBridge/FleetExecutionBridge; ORG-03..07, MEM-02 закрыты (084ad3a)
- evidence: E2E: родитель не COMPLETE при непроверенном ребёнке, рестарт без дублей (bossman-core/tests/test_v3_organization_e2e.py)
- evidence: ORG-01/02: фича `/api/org/*` за флагами, агент организации → агент V2, задача+run V2 на контракт; PlannerPort/DeterministicPlanner; контракт без шагов → BLOCKED/no_executable_steps (efaa55f, test_v3_organization_planner.py, command-center/tests/test_feat_organization.py)
- evidence: O001–O007 закрыты: private→нет cloud даже в fallback, атомарный intake, невалидные ресурсы, veto риск-ревьюера, атомарный бюджет, владение scope (2077bbf, test_astra_remediation.py)
- blocker: ORG-08: saga-компенсации
- blocker: модельный планировщик (TZ-03 §2.5)
- blocker: HTTP-E2E исполнения через V2 с реальным агентом
- blocker: usd как view над fable_budget (TR-04)
- tests: bossman-core/tests/test_v3_organization_core.py
- tests: bossman-core/tests/test_v3_organization_e2e.py
- tests: bossman-core/tests/test_v3_organization_company_bridge.py
- tests: bossman-core/tests/test_v3_organization_planner.py
- tests: command-center/tests/test_feat_organization.py
- last_verified_sha: `e26553e56cc3097fd3139a48e3fac8fa3db3ab75` · last_verified_at: 2026-09-05 · live_attestation: NOT_REQUIRED · regression_delta: +0.3

## Fleet & Resources — 7.0 · INTEGRATED

- evidence: FleetStore/LeaseManager/WorkQueue CAS-claim, fencing, reclaim (bossman-core/tests/test_v3_fleet_core.py, 20 тестов)
- evidence: E2E #1–#4: размещение→исполнение, смерть узла→resume без дублей, приватность, двойной claim (test_v3_fleet_e2e.py)
- evidence: FL-01: task_runs.fence, условные записи и heartbeat, assert_fence до эффекта в V2 и в V3-адаптере (2487694, test_fence_fl01.py)
- evidence: 10 safety proofs (реестр + истёкшая аренда без власти + размещение не штрафует исполнителя); REMOTE_TRANSPORT_PRODUCTION_READY=NO, NODE_AUTH_PRODUCTION_READY=NO (test_v3_fleet_safety_proofs.py)
- evidence: F001–F006: shared/exclusive аренды, guard до мутации, claim_fence на ACK/COMPLETE, backoff/WAIT_HUMAN в SQLite, единый пул RAM+GPU, MINIMIZED-гейт (2077bbf, test_astra_remediation.py, test_v3_fleet_e2e.py)
- blocker: Fake 128GB topology test ≠ аттестация железа
- tests: bossman-core/tests/test_v3_fleet_core.py
- tests: bossman-core/tests/test_v3_fleet_e2e.py
- tests: command-center/tests/test_fence_fl01.py
- tests: bossman-core/tests/test_v3_fleet_safety_proofs.py
- last_verified_sha: `e26553e56cc3097fd3139a48e3fac8fa3db3ab75` · last_verified_at: 2026-09-05 · live_attestation: PENDING · regression_delta: +0.5

## Memory / Context — 6.5 · IMPLEMENTED

- evidence: TaskJournal + FailureMemory + ContextAssembler с редакцией (bossman_v3/memory)
- evidence: ScopedKnowledge: явное наследование include_parents, экспорт по allowlist (MEM-02, 084ad3a)
- evidence: Журнал: exclusive writer lock + CAS, устаревший writer не стирает durable intent (test_reaudit_journal_*); scope ownership по хранилищу (ASTRA-009); лимит V3 включает заголовок/сериализацию (ASTRA-010)
- blocker: TZ-06: решётка скоупов по дереву, ScopeToken, эмбеддинги, токен-оценка контекста
- blocker: слияние с bossman.context_engine (колонка scope)
- tests: bossman-core/tests/test_v3_organization_core.py
- tests: bossman-core/tests/test_v3_memory_kernel.py
- last_verified_sha: `e26553e56cc3097fd3139a48e3fac8fa3db3ab75` · last_verified_at: 2026-09-05 · live_attestation: NOT_REQUIRED · regression_delta: +0.3

## Testing / CI — 8.0 · VERIFIED

- evidence: 4 workflow (root-ci, Bossman Core CI, Command Center CI, V2 Auto-Repair) зелёные по точному SHA 714bb01/fb201a4
- evidence: Полный регресс ядра на стабильном HEAD; benchmark-тесты проверяют SHA
- evidence: README_SCORECARD_CURRENT проверяется в root-ci (scripts/update_readme_scorecard.py --check)
- evidence: Пассивный benchmark overlay: 9 hard fail'ов, 5 стресс-бенчмарков над реальными Organization/Fleet/CompoundRunner, мост в scorecard `--from-benchmark` (bossman-core/tests/test_v3_benchmark_overlay.py, test_v3_org_benchmark.py)
- evidence: Сквозной E2E миссия→организация→флот→узел→V3→файл→свежее чтение→подписанные улики→VERIFIED→ревью→COMPLETE→benchmark→scorecard (bossman-core/tests/test_v3_cross_layer_e2e.py)
- evidence: Реестр 10 доказательств безопасности флота, каждое привязано к существующему тесту (test_v3_fleet_safety_proofs.py)
- evidence: Windows portable job (astra-acceptance) + windows-paths job; skips registry 89/0 без причины (d7b3519); полный регресс на интегрированном дереве: Core 2047, CC 1471, root 152; VERIFY.py 5/5 PASS
- evidence: Exact-SHA CI на 38c836b: root-ci PASS, V2 Auto-Repair PASS, ASTRA acceptance PASS (включая windows-latest portable), Solana safety PASS; Core/CC CI на том же SHA отменены более новым пушем (concurrency), не падением
- evidence: Неснижаемый порог покрытия из измерения: bossman_v3 89% → gate 85% (проверено локально 88.89%), bcc 76% → gate 72% (docs/testing/COVERAGE_BASELINE.md)
- blocker: Core CI и Command Center CI по одному SHA подряд отменяются пушами: полный зелёный набор всех шести workflow по ОДНОМУ SHA ещё не наблюдался
- tests: tests/test_readme_scorecard.py
- tests: .github/workflows/*.yml
- tests: bossman-core/tests/test_v3_benchmark_overlay.py
- tests: bossman-core/tests/test_v3_org_benchmark.py
- tests: bossman-core/tests/test_v3_cross_layer_e2e.py
- tests: bossman-core/tests/test_v3_fleet_safety_proofs.py
- last_verified_sha: `e26553e56cc3097fd3139a48e3fac8fa3db3ab75` · last_verified_at: 2026-09-05 · live_attestation: NOT_REQUIRED · regression_delta: +0.5

## Observability / CEO Control — 7.0 · PARTIAL

- evidence: control_plane снимки организации и флота из durable store (bossman_v3/organization/control_plane.py, fleet/control_plane.py)
- evidence: GET /api/control-plane: organization/queue/treasury/fleet/slo/attention из durable-источников, снимок совпадает после рестарта (5709611, command-center/tests/test_feat_control_plane.py)
- evidence: AST-скан: события и run-лог не несут messages/prompt/api_key/cookie/token (test_no_private_fields_in_events.py)
- evidence: trace_id на весь цикл, ретеншн событий, latency-метрики, Fleet в /api/control-plane (f42bcff, 57f4f31, test_observability_trace.py)
- evidence: Пульт владельца: /api/control-plane → owner_view (КТО/ГДЕ/МОДЕЛЬ/ЧТО/СОСТОЯНИЕ/ПОЧЕМУ/ЦЕНА/ВНИМАНИЕ) и ui/pages/control.js; зелёный COMPLETE только после task.finalized (command-center/tests/test_owner_control_view.py, test_owner_control_ui.py — Playwright, выполнены)
- blocker: SLO по маршрутам не реализован (slo.status = NOT_IMPLEMENTED)
- tests: command-center/tests/test_feat_control_plane.py
- tests: command-center/tests/test_no_private_fields_in_events.py
- tests: bossman-core/tests/test_v3_organization_core.py
- last_verified_sha: `e26553e56cc3097fd3139a48e3fac8fa3db3ab75` · last_verified_at: 2026-09-05 · live_attestation: NOT_REQUIRED · regression_delta: +1.0

## Treasury / Cost — 6.8 · IMPLEMENTED

- evidence: TR-01/02/03: актуальные цены 5 семейств (provisional), токен-оценка по скрипту, потолок in·max(p_in,p_cw)+out·p_out (e724a44, tests/test_fable_budget_pricing.py)
- evidence: ResourceTreasury: INV-3 PartitionViolation, конверты org→dept→mission (test_v3_organization_core.py)
- evidence: PROD-002: неизвестная цена остаётся null до inference, старый нуль ≠ free; O005/O006 атомарный резерв/списание (test_astra_remediation_cc.py)
- blocker: TR-04: usd как view над ledger
- blocker: TR-05: локальный GPU
- blocker: TR-06: burn-rate
- blocker: цены provisional (AS_OF 2026-09-05)
- tests: tests/test_fable_budget_pricing.py
- tests: command-center/tests/test_fable_hard_cap.py
- tests: bossman-core/tests/test_v3_organization_core.py
- last_verified_sha: `e26553e56cc3097fd3139a48e3fac8fa3db3ab75` · last_verified_at: 2026-09-05 · live_attestation: NOT_REQUIRED · regression_delta: +0.3

## Mission UX / Command Center — 6.8 · IMPLEMENTED

- evidence: Command Center UI (command-center/ui) с approvals, задачами, инструментами; command-center/tests зелёные
- evidence: Компактная навигация + OpenRouter Connect (исправлен appendChild) — ветка claude/v2-ui-sidebar-compact, 1401 passed, НЕ влита
- evidence: Данные для страницы владельца доступны: `/api/control-plane`, `/api/org/snapshot` (UI-страница — TZ-10, не сделана)
- evidence: PROD-004 фильтрация миссии до LIMIT + пагинация; CSRF-403 → форма входа вместо мёртвой кнопки (d114e30); журнал тестового периода 51307af16b90: 8 ошибок разобраны
- evidence: Страница «Пульт» в реестре UI; цвет = уровень доказанности, промпты на экран не выводятся
- blocker: Покрытие Command Center 76%: часть UI-фич без тестов
- tests: command-center/tests/test_ux2_pages_sweep.py
- tests: command-center/tests/test_command_bar_ui.py
- last_verified_sha: `e26553e56cc3097fd3139a48e3fac8fa3db3ab75` · last_verified_at: 2026-09-05 · live_attestation: NOT_REQUIRED · regression_delta: +0.6

## Deterministic counters

- false_success_count: 0
- duplicate_side_effect_count: 0
- privacy_violation_count: 0
- permission_bypass_count: 0
- source: bossman-core/tests/test_v3_org_benchmark.py (A–E), test_v3_benchmark_overlay.py, test_v3_fleet_e2e.py, command-center/tests/test_fence_fl01.py at last_evidence_sha; deterministic suites, no live run recorded ; SALVAGE-004: bossman-core/tests/test_astra_remediation.py (ASTRA-004/005, F003/F004, O005), test_v3_astra_p1.py, test_v3_fence_receipts.py at ffd7d25
