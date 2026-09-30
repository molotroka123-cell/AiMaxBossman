# TZ-04 — Организационный слой Bossman (5 → 10)

Находки: ORG-01..ORG-08. Инварианты: INV-1, INV-3, INV-4, INV-5, INV-6.
Пакет: `bossman-core/bossman_v3/organization/` (2 307 строк, SQLite WAL, 4 тест-файла).

## 1. Текущее состояние — что уже правильно
- Delegation Contract 2.0 с `validate()` только по уликам; ревью — только вето; организация не расширяет прав (`contracts.py`, `runtime.py`, `SECURITY.md`).
- Топологический порядок зависимостей с детекцией цикла (`runtime.py:444-467`).
- Динамические команды по риску (`teams.py:61-63`), маркетплейс по лестнице tier (`marketplace.py`).
- Durable store, `resume()` без повторной делегации COMPLETED.

## 2. Что не даёт продукту работать (ORG-01, ORG-02) — MUST

### 2.1 Включение и HTTP
1. Feature `command-center/bcc/features/organization.py` (авто-регистрация через `Feature`): при `BOSSMAN_V3_ENABLED=1 ∧ BOSSMAN_V3_ORGANIZATION=1` создаёт `OrganizationRuntime(store=data_dir/organization.sqlite, execution=V3ExecutionBridge(...), human_review=ApprovalsPort(svc.approvals), reporter=MissionsPort(svc))`.
2. Маршруты: `GET /api/org/snapshot` (= `control_plane.snapshot().to_dict()`), `GET/POST /api/org/departments`, `/api/org/agents`, `POST /api/org/missions` (goal → контракты), `POST /api/org/missions/{id}/run`, `POST /api/org/resume`, `GET /api/org/learning`.
3. `agent_factory` MUST маппить `AgentProfile.agent_id → agents.id` V2 (сейчас один `build_agent`), иначе tier-лестница фиктивна.
4. `cost_meter` MUST брать факт из `spend_meter`/`fable_budget` (TZ-09), не из оценки контракта.

### 2.2 Планировщик шагов
Контракт без `steps` MUST проходить через `PlannerPort`: (а) детерминированные планы из `CapabilitySpec` (TZ-03) для односложных целей («открой X», «создай файл Y»); (б) для составных — локальная модель через обёртку TZ-03 §2.5 с валидацией шагов по реестру способностей; (в) план без валидных шагов → `BLOCKED/no_executable_steps`, а не `FAILED` (это не провал исполнителя).

## 3. Математика: исправить обучение и маршрутизацию (ORG-04, ORG-05, ORG-06) — MUST

### 3.1 Затухание и эффективная выборка
Сейчас все счётчики умножаются на `λ=0.9` при каждом наблюдении:
`attempts_n = Σ_{k=0}^{n−1} λ^k = (1−λⁿ)/(1−λ) → 10`. Следствия:
- `failing_agents(min_attempts=2.0)` после двух наблюдений видит 1.9 → не срабатывает; нужно ≥ 3.
- `reliability_max = (1+10)/(2+10) = 0.917` — агент с сотней подтверждённых успехов выглядит как «92 %».

Требования:
1. Хранить `n_eff = (1−λⁿ)/(1−λ)` и число сырых наблюдений `n_raw` отдельно; пороги (`min_attempts`) сравнивать с `n_raw`.
2. Апостериор Beta с затухающими счётчиками: `α = 1 + Σ λ^{age} · success`, `β = 1 + Σ λ^{age} · failure`; `reliability = α/(α+β)` (то же, что сейчас, но `attempts` не смешивать с `n_raw`).
3. Выбор `λ` из периода полураспада: `λ = 2^{−1/T½}`; для `T½ = 20` наблюдений `λ ≈ 0.966`. Значение 0.9 (T½ ≈ 6.6) слишком короткое для редких способностей — сделать параметром отдела с дефолтом 0.966.

### 3.2 Исследование в маркетплейсе
Сортировка по точечной `reliability` внутри tier = чистая эксплуатация → новый агент (0.5) никогда не выбирается, пока старый (0.9) не провалится (starvation). Требование: внутри одного tier ранжировать по **UCB1-Bayes** или Thompson:
- UCB: `score = μ + c·σ`, где `μ = α/(α+β)`, `σ² = αβ/((α+β)²(α+β+1))`, `c = 1.0` для LOW-риска, `c = 0` для HIGH (на HIGH — только эксплуатация плюс независимый ревьюер).
- Детерминизм для тестов: seed из `contract.digest()`.
- Штраф за ложный успех остаётся лексикографически выше (это правильно).

### 3.3 Бюджетная проверка кандидата
Заменить `cost_per_call_usd > c.budget.usd` на `E[calls] · cost_per_call ≤ remaining(budget)`, где `E[calls] = |steps| · (1 + retry_rate(agent, cap))`; `retry_rate` из `OutcomeStats.retries/n_raw`.

## 4. SLA и конверты (ORG-03, ORG-07) — MUST
1. `deadline`: `runtime._attempt` MUST проверять `now > deadline → BLOCKED/deadline_missed` и не делегировать; `teams.form` получает `wall_seconds = deadline − now` в `Resources`.
2. Конверты: `set_limit(child)` MUST отклонять `limit(child) > limit(parent)` и `Σ_{siblings} limit > limit(parent)` (INV-3). `restore()` MUST восстанавливать резервы из store (`org_treasury` расширить колонкой `reserved_json`), а не терять их.
3. Казначейство организации MUST быть view поверх `fable_budget` для `usd` (одна истина, TZ-09), а собственные конверты — только для `tokens/compute_seconds/wall/concurrency`.

## 5. Каскадный провал и компенсации (ORG-08) — SHOULD
Для составной миссии ввести saga-семантику: у контракта опциональное `compensation: PlanStep[]` (например, «удалить созданный файл», «revert коммита»). При `FAILED` обязательного контракта после успешных зависимых — исполнить компенсации только тех, у кого `reversible=True`; необратимые фиксируются в отчёте как «оставлено». Родитель — `FAILED/partial` с перечнем сохранённой работы (уже требуется INV «preserve completed work»).

## 6. Приёмка
1. `test_org_feature_off_by_default` / `test_org_routes_when_enabled`.
2. `test_snapshot_http_matches_store` — `GET /api/org/snapshot` == `snapshot().to_dict()`.
3. `test_failing_agents_after_two_raw_attempts` — 2 провала подряд → агент в `failing_agents` (сейчас падает).
4. `test_reliability_reaches_one_asymptotically` — 100 успехов → `reliability ≥ 0.98`.
5. `test_ucb_explores_new_agent` — два агента одного tier, у первого 9/10, у второго 0/0: за 20 контрактов второй выбран ≥ 3 раз (seed фиксирован).
6. `test_budget_check_uses_expected_calls` — контракт из 5 шагов и `cost_per_call=0.3`, бюджет 1.0 → отклонён.
7. `test_deadline_blocks` — истёкший deadline → `BLOCKED/deadline_missed`, делегации нет.
8. `test_envelope_partition_invariant` — лимит миссии > лимита отдела → `ValueError`.
9. `test_restore_keeps_reservations`.
10. `test_saga_compensation_on_partial_failure` — файл создан, коммит провалился → файл удалён (reversible), состояние `FAILED/partial`.
11. E2E через `bcc` (существующий `test_v3_organization_command_center.py`) с включённым флагом — без skip в Command Center CI (в job уже ставится `asyncpg redis`).

## 7. Чек-лист 10/10
- [ ] Feature+HTTP, `agent_factory` по агенту, `cost_meter` из ledger
- [ ] PlannerPort, `no_executable_steps` = BLOCKED
- [ ] `n_raw`/`n_eff`, `λ` по T½, UCB внутри tier
- [ ] `E[calls]` в проверке бюджета
- [ ] deadline, INV-3, резервы после рестарта
- [ ] saga-компенсации
