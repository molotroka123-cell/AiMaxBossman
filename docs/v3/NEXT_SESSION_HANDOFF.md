# Handoff для следующей сессии (Opus) — компактный контекст

> Обновляется в конце каждой сессии Fable. Цель — начать работу без перечитывания истории.
> Сначала `git fetch origin && git log --oneline -15 origin/claude/bossman-control-v03-43igbk`.

## 1. Где мы

- Ветка: `claude/bossman-control-v03-43igbk`. V2 (`command-center/bcc`) заморожен на `ffda281`; правки V2 — только по доказанному P0/P1 (FL-01 fencing, TR-01 цены).
- V3 живёт в `bossman-core/bossman_v3/`: `memory` (TaskJournal, FailureMemory, ContextAssembler), `execution` (CompoundRunner), `adapters/command_center.py` (V3-порты → живой bcc), `organization` (КТО), `fleet` (ГДЕ), `benchmark_overlay` (пассивный бенчмарк, если влит).
- Разделение слоёв: Organization=КТО · Fleet=ГДЕ · Model Broker (`bcc/v2/model_router`)=КАКАЯ МОДЕЛЬ · V3/V2=КАК и ДОКАЗАНО ЛИ · Memory=ЧТО ПЕРСИСТИТСЯ · Autonomous Ops=КОГДА (НЕ начато) · Benchmark/Scorecard=КАК измеряем.
- Инварианты (не ослаблять): `SIDE_EFFECT_REQUIRED && !VERIFIED → !SUCCESS`; `любой обязательный ребёнок не VERIFIED → родитель не COMPLETE`; `PLACED ≠ DISPATCHED ≠ EXECUTED ≠ VERIFIED`; `DUPLICATE_SIDE_EFFECT_COUNT=0`; текст/событие/размещение/кэш ≠ доказательство; fail-closed.

## 2. Документы, которые читать первыми

| Что | Где |
|---|---|
| Аудит 10×10 и ТЗ (порядок исполнения) | `docs/audit/2026-09-05_AUDITOR_SCORECARD_10x10.md`, `docs/audit/tz/TZ-01..TZ-10` |
| Organization: архитектура/безопасность/журнал работ | `docs/v3/organization/{ARCHITECTURE,SECURITY,WORK_LOG,HANDOFF}.md` |
| Fleet: архитектура/adoption table/безопасность | `docs/v3/fleet/{ARCHITECTURE,SECURITY,HANDOFF}.md` |
| ZIP-артефакты (что удалять после релиза) | `docs/v3/ZIP_ARTIFACTS.md` |
| Заимствованные паттерны и дедуп | `docs/v3/ARCHITECTURE_PATTERNS.md` |
| Live scorecard (если влит) | `README.md` блок `BOSSMAN_LIVE_SCORECARD_*`, `docs/benchmark/current-scorecard.json`, `scripts/update_readme_scorecard.py --check` |

## 3. Как гонять тесты (важно)

- venv: `/tmp/bccvenv/bin/python` (editable-инсталлы указывают на ОСНОВНОЙ checkout). В git-worktree ОБЯЗАТЕЛЬНО:
  `cd bossman-core && PYTHONPATH=$PWD:$PWD/../command-center:$PWD/.. /tmp/bccvenv/bin/python -m pytest -q -p no:cacheprovider tests/test_v3_*.py`
- Целевые наборы: `tests/test_v3_organization_*.py`, `tests/test_v3_fleet_*.py` (E2E #1–#4), `test_v3_command_center_adapters.py` (живой bcc), `test_company_mode.py`.
- Полный регресс ядра — один раз перед пушем: `BOSSMAN_RUN_REAL_SANDBOX=0 ... pytest tests/ --timeout=300` (~4 мин; не коммитить во время прогона — benchmark-тесты проверяют SHA).
- Secret scan: `python tools/ci_secret_scan.py` из корня (канарейки — только с маркером `ci-secret-scan: allow`). Сканер видит только tracked-файлы: запускать ПОСЛЕ `git add`.
- Коммиты: trailer'ы `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` (или актуальная модель) и `Claude-Session: <url>`; ID находок (TR-01, FL-01, EH-01…) в сообщении; один коммит = одно ТЗ.
- CI: смотреть Actions по точному SHA (root-ci, Bossman Core CI, Command Center CI, Bossman V2 Auto-Repair); пуш поверх запущенного прогона отменяет его (cancel-in-progress) — это не красный.

## 4. Состояние по ТЗ (порядок исполнения владельца: TZ-09 → TZ-05 → TZ-01 → TZ-04 → TZ-08 → TZ-02/07 → TZ-06 → TZ-03 → TZ-10)

| ТЗ | Статус | Коммит(ы) | Что осталось |
|---|---|---|---|
| TZ-09 казначейство | §2 ЗАКРЫТО (TR-01/02/03), INV-3 в ResourceTreasury | `e724a44`, `084ad3a` | TR-04 usd как view над `fable_budget` ledger, TR-05 GPU-секунды, TR-06 burn-rate (частично: `/api/control-plane` считает burn по `task_runs.cost_usd`) |
| TZ-05 fencing/флот | §2 ЗАКРЫТО (FL-01: `task_runs.fence`, условные записи/heartbeat, `assert_fence` до эффекта, replay-guard) | `2487694` | §3 реестр/планировщик в V2-пути (`BCC_FLEET_ENABLED` нет; в V3 `bossman_v3/fleet` есть), UNIQUE idem-индекс, ZIP удалить, §4 GPU |
| TZ-01 подпись улик/верификаторы | §2.1 ЗАКРЫТО (EH-01 HMAC, `Evidence.signed`, журнал подписывает шаг); §2.5 ЗАКРЫТО (EH-05 requeue) | `000f331`, `4c8fec2` | §2.2 11 верификаторов пост-состояния + `ActionReceipt`, §2.3 `finalize()` + grep-тест, §2.4 абстенция |
| TZ-04 организация | §2 ЗАКРЫТО (ORG-01 feature `/api/org/*`, ORG-02 PlannerPort/BLOCKED no_executable_steps); ORG-03..07 ранее | `084ad3a`, `efaa55f` | ORG-08 saga; модельный планировщик (TZ-03 §2.5); HTTP-E2E исполнения через V2 с реальным агентом; §4.3 usd-view (TR-04) |
| TZ-08 наблюдаемость | §2.5 ЗАКРЫТО (`GET /api/control-plane`), §2.7 AST-тест приватности | `5709611` | §2.1 ретеншн, §2.2 гистограммы/SLO/burn-rate алерт, §2.3 span'ы, §2.4 цепочка INV-5, §2.6 dead-click, §2.7 тест приватности |
| TZ-02 / TZ-07 | SEC-01 скан 2.0 и SEC-03 rate-limit ЗАКРЫТЫ; P0-A/P0-B закрыты (`cbdabf2`, `eb0e969`) | `4568e2c`, `97b3091` | SEC-02 TTL/idle/ротация sid, SEC-04 сканы-гейты, SEC-06 hash-chain, coverage gate, windows job, реестр skips |
| TZ-06 память | MEM-02 явное наследование (`084ad3a`); остальное OPEN | | решётка по дереву (INV-4), ScopeToken, эмбеддинги, токен-оценка |
| TZ-03 инструменты | OPEN | | CapabilitySpec, выдача инструмента на run, no-progress |
| TZ-10 UX | OPEN (дизайн-ветка `claude/v2-ui-sidebar-compact` не влита) | | статусы blocked/capability_unavailable, aria, страница control-plane (данные уже есть в `/api/control-plane`) |
| Live Scorecard | ЗАКРЫТО (README блок, `docs/benchmark/current-scorecard.json`, `scripts/update_readme_scorecard.py --check` в root-ci) | `dd658b7` | обновлять `last_evidence_sha` при материальных изменениях; `exact_sha_ci` ставить PASS только по Actions на этом SHA |
| Benchmark overlay | ВЛИТ (`bossman_v3/benchmark_overlay`, 5 стресс-бенчмарков, `--from-benchmark`) | `663a720` | регулярный прогон, пишущий `benchmark-report.json`; live-режим |
| OpenRouter provider path | ЗАКРЫТО (`BOSSMAN_OPENROUTER_API_KEY` + `BOSSMAN_OPENROUTER_MODELS` → провайдер и модели из окружения; фейковый провайдер гоняет тот же tool loop; live smoke opt-in) | `3e673d3`, `97b3091` | ротация ключа, выбор модели по capability_probe |
| Fleet safety proofs 1–10 / cross-layer E2E | ЗАКРЫТО | `869a124`, `4568e2c` | live-режим с реальным узлом |
| Delta-аудит CLOSURE-002 | `docs/audit/2026-09-05_DELTA_AUDIT_CLOSURE_002.md` | `4568e2c` | обновлять при закрытии находок |
| Autonomous Operations | НЕ НАЧАТО (отдельная миссия по решению владельца) | | |

## 5. Известные P0/P1 вне V3 (для владельца)

- ЗАКРЫТО P0-A `gateway/auth.py` (`cbdabf2`): loopback-проход только для прямого 127.0.0.1 без proxy-заголовков; `loopback_allowed_aliases`.
- ЗАКРЫТО P0-B `bcc/tools.py` (`eb0e969`): пол политики DENY⊗X=DENY, hook-ASK⊗AUTO=ASK (`ToolSpec.hook_is_floor`, опт-аут только у OpenCode).
- P1 bcc `approvals.consume` по `(kind, preview)` без срока/скоупа; V3-адаптер привязывает preview к `task#<id>`. Сам V2 не менялся.
- P1 `bossman.company.runtime` может закрыть задачу DONE по самоотчёту без улик; путь Organization этого не допускает (и теперь требует подписанных улик).

## 6. Итог последней сессии (Fable, 2026-09-05, миссия CLOSURE-002)

- **Коммиты (по порядку):** `e724a44` TR-01/02/03 · `cbdabf2` P0-A · `eb0e969` P0-B · `dd658b7` Live Scorecard · `2487694` FL-01 · `000f331` EH-01 · `efaa55f` ORG-01/02 · `5709611` control-plane+privacy · `3e673d3` OpenRouter env · `4c8fec2` EH-05 · `2684600` docs · `663a720` Benchmark Overlay · `869a124` CI-fix + safety proofs · `4568e2c` cross-layer E2E + SEC-01 + delta-аудит · `97b3091` OpenRouter models + SEC-03 · затем этот docs-коммит (FINAL_SHA = HEAD ветки).
- **Тесты:** command-center полный регресс — см. `docs/v3/WORK_LOG_PRODUCTIZATION.md` (последняя строка docs); bossman-core все `test_v3_*` + gateway + budget зелёные; root `tests/` зелёные; целевые наборы по каждому пункту — в work log.
- **CI по точному SHA:** `2684600` Command Center был красным (организация импортировала ядро до 503; legacy-гейт без requeue) — исправлено в `869a124`. Для `869a124..97b3091` и docs-коммита смотреть Actions; `exact_sha_ci` в scorecard стоит NOT_RUN до наблюдения.
- **Не сделано (в порядке ценности):** TZ-01 §2.2–2.3 (верификаторы пост-состояния + `finalize()`), TZ-08 §2.1–2.4/2.6, SEC-02/04/06, TZ-07 (coverage, windows-job, skips), TZ-06, TZ-03 (CapabilitySpec, no-progress), TZ-10 (страница control-plane; дизайн-ветка не влита), ORG-08 saga, TR-04..06, регулярный benchmark-прогон.
- **Не начато по решению владельца:** Autonomous Operations.
- **Осторожно:** `bossman-core/tests/conftest.py` держит ключ подписи улик в tmp; `Evidence(verified=True)` без `Evidence.signed(...)` отвергается везде; `CommandCenterRuntime.call` из собственного цикла бросает RuntimeError; фича organization отвечает 503 ДО импорта ядра (Command Center CI без bossman-core); гейт с FAIL обязан нести `requeue`; секрет-скан теперь считает энтропию в коде/конфиге — фальшивые токены в тестах помечать `# ci-secret-scan: allow`.
- **Следующий шаг для Opus:** TZ-01 §2.2 — `ActionReceipt` как расширение `tool_calls` (`receipt_json, verified, verifier, observed_at, sig`) + верификаторы terminal/files/apps/github с подписью `bossman_v3.verifier`; затем §2.3 `finalize()` и grep-тест `test_no_direct_completed_writes`.


## 7. Итог сессии SALVAGE-004 (Fable, 2026-09-05)

- Пакет `AiMaxBossman_ASTRA_Fixes_35390ec(1).zip` объединён с HEAD адресно (2077bbf): 59 файлов
  совпали с хешами манифеста и взяты из payload, 14 новых, 4 слиты трёхсторонне, 4 разрешены
  вручную в пользу payload (compound/journal/bridges/contracts). Интерим-реализации ASTRA-002/003/004
  из c36dd5b заменены механизмами пакета (bind_plan, execution_binding, durable intent).
- Дополнительно: V2-HOOK-CANCEL-01 (0fa1317), observe_pid с перекрёстной проверкой источников
  (2077bbf), CSRF-403 → повторный вход (d114e30), UTF-8 в Windows-тестах и pin pip/setuptools для
  SCA (ffd7d25). Статусы 35 находок и трассы: `docs/security/ASTRA_SALVAGE_DELTA_HEAD.md`.
- Открыто для владельца: branch protection (`tools/astra_branch_protection.py --apply`), exact-SHA CI
  по FINAL_SHA, Windows ACL ключей, sandbox/железо, live OpenRouter (лимит 3.00 USD, UNKNOWN_PRICE →
  блок). Остатки TRUTH-003: coverage gate (§16), CapabilitySpec (§17), страница владельца (§20).
- Как гонять: Core — `cd bossman-core && pytest tests` (медленные benchmark-gate тесты, ~5 мин);
  CC — `cd command-center && pytest tests` (~8 мин, Chromium нужен, иначе skip ≠ pass);
  root — `pytest tests`; пакетная проверка — `VERIFY.py --repo .` из распакованного архива.
