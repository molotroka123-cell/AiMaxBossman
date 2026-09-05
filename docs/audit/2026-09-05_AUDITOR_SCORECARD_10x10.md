# AiMaxBossman — независимый аудит 10×10 (роль: аудитор)

Дата: 2026-09-05 · Ветка: `claude/bossman-control-v03-43igbk` · HEAD на момент аудита: `e8f348d`
Метод: каждая из 10 категорий владельческого scorecard проверена ПО КОДУ (файл:строка), а не по
документации. Оценка «grounded» — моя; оценка «owner» — из исходного scorecard. Для каждой категории
с оценкой < 10 написано отдельное ТЗ исправления в `docs/audit/tz/TZ-NN_*.md`.

Принцип аудита: **утверждение без файла и строки — не улика**. Всё, что ниже, воспроизводимо `grep`-ом.

## 0. Итог

| # | Категория | Owner | Grounded | Δ | ТЗ | Главная причина, почему не 10 |
|---|---|---|---|---|---|---|
| 1 | Честность исполнения | 9 | **8** | −1 | TZ-01 | Доверие к уликам — по строковому префиксу `source.startswith("journal:")` (`contracts.py:188`), классификация — только regex RU/EN; наблюдение пост-состояния есть лишь для 4 видов (`file/db/browser/app`, `verification.py:230-246`) |
| 2 | Безопасность и периметр | 9 | **8** | −1 | TZ-02 | Секрет-скан — 6 паттернов (`tools/ci_secret_scan.py`), нет энтропийного детектора; сессия UI 720 ч (`sessions.py:27`); нет rate-limit на логин; pip-audit/bandit — `continue-on-error` |
| 3 | Инструменты / ОС | 8 | **7** | −1 | TZ-03 | Нет Windows-раннера в CI при 358 тестовых файлах с win-ветками; `agent.tools=[]` по умолчанию — способность выводится роутером только для browser; observe→act без структурного диффа |
| 4 | Организационный слой | 5 | **5** | 0 | TZ-04 | Библиотека есть (2 307 строк, SQLite, 4 тест-файла), но флаг OFF по умолчанию (`feature_flags.py`), нет HTTP/UI, нет планировщика шагов (контракт без `steps` → FAILED), `deadline` не проверяется, маркетплейс без исследования (starvation), математическая ошибка в `min_attempts` при затухании |
| 5 | Fleet Control Plane | 4 | **5** | +1 | TZ-05 | Есть аренда run'а с CAS (`engine.py:449-472`) и heartbeat, но нет fencing-токена → зомби-воркер после истечения аренды может дублировать сайд-эффект; нет реестра узлов/планировщика; Fleet OS лежит НЕ влитым ZIP-ом (70 файлов `bcc/v5/fleet/*`) |
| 6 | Память и контекст | 6 | **6** | 0 | TZ-06 | Скоупы — плоские строки без решётки наследования; `HashEmbedder` — заглушка вместо эмбеддингов (`context_engine/embeddings.py:19`); нет TTL/забывания кроме `valid_until`; изоляция доказана только на уровне равенства строки скоупа |
| 7 | Тесты и CI | 8 | **7** | −1 | TZ-07 | 3 626 тестов, 46 skip, нет измерения покрытия, нет property-based, нет mutation, один stress-тест, security-сканы advisory, нет windows-job |
| 8 | Наблюдаемость | 5 | **5** | 0 | TZ-08 | 105 видов событий, но таблица `events` не чистится (нет `delete(events_t)`), нет OTel/Prometheus, нет серверных гистограмм задержек по маршрутам, нет SLO; CEO-снимок (`control_plane.py`) не выставлен по HTTP; детектор dead-click сравнивает лишь число DOM-узлов (`testing.js:187-191`) |
| 9 | Казначейство | 5 | **7** | +2 | TZ-09 | Жёсткий потолок сделан правильно (worst-case-first, RECONCILING, ratchet, file-lock), но `PRICE_TABLE` не содержит актуальных моделей → любой `claude-*-5` = отказ; `3.0 chars/token` НЕ верхняя граница для кириллицы/CJK → недорезерв; конверты казначейства не образуют разбиение (Σ детей ≤ родителя не проверяется); нет учёта локального GPU-времени |
| 10 | UX / миссии | 7 | **6** | −1 | TZ-10 | Словарь статусов UI не знает `blocked`/`capability_unavailable` (`components.js:256-270`), aria-атрибутов почти нет (0 в 20 из 27 страниц), RU-only без i18n, dead-click детектор даёт ложные срабатывания |

**Средняя grounded-оценка: 6.4 / 10** (после закрытия TZ-09 §2 и TZ-05 §2; исходно 6.0) — совпадает с консервативной оценкой владельца, но по другим причинам:
слой исполнения и периметр чуть ниже заявленного (доверие по строке, скан секретов), организация —
на уровне заявленного (библиотека готова, продукт — нет), флот — ниже (нет fencing, ZIP не влит).

### 0.1 Обновления Grounded по закрытым ТЗ (ведёт исполнитель, не аудитор)

| ТЗ | Было | Стало | Коммит(ы) | Что закрыто (по коду) | Что осталось |
|---|---|---|---|---|---|
| TZ-09 §2 | 5 | 7 | `e724a44` | `PRICE_TABLE` 5 семейств + `PRICE_TABLE_AS_OF`; `estimate_tokens_upper` по скрипту (кириллица 1.8, CJK 0.7 chars/token); worst = `in·max(p_in,p_cache_write)+out·p_out`; `price_version` в ledger; INV-3 `PartitionViolation` в `ResourceTreasury` | TR-04 usd-как-view, TR-05 GPU-секунды, TR-06 burn-rate |
| TZ-01 §2.1 | 8 | 8 (без роста до §2.2–2.3) | _EH-01 коммит_ | `bossman_shared/evidence.py` HMAC; `Evidence.sig/signer/nonce/issued_at`; `contracts._trusted` = валидная подпись ∧ signer ∈ TRUSTED_SIGNERS (префикс `source` — информационный); `TaskJournal.record` подписывает закрытый шаг; улики из журнала — только из подписанных шагов. Тесты `bossman-core/tests/test_v3_evidence_signing.py` (6), `tests/test_evidence_signing_shared.py` (4) | §2.2 верификаторы пост-состояния (11), §2.3 `finalize()` + grep-тест, §2.4 абстенция, §2.5 `requeue` |
| TZ-05 §2 | 3 | 5 | _FL-01 коммит_ | `task_runs.fence` (+1 на claim и на recover-requeue); `_start/_save_checkpoint/_finish` условны по fence → `FencedOut`; `assert_fence` ДО внешнего эффекта в `_run_tool_now` и в V3-адаптере; heartbeat условный (0 строк → отмена владельца, выход без записи); replay-guard неидемпотентного шага по `(task, step, tool, args_hash)` из прежней попытки. Тесты `command-center/tests/test_fence_fl01.py` (4) | §3 реестр/планировщик в V2-пути (в V3 `bossman_v3/fleet` есть, флаг `BCC_FLEET_ENABLED` нет), UNIQUE-индекс idem, ZIP не удалён, §4 GPU |

## 1. Сквозные инварианты (то, что должно быть верно во всех категориях)

Эти формулы — единый язык всех десяти ТЗ. Реализация каждой обязана ссылаться на номер инварианта.

- **INV-1 (честность):** `COMPLETED(task) ⇔ ∀ c ∈ Required(task): ∃ r ∈ Receipts(run): r.capability = c ∧ r.verified ∧ SigValid(r) ∧ r.observed_at > r.started_at`.
  Сейчас выполняется левая половина без `SigValid` (доверие по префиксу) и без `observed_at > started_at` для всех видов, кроме `bcc.v2.verification`.
- **INV-2 (единственность сайд-эффекта):** для каждого `(task_id, step_id, idempotency_key)` число исполненных сайд-эффектов ≤ 1 при любом числе рестартов и воркеров. Требует fencing-токена (TZ-05) поверх журнала V3.
- **INV-3 (разбиение бюджета):** для любого узла конвертов `Σ_{child} limit(child) ≤ limit(parent)` и `spent(parent) = Σ spent(child) + spent_direct(parent)`; резерв берётся сверху вниз атомарно (TZ-09).
- **INV-4 (решётка памяти):** `read(s)` возвращает `own(s) ∪ ⋃_{a ∈ ancestors(s)} exported(a → s)` и никогда `sibling(s)`; экспорт монотонен по уверенности (`confidence` не растёт) (TZ-06).
- **INV-5 (наблюдаемость):** каждая задача с сайд-эффектом оставляет цепочку событий
  `request.classified → capability.selected → permission.checked → action.started → action.result → action.verified → task.finalized` с одним `trace_id` (TZ-08).
- **INV-6 (fail-closed):** неизвестная цена, неизвестная модель, неизвестный вид улики, неизвестный скоуп, неизвестный узел флота — отказ, а не «по умолчанию разрешено». Уже так в `fable_budget` и `contracts.validate`; должно быть так везде.

## 2. Реестр находок (сводно; детали и математика — в ТЗ)

Формат: `ID · серьёзность · файл:строка · суть`. Серьёзность: P0 — ломает инвариант выше; P1 — ломает
обещание категории; P2 — качество/долг.

### Категория 1 — честность исполнения (TZ-01)
- EH-01 · P1 · `bossman-core/bossman_v3/organization/contracts.py:188-189` · `_trusted(e)` = `e.source.startswith("journal:")`. Улика — это словарь; любой код (в т.ч. десериализованный из ответа модели `WorkResult.from_dict`) может выставить `source="journal:x"` и `verified=True`. Доверие должно быть свойством подписи, а не строки.
- EH-02 · P1 · `command-center/bcc/v2/verification.py:230-246` · наблюдение пост-состояния реализовано для `file/db/browser/app`; для `terminal/mcp/memory/schedules/images/github/openclaw` доказательством служит строка `tool_calls` (+ `call_filter`), т.е. «инструмент вызван», а не «мир изменился». Для `github` нет проверки remote SHA, для `memory` — read-back, для `schedules` — существования строки расписания.
- EH-03 · P2 · `command-center/bcc/features/action_contract.py:99-160`, `action_router.py:83-101` · классификация намерения — регулярные выражения RU/EN с окном 80 символов. Нет абстенции/fail-closed для неразобранных формулировок; нет тестов-фаззеров.
- EH-04 · P2 · `bossman-core/bossman/runner.py:448`, `bossman-core/bossman/computer_operator/manager.py:103` · пути `status="completed"` вне `engine._finish` — нужен единый `finalize()` с проверкой INV-1 (аудит показывает 7 мест записи `completed`).
- EH-05 · P2 · `engine.py:720` · `res.get("requeue", True)` — отсутствие ключа = «повторить». Уже обойдено в `action_gate._verdict`, но контракт хука должен требовать ключ явно (malformed → CriticalHookFailure).

### Категория 2 — безопасность (TZ-02)
- SEC-01 · P1 · `tools/ci_secret_scan.py` · 6 регулярок (OpenAI, GitHub, AWS AKIA, private key, seed, password=). Нет: энтропийного детектора, Telegram bot token, Google `AIza`, Slack `xox`, JWT `eyJ`, Azure, OpenRouter отдельно (перекрыт `sk-`), `.env`-файлов в git.
- SEC-02 · P1 · `command-center/bcc/sessions.py:27` · TTL сессии 720 ч; нет idle-таймаута и ротации `sid` после логина.
- SEC-03 · P1 · `command-center/bcc/auth.py:65` · `check(token)` без учёта попыток: нет rate-limit/lockout на `POST /login`.
- SEC-04 · P2 · `.github/workflows/command-center-ci.yml:120-127`, `bossman-core-ci.yml:158-165` · `pip-audit`/`bandit` с `continue-on-error: true` — не гейт.
- SEC-05 · P2 · = EH-01 (подделка доверенной улики).
- SEC-06 · P2 · нет hash-chain для журнала событий/аппрувов: запись `approvals` можно изменить в БД без следа.

### Категория 3 — инструменты/ОС (TZ-03)
- TL-01 · P1 · `.github/workflows/*.yml` · все job'ы `ubuntu-latest`; при этом 358 файлов тестов содержат ветвления `win32`/`nt`. Windows — целевая ОС владельца, а CI её не исполняет.
- TL-02 · P1 · `command-center/bcc/tools.py:allowed_tools_for` + `db.py agents.tools=[]` · способность прикрепляется роутером только для browser (`action_router._before_run`); для terminal/apps/memory/… агент без `tools` честно блокируется (`CAPABILITY_UNAVAILABLE`), но не получает инструмент автоматически при разрешённой политике.
- TL-03 · P2 · `engine.py:582,606` · `max_steps` по умолчанию 4 (агент) / 1 (`make_stack`); observe→act цикл не ограничен «нет прогресса N шагов подряд».
- TL-04 · P2 · нет `capability manifest` (платформа, способ верификации, idempotency) на исполнитель — знания размазаны по `action_contract.Capability` и `ToolSpec`.

### Категория 4 — организация (TZ-04)
- ORG-01 · P1 · `bossman-core/bossman_v3/feature_flags.py` · `organization = master ∧ BOSSMAN_V3_ORGANIZATION`, оба по умолчанию `False`; нет HTTP-маршрута, нет UI, нет запуска в `bcc` — слой существует только в тестах.
- ORG-02 · P1 · `docs/v3/organization/HANDOFF.md` («контракт без `steps` завершается FAILED») · нет планировщика «цель → шаги» для произвольной цели; `bossman.company.planner` — только seo/generic.
- ORG-03 · P1 · `contracts.py:74` (`deadline`) · поле есть, в `runtime.py` слово `deadline` не встречается → SLA не исполняется.
- ORG-04 · P1 (математика) · `learning.py:19,74` · `DECAY=0.9` применяется ко ВСЕМ счётчикам, поэтому `attempts` после n наблюдений = `(1−0.9ⁿ)/0.1` < 10 всегда; `failing_agents(min_attempts=2.0)` после ровно 2 попыток видит `attempts=1.9` и не срабатывает; `reliability` ограничена сверху `(1+10)/(2+10)=0.917` при 100 % успехов. Нужна эффективная выборка `n_eff` и раздельное затухание.
- ORG-05 · P1 (математика) · `marketplace.py:95-100` · сортировка по точечной оценке `reliability` → агент без истории (0.5) никогда не выигрывает у агента с 0.9 в том же tier → отсутствие исследования (starvation новых агентов). Нужен UCB/Thompson по Beta-постериору.
- ORG-06 · P2 · `marketplace.py:86` · `cost_per_call_usd > c.budget.usd` сравнивает цену ОДНОГО вызова с бюджетом контракта; ожидаемое число вызовов = число шагов × попыток не учитывается.
- ORG-07 · P2 · `treasury.py` · конверты в памяти; `restore()` теряет резервы; нет проверки INV-3 (лимит миссии может превышать лимит отдела).
- ORG-08 · P2 · `runtime.py:444-467` · топологическая сортировка есть; нет компенсаций (saga) для частично выполненных составных миссий — только «преserve completed».

### Категория 5 — флот (TZ-05)
- FL-01 · P0 · `command-center/bcc/engine.py:449-472, 520-533` · аренда с CAS и heartbeat, но без fencing-токена: воркер A, потерявший аренду (GC-пауза, сон ноутбука), продолжает исполнять шаг, пока воркер B уже заново взял run → два сайд-эффекта. Журнал V3 защищает только от рестарта того же процесса.
- FL-02 · P1 · нет реестра узлов (AI MAX / ноут / RunPod) с heartbeat и ёмкостью; `remote_client` — реестр мобильных устройств, не исполнителей.
- FL-03 · P1 · `AiMaxBossman_Fleet_OS_Complete_Foundation_DropIn.zip` · 70 файлов (`registry, scheduler, leases, work_stealing, dead_letter, resume, retry, privacy, topology, node_agent…`) лежат в корне репозитория как ZIP — не влиты, не протестированы в CI, не защищены флагом.
- FL-04 · P2 · `resource_brain/brain.py` · аренда ресурсов локальна процессу; нет кросс-узловой ёмкости.
- FL-05 · P2 · `metrics.py` · GPU best-effort (`nvidia-smi`), нет VRAM-учёта на модель при размещении.

### Категория 6 — память (TZ-06)
- MEM-01 · P1 · `bossman-core/bossman/context_engine/embeddings.py:19-25` · `HashEmbedder` (хеш-мешок) вместо эмбеддингов; семантический поиск — по сути лексический.
- MEM-02 · P1 · `memory_scope.py:83-97` · `read(scope)` — строгое равенство строки; нет наследования `organization → department → project → mission`; чтобы миссия видела правило отдела, нужен ручной `export`.
- MEM-03 · P2 · нет забывания/консолидации: `valid_until` — единственный механизм; нет дедупликации по смыслу.
- MEM-04 · P2 · нет «канареечных» тестов утечки между проектами на живом стеке (только unit на store).

### Категория 7 — тесты/CI (TZ-07)
- CI-01 · P1 · нет измерения покрытия и порога.
- CI-02 · P1 · = TL-01 (нет Windows job).
- CI-03 · P2 · нет property-based (hypothesis отсутствует в зависимостях), нет mutation-тестов, единственный stress — `test_stage9_resource_stress.py`.
- CI-04 · P2 · 46 `skip/skipif` без централизованного реестра причин и срока.
- CI-05 · P2 · = SEC-04 (сканы advisory).

### Категория 8 — наблюдаемость (TZ-08)
- OBS-01 · P1 · `command-center/bcc/events.py` · нет удаления/ротации `events` → рост без ограничений; тестовая сессия за час дала 414 записей журнала UI.
- OBS-02 · P1 · нет серверного измерения задержек по маршрутам (5–27 с в сессии `20783913fa36` видны только из клиента).
- OBS-03 · P1 · `control_plane.py` · `snapshot()` не выставлен по HTTP и не объединён с очередью V2/казначейством/флотом.
- OBS-04 · P2 · нет OpenTelemetry/Prometheus, SLO, error budget.
- OBS-05 · P2 · `ui/testing.js:187-191` · `viewFingerprint = childElementCount|querySelectorAll('*').length` — изменение текста/атрибутов невидимо → ложные `ui.dead_click` (подтверждено: `#bcc-testing-publish` меняет текст кнопки синхронно и всё равно помечен dead).

### Категория 9 — казначейство (TZ-09)
- TR-01 · P0 · `bossman_shared/fable_budget.py:PRICE_TABLE` · только `claude-sonnet-4-5`, `claude-haiku-4-5`, `claude-opus-4-1`. Любая актуальная модель (`claude-fable-5-1`, `claude-opus-5`, `claude-sonnet-5`) → `BudgetExhausted("unknown model")`. Fail-closed верен, но продукт не может платно работать на текущих моделях без правки кода.
- TR-02 · P1 (математика) · `_CHARS_PER_TOKEN_UPPER_BOUND = 3.0` · для кириллицы реальное отношение ≈ 2.0–2.6 симв/токен, для CJK ≈ 0.7–1.2. Значит для русского промпта `tokens_est = chars/3.0` занижает токены на 15–35 %, для CJK — в 2.5–4 раза. Это НЕ верхняя граница → недорезерв → потолок не «жёсткий» для не-латиницы (UI на русском).
- TR-03 · P2 (математика) · `estimate_worst_case_usd` берёт `max(rates)` (цена output) для ВСЕХ токенов, включая вход: перерезерв ×5 (sonnet) — при $3 потолке большие промпты отвергаются зря. Корректная верхняя граница: `in·max(p_in, p_cache_write) + out·p_out`.
- TR-04 · P1 · = ORG-07 (конверты не разбиение, резерв не атомарен между уровнями, оценка контракта не выводится из `fable_budget`).
- TR-05 · P2 · нет учёта локальных вычислений (GPU-секунды, кВт·ч) → «бесплатный» локальный запуск невидим для казначейства.
- TR-06 · P2 · нет прогноза исчерпания (burn-rate) и алертов.

### Категория 10 — UX (TZ-10)
- UX-01 · P1 · `ui/components.js:256-270` · нет `blocked`/`capability_unavailable` в тонах и подписях; после `action_contract` бэкенд отдаёт `failed` с `reasons=action_contract/…`, а UI показывает просто «ошибка».
- UX-02 · P1 · aria-атрибуты: 0 в 20 из 27 файлов страниц; нет управления фокусом в модалках.
- UX-03 · P2 · RU-only строки в коде, нет слоя i18n.
- UX-04 · P2 · = OBS-05 (ложные dead-click).
- UX-05 · P2 · нет экрана «что делает Bossman сейчас» на основе `control_plane.snapshot()` + очередь + казначейство (есть частично: home attention, mission console).

## 3. Порядок исполнения (для следующей сессии)

Порядок выбран по правилу «сначала инварианты, потом продукт»: P0 → P1 с максимальной связностью.

1. **TZ-09 §1–§3** (TR-01/02/03): цены и токенизация — 1 день, разблокирует платную работу и делает потолок жёстким для кириллицы.
2. **TZ-05 §1** (FL-01): fencing-токен в `engine` + журнале V3 — 1–2 дня, закрывает INV-2.
3. **TZ-01 §1–§2** (EH-01/02): подпись улик + верификаторы пост-состояния для оставшихся 7 видов — 3 дня, закрывает INV-1.
4. **TZ-04 §1–§4** (ORG-01/03/04/05): включение слоя по флагу, HTTP-снимок, deadline, исправление математики обучения/маршрутизации — 3 дня.
5. **TZ-08 §1–§3** (OBS-01/02/03): ретеншн событий, серверные гистограммы, `/api/control-plane` — 2 дня.
6. **TZ-02, TZ-07** (SEC-01..04, CI-01..03): скан, rate-limit, покрытие, Windows job — 2 дня.
7. **TZ-06, TZ-03, TZ-10** — 4–6 дней.

Определение «готово» для каждого ТЗ: все acceptance-тесты из раздела «Приёмка» зелёные на exact-SHA CI,
оценка категории ≥ 9 по чек-листу в конце ТЗ, ни один инвариант INV-1..6 не ослаблен.

## 4. Что аудит НЕ утверждает

- Не запускался живой owner-machine acceptance (нет доступа к машине владельца из этого окружения).
- Оценки категорий 3, 6, 10 частично основаны на статическом анализе UI/исполнителей без прогона Playwright-сьюта; числа тестов и файлов — из `grep`/`find` на HEAD `e8f348d`.
- Fleet OS ZIP оценён по списку файлов (`unzip -l`), содержимое не распаковывалось.

## 5. Мини-промпт для следующей сессии

```
Прочитай docs/audit/2026-09-05_AUDITOR_SCORECARD_10x10.md и docs/audit/tz/*.md.
Исполняй ТЗ строго в порядке раздела 3 scorecard (TZ-09 → TZ-05 → TZ-01 → TZ-04 → TZ-08 → TZ-02/07 → TZ-06/03/10).
Каждое ТЗ закрывай отдельным коммитом с ссылкой на ID находок (EH-xx, FL-xx, TR-xx…), тестами из раздела «Приёмка»
и обновлением таблицы в scorecard (колонка Grounded). Не ослабляй INV-1..INV-6. Не начинай V3-модули из V3 BOUNDARY.
После каждого ТЗ: targeted → группа → один полный прогон; exact-SHA CI; FREEZE_READY выставлять только при OPEN_P0=0.
```
