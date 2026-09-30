# TZ-01 — Честность исполнения: от «доверия по строке» к подписанным уликам (8 → 10)

Находки: EH-01..EH-05 (`docs/audit/2026-09-05_AUDITOR_SCORECARD_10x10.md`). Инварианты: INV-1, INV-6.
Слова MUST/SHOULD/MAY — по RFC 2119.

## 1. Текущее состояние (по коду)

| Компонент | Файл | Что делает | Пробел |
|---|---|---|---|
| Гейт отказа | `command-center/bcc/features/action_gate.py` | Текстовый отказ + 0 строк `tool_calls` → FAIL | Только эвристика на тексте |
| Контракт способностей | `command-center/bcc/features/action_contract.py:83-160, 292-330` | Regex-классификация 12 семейств; доказательство = строка `tool_calls` семейства (+`call_filter`) | «Инструмент вызван» ≠ «мир изменился» |
| Роутер действий | `command-center/bcc/features/action_router.py:83-125` | browser-намерение → прикрепляет `meta.review.evidence(kind=browser,url_contains)` | Только browser |
| Верификация | `command-center/bcc/v2/verification.py:99-246` | Свежее наблюдение для `file/db/browser/app` | 7 семейств без наблюдения |
| Доверие уликам | `bossman-core/bossman_v3/organization/contracts.py:23-26,188-189` | `source.startswith(("journal:","bcc.v2.verification","bossman_v3.verifier"))` | Префикс строки подделывается |
| Пути `completed` | `engine.py:750`, `missions.py:165`, `review_gate.py:182`, `images.py:575`, `benchlab.py:78`, `bossman/runner.py:448`, `computer_operator/manager.py:103` | 7 независимых записей статуса | Нет единой точки `finalize()` с INV-1 |

## 2. Требования

### 2.1 Подписанные улики (EH-01) — MUST
1. Ввести `bossman_shared/evidence.py`: `sign(evidence: dict, *, key: bytes) -> str` и `verify(evidence, sig, key) -> bool` на HMAC-SHA256 над каноническим JSON (`sort_keys=True, separators=(",",":")`). Ключ процесса — `data_dir/keys/evidence.key` (32 байта из `secrets.token_bytes`, права 0600), создаётся при старте; в тестах — `tmp_path`.
2. `Evidence` получает поля `sig: str`, `signer: str` (имя верификатора), `nonce: str` (uuid4), `issued_at`.
3. `contracts._trusted(e)` MUST стать `verify(e.without_sig(), e.sig, key) and e.signer in TRUSTED_SIGNERS`. Строка `source` остаётся информационной.
4. Любой `Evidence(verified=True)` без валидной подписи → ошибка контракта `"unsigned verified evidence"` (fail-closed, INV-6).
5. Подписывать могут ТОЛЬКО: `bcc.v2.verification.verify_all`, `TaskJournal.record(receipt=…)` при закрытии шага с подтверждением, `bossman_v3.verifier`. Модельные адаптеры и `WorkResult.from_dict` подпись не создают.

Обоснование: доверие по префиксу — это «security by naming». Подпись даёт свойство: *улику может создать только код, у которого есть ключ*, и ключ не проходит через модель.

### 2.2 Универсальный `ActionReceipt` и верификаторы пост-состояния (EH-02) — MUST
Единый тип (без новой таблицы — расширение `tool_calls` колонками `receipt_json`, `verified`, `verifier`, `observed_at`, `sig`):

```
ActionReceipt = {
  task_id, run_id, step, capability, executor, action_type, target,
  started_at, finished_at, observed_at,        # observed_at > finished_at (свежесть)
  attempted: bool, succeeded: bool, verified: bool,
  verification_type: "post_state"|"receipt_readback"|"tool_result_only",
  observation: {...},                           # что именно увидели
  error: str|None, sig, signer, nonce
}
```

Таблица верификаторов (все — детерминированные, без LLM):

| Способность | Наблюдение пост-состояния (MUST) | Источник истины |
|---|---|---|
| terminal | exit code из `terminal_sessions` + для мутирующих команд `git status --porcelain`/`stat` целевого пути до/после | ФС/процесс |
| files | `stat`+`sha256` пути после операции; для удаления — отсутствие | ФС |
| apps | PID жив (`psutil.pid_exists`) + окно/порт (`apps_control._wait_ready`) | ОС |
| browser | уже есть (`_observe_browser`) | Playwright |
| github | `git ls-remote <remote> <ref>` = ожидаемый SHA; для PR — номер из ответа API | удалённый git |
| memory | read-back: `SQLiteMemoryBackend.search(fact_id)` вернул запись с тем же хешем | индекс памяти |
| schedules | строка `schedules` с `enabled=1` и `next_run_at` | БД |
| images | файл ассета существует, `sha256` совпадает с job-записью | ФС |
| mcp | ответ MCP с `id` запроса и без `error`; для мутаций — повторный `read`-вызов если сервер его объявляет | MCP |
| openclaw | сохранённый receipt дедупа (уже есть) | store |
| code | `git diff --stat` ≠ пусто И целевой тест/компиляция выполнены (`tool_calls` c `pytest`/`compileall`) | git+процесс |

Правило: `verification_type="tool_result_only"` MUST давать `verified=False`. Такое действие может закрыть шаг только как `attempted`, не как `succeeded`.

### 2.3 Единая точка финализации (EH-04) — MUST
`bcc/lifecycle.py::finalize(task, run, *, verdicts, receipts)`:
```
required = Required(task)                       # из action_contract.classify_all + meta.review.evidence
ok = all(any(r.capability==c and r.verified and verify_sig(r) and r.observed_at > r.started_at
             for r in receipts) for c in required)
status = "completed" if ok and no FAIL verdict else terminal_state(verdicts, receipts)
```
Все 7 мест записи `completed` MUST вызывать `finalize()`; прямой `UPDATE tasks SET status='completed'` вне него запрещён тестом-грепом (`tests/test_no_direct_completed_writes.py`).

`terminal_state`: `blocked` (политика/аппрув), `capability_unavailable` (нет исполнителя), `failed` (исполнитель был, улик нет). Эти два новых значения MUST быть добавлены в `tasks.status` CHECK-ограничение и в словарь UI (TZ-10). Это единственное допустимое расширение словаря состояний.

### 2.4 Классификация с абстенцией (EH-03) — SHOULD
1. Сохранить regex-лексикон как детерминированный первый слой.
2. Добавить второй слой — локальная малая модель (через существующий `pick_model`/`local_first`) с фиксированным JSON-выходом `{mode: INFORMATIONAL|LOCAL_ACTION|EXTERNAL_ACTION|DESTRUCTIVE|HIGH_IMPACT, capabilities:[…], confidence}` и **абстенцией**: если `confidence < 0.7` или слои расходятся → трактовать как ACTION (fail-closed) и требовать улику.
3. Никогда не понижать класс от ACTION к INFORMATIONAL по решению модели (монотонность: модель может только повышать строгость).
4. Тесты: property-based (hypothesis) — для любой строки с глаголом действия из лексикона класс ≠ INFORMATIONAL; метаморфические — перестановка предложений/регистр/ё→е не меняют класс.

### 2.5 Контракт хука `gate_completion` (EH-05) — MUST
`_malformed_hook_result` MUST требовать ключ `requeue` при `verdict=="FAIL"`; отсутствие = `CriticalHookFailure`. Обновить `review_gate`, `action_gate`, `action_contract`.

## 3. Математика и инварианты

- **INV-1 формально:** пусть `R` — множество подписанных receipts run'а, `C` — требуемые способности.
  `completed ⇔ ∀c∈C ∃r∈R: r.cap=c ∧ r.verified ∧ HMAC_k(r∖sig)=r.sig ∧ r.observed_at > r.started_at`.
  Отсутствие ключа `k` у модели ⇒ вероятность подделки = вероятность подбора HMAC-SHA256 ≈ 2⁻²⁵⁶ (пренебрежимо).
- **Свежесть:** `observed_at − finished_at ≤ Δmax` (по умолчанию 60 с) — иначе `stale_observation`, `verified=False`.
- **Монотонность классификации:** порядок строгости `INFORMATIONAL < LOCAL_ACTION < EXTERNAL_ACTION < DESTRUCTIVE < HIGH_IMPACT`; итог = `max(layer1, layer2)`.
- **Ложные срабатывания гейта** стоят одного повтора (≤ 1 доп. вызов); ложные пропуски стоят false-success. Асимметрия ⇒ порог абстенции 0.7 выбран так, чтобы FN → 0 при FP ≤ 10 % на корпусе `docs/testing/sessions/*.jsonl` (проверять тестом на корпусе).

## 4. Приёмка (детерминированные тесты)

1. `test_evidence_unsigned_verified_is_rejected` — `Evidence(verified=True, source="journal:x")` без подписи → контракт FAIL.
2. `test_evidence_signature_tamper` — изменение одного байта payload → `verify()==False`.
3. Для каждой строки таблицы 2.2 — пара тестов: (а) `tool_calls` есть, пост-состояние не изменилось → `verified=False`, задача не `completed`; (б) пост-состояние изменилось → `completed`.
4. `test_no_direct_completed_writes` — grep по репозиторию: единственная запись `status="completed"` для `tasks` — в `finalize()`.
5. `test_classifier_abstains_fail_closed` — фраза вне лексикона с explicit `confidence=0.3` → ACTION.
6. Корпусный тест: все 414 записей сессии `20783913fa36` — ни одна `task.completed` без подписанного receipt.
7. Регрессия: `tests/test_action_gate.py`, `test_action_contract.py`, `test_action_router.py` без изменений семантики.

## 5. Вне объёма
Визуальная верификация (Visual State Engine) — V3 boundary. Верификаторы используют только DOM/ФС/процессы/git/БД.

## 6. Чек-лист оценки 10/10
- [ ] Ни одна улика `verified=True` не принимается без HMAC (EH-01)
- [ ] 11 верификаторов пост-состояния, `tool_result_only ⇒ verified=False` (EH-02)
- [ ] Один `finalize()`, grep-тест (EH-04)
- [ ] Классификация с абстенцией и монотонностью (EH-03)
- [ ] `requeue` обязателен при FAIL (EH-05)
- [ ] Корпусный тест на реальной сессии зелёный
