# Bossman — единая AI-рабочая среда

Локальные и облачные модели, миссии, браузер, терминал и творческие приложения
в одной панели. Политика определяет, что разрешено; верификатор проверяет,
что действительно получилось. Текст модели не заменяет результат.

> **Интеграционный кандидат, не готовый релиз.**
> Общая ветка: `integration/bossman-unified-20260906`.
> Она объединяет основную сборку `b85ab17` и опубликованные исправления Fable
> `dc0d149` через merge `2c88d24`. Полная приёмка текущего SHA ещё не выполнена.
> Рабочие ветки разработчиков сохранены; автоматического внедрения новых
> коммитов, включения автономии или фоновых платных вызовов нет.

[Состав сборки и ограничения](docs/integration/UNIFIED_CANDIDATE.md) ·
[Дневная приёмка](docs/integration/DAYTIME_ACCEPTANCE.md) ·
[Инструкция Fable](handoffs/FABLE_START_HERE.md)

## Одна система, общие механизмы

| Область | Что включено в кандидата | Каноническое место |
|---|---|---|
| Миссии и контроль | Очередь, агенты, approvals, ресурсы, история и проверка результата | `command-center/bcc/` |
| Видео | Один основной **Video Studio**, сохранены продвинутый монтаж, история и native FFmpeg-путь | `#/video-studio`, `bcc/video_studio/` |
| Веб-дизайн | Общая панель, preview sandbox и исправления сохранения/DOM | `#/web_designer`, `bcc/features/web_designer.py` |
| Continuity | Mission IR, защищённый журнал, visual/reaction guards, preflight и кандидаты навыков | `bossman_shared/mission_ir.py`, `bossman-core/bossman_v3/` |
| Steward | Чистые контракты целей, наблюдений и ограниченных предложений; не фоновый исполнитель | `bossman_shared/objective_spec.py` |
| Защита качества моделей | Оценщик Intelligence Preservation; реальные парные замеры требуются отдельно | `tools/intelligence_preservation_gate.py` |

Наличие кода и отсутствие merge-конфликтов не доказывают совместимость.
Редакторы используют существующие очередь, разрешения, бюджеты и верификацию:
вторая система исполнения или второй видеоредактор в этой сборке не создаются.

## Эпохи

| Этап | Смысл | Статус |
|---|---|---|
| 1–3 | Агенты → оркестрация → проверяемое исполнение и восстановление | Существующий фундамент; общая регрессия текущей сборки обязательна |
| 4 — **Bossman Continuity** | Сохранить цель и проверенное состояние между приложениями, моделями и сбоями | Интегрированный фундамент, **не завершена** |
| 5 — **Bossman Steward** | Поддерживать заданные владельцем условия через ограниченные миссии | Контракты начаты; runtime activation и длительная приёмка **не завершены** |

Планы: [Epoch 4](docs/v4/EPOCH_4_PLAN.md), [Epoch 5](docs/v5/EPOCH_5_PLAN.md).
Ограничения предыдущего интеграционного чекпоинта:
[отчёт Astra](docs/v4/INTEGRATION_2026-09-06.md).
Цели ×3 и сохранение качества моделей остаются целями измерения, не достижениями.

## Запуск для разработки

Python 3.11/3.12. Из корня репозитория, в отдельном виртуальном окружении:

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e . -e "./bossman-core[dev]" -e "./command-center[dev]"
cd command-center
bcc
```

Панель по умолчанию открывается на `http://127.0.0.1:8800`;
фактический адрес и входной токен берите из локального запуска.
Не публикуйте токен, не выставляйте панель наружу без защиты.
Для медиа нужны системные `ffmpeg` и `ffprobe`; для браузерных тестов —
Playwright/Chromium. PostgreSQL и внешние службы настраиваются отдельно.
Отключённые возможности не включаются ради демонстрации «всё работает».

## Приёмка и безопасная совместная работа

Быстрая проверка общего desktop-контракта:

```bash
node --test command-center/tests/js/desktop.test.mjs command-center/tests/js/unified_shell.test.mjs
```

Workflow `Unified candidate smoke` проверяет точный SHA и узкие точки интеграции.
Это **не полный регресс**, не Windows/AI Max acceptance и не тест интеллекта LLM.
Полные Core/Command Center/root, браузер, реальные media-сценарии, crash/resume,
Windows и парные model-evals выполняются по [плану приёмки](docs/integration/DAYTIME_ACCEPTANCE.md).
Отсутствие evidence, skipped и cancelled не считаются PASS.

Fable продолжает в собственной ветке. Новые изменения поступают через PR,
без force-push и массового слияния старых экспериментальных веток.
Политика, approvals, бюджеты, сохранённые данные и feature flags не ослабляются.

## Документация и исторические оценки

Подробное прежнее описание, конфигурация, флаги и справочные ссылки сохранены
**без изменений** в [историческом README](README_HISTORY_20260906.md).
Устаревшие сведения внутри него не являются статусом текущего кандидата.

Ниже сохранена существующая автоматически генерируемая проекция scorecard.
**Она относится к своему `Last evidence SHA`, а не к этой сборке.**
Баллы не повышались; строка FRESH внутри старого снимка не переносится на новый HEAD.
Источник — [scorecard JSON](docs/benchmark/current-scorecard.json);
обновление и проверка: `python scripts/update_readme_scorecard.py --check`.

<details>
<summary>Историческая проекция scorecard — не сертификат текущего SHA</summary>

<!-- BOSSMAN_LIVE_SCORECARD_START -->
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
<!-- BOSSMAN_LIVE_SCORECARD_END -->

</details>
