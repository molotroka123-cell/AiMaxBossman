# TZ-07 — Тесты и CI (7 → 10)

Находки: CI-01..CI-05, TL-01, SEC-04. Инварианты: все (CI — единственное место, где они проверяются на каждом коммите).

## 1. Текущее состояние (HEAD e8f348d)

| Каталог | Файлов | Тестов |
|---|---|---|
| command-center/tests | 126 | 1 154 |
| bossman-core/tests | 178 | 1 515 |
| tests (root) | 13 | 76 |
| apps/*/tests | 74 | 881 |
| **Итого** | **391** | **3 626** |

- 46 `skip/skipif`; pytest-timeout везде; matrix py3.11/3.12; concurrency-группы; `pip-audit`/`bandit` — `continue-on-error`.
- Нет: покрытия, hypothesis, mutation, Windows-job, реестра skip, контрактных тестов UI↔API.
- Stress/restart: `test_stage9_resource_stress.py`, `test_cost_governor_concurrency.py`, `test_cost_restart_ttl.py`, `test_ux2_restart_durability.py`, `test_v3_compound_resume.py` — есть, но нет мульти-воркерного chaos.

## 2. Требования

### 2.1 Покрытие как гейт (CI-01) — MUST
1. `pytest-cov` в dev-зависимостях; `--cov=bcc --cov=bossman --cov=bossman_v3 --cov-report=xml --cov-fail-under=80` в каждом pytest-job.
2. Ветвевое покрытие для модулей-инвариантов (`engine.py`, `action_contract.py`, `verification.py`, `fable_budget.py`, `contracts.py`, `treasury.py`, `marketplace.py`): порог 90 % (`--cov-branch`, per-file через `.coveragerc [report] fail_under` + скрипт проверки).
3. Diff-coverage на PR (`diff-cover`) ≥ 90 % новых строк.

### 2.2 Property-based и метаморфические (CI-03) — MUST
`hypothesis` в dev-зависимостях. Обязательные свойства:
- Классификатор (TZ-01): ∀ строка из лексикона-генератора — класс ≠ INFORMATIONAL; инвариантность к регистру, ё/е, лишним пробелам.
- Ledger (`fable_budget`): ∀ последовательность `reserve/commit/release/reconcile` — `remaining ≥ 0`, `committed + holds ≤ total`, идемпотентность повторного `commit`.
- Treasury INV-3: ∀ дерево конвертов — `Σ children ≤ parent` после любой последовательности операций.
- Scheduler (TZ-05): ∀ набор узлов и run — `privacy=local_only` никогда на `cloud`; `score` детерминирован.
- Scope lattice (TZ-06): ∀ пара несравнимых скоупов — пересечение `own` пусто.
- Топосорт `_topological`: ∀ DAG — порядок уважает зависимости; ∀ граф с циклом — `ValueError`.

### 2.3 Mutation-тесты (CI-03) — SHOULD
`mutmut` по модулям-инвариантам раз в неделю (schedule job), отчёт-артефакт; порог убитых мутантов ≥ 70 % как предупреждение, не гейт.

### 2.4 Chaos/мульти-воркер — MUST (связано с TZ-05)
1. Два `Engine` на одной SQLite в одном процессе (asyncio) — гонка `claim`, потеря аренды, `FencedOut`.
2. «Убить воркер посреди шага» (`asyncio.CancelledError` в середине `execute`) → `recover()` → продолжение с журнала → `duplicate_side_effect_count = 0`.
3. Сдвиг часов: `utcnow` monkeypatch на ±5 мин — аренды/дедлайны не ломают инварианты.
4. DAG из 50 контрактов со случайными зависимостями (seed) — топосорт, каскад провала, компенсации.
5. Бюджет исчерпывается в середине run — run завершается `blocked/budget`, не `completed`.

### 2.5 Windows job (TL-01) — MUST
См. TZ-03 §2.1. Маркеры `windows/linux_only/live/browser`.

### 2.6 Реестр skip (CI-04) — MUST
`tests/SKIPS.md` генерируется скриптом `tools/skips_registry.py` из маркеров: причина, владелец, `expires` (дата). CI падает, если `expires` в прошлом. Цель — 46 → ≤ 15 skip к концу квартала.

### 2.7 Контрактные тесты UI↔API — SHOULD
JSON-схемы ответов (`schemas/api/*.json`, каталог `schemas/` уже есть) для `/api/tasks`, `/api/browser/sessions`, `/api/org/snapshot`, `/api/control-plane`; pytest проверяет ответы, а Playwright-тест страниц проверяет, что UI не обращается к полям вне схемы (перехват `fetch` в `test_ux2_pages_sweep`).

### 2.8 Гигиена репозитория — MUST
В корне 23 ZIP-архива и `IMG_3955.png`. ZIP — не исходники: либо влить (Fleet OS, TZ-05), либо перенести в релизы/`artifacts/` и добавить в скан секретов (TZ-02 §2.1). CI-проверка: `*.zip` в корне запрещены.

### 2.9 Сканы как гейт — MUST (= TZ-02 §2.3)

## 3. Математика
- Порог покрытия 80/90: при равномерной плотности дефектов ρ на строку вероятность пропустить дефект в непокрытых 10 % кода ≈ 0.1·ρ·N; для модулей-инвариантов (N≈3 000 строк) требование 90 % ветвей оставляет ≤ 300 непроверенных строк — обозримо для ревью.
- Hypothesis: 200 примеров на свойство при 6 свойствах × 5 модулей ≈ 6 000 случаев за ~1 мин — дешевле одного полного прогона (8 мин).
- Mutation score 70 % — эмпирический порог, ниже которого тесты «проверяют, что код запускается», а не «что он прав».

## 4. Приёмка
1. Все job'ы зелёные с `--cov-fail-under`.
2. ≥ 6 hypothesis-свойств, ≥ 5 chaos-тестов.
3. Windows job в matrix.
4. `tests/SKIPS.md` актуален; skip с истёкшим сроком роняет CI.
5. Корень без ZIP; секрет-скан читает архивы в `artifacts/`.
6. Контрактные схемы для ≥ 4 маршрутов.

## 5. Чек-лист 10/10
- [ ] cov 80 % общий / 90 % ветвей на инвариантах / diff-cover
- [ ] hypothesis + mutmut
- [ ] chaos мульти-воркер, kill-mid-step, clock skew, DAG-50, budget-mid-run
- [ ] Windows job
- [ ] реестр skip с expiry
- [ ] контрактные схемы UI↔API
- [ ] корень без ZIP
