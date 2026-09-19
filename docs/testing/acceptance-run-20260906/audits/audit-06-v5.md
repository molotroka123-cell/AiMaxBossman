# Audit 06 — V5 (bossman_shared/objective_*): research-only

- Repo: `C:\Bossman-acceptance-20260906`, HEAD `6dfb1e9`
- Scope: `bossman_shared/{objective_spec,objective_store,objective_admission,objective_observer,objective_reconcile,objective_recovery,objective_world_state}.py`, `tests/test_v5_*.py`, `docs/v5/EPOCH_5_PLAN.md` (N0–N8, H01–H10), `docs/v5/V5_RELEASE_SCORECARD.md`
- Duplicate-check: `docs/testing/acceptance-run-20260906/OPEN_FINDINGS.json` прочитан; ниже только НОВЫЕ находки (пересечения с HOST-SENSITIVE-PERF-GATES / CC-VIDEO-* явно помечены).
- Код не изменялся; создан только этот файл.

## 1. Таблица N0–N8 (сверка плана, кода и scorecard)

| Node | Scorecard | Что реально в коде | Расхождение / дефект |
|---|---|---|---|
| N0 | NOT_RUN | Корректно: нигде нет активации standing autonomy; `create` только DRAFT (`objective_store.py:244-245`) | Нет |
| N1 | PASS («persistence/CAS/migration») | CAS/once-only надёжны (PK+UNIQUE+BEGIN IMMEDIATE). **Миграции нет вообще**: только отказ при более новой схеме (`objective_store.py:211-220`); старая/частичная схема открывается и падает с непонятной ошибкой; v4→v5 миграции не существует; импорт PAUSED запрещён | A6-02 (P2): «migration» в основании PASS-оценки N1 не подтверждается кодом; H10 «default paused on migration» не реализуем |
| N2 | PASS | Observers чистые, value_digest без времени, UNKNOWN-доминирование корректно | A6-05 (P2): transient FS-ошибка роняет весь batch наблюдения вместо деградации в UNKNOWN/dropped |
| N3 | PASS | PROPOSAL != AUTHORIZATION выдержан; порядок проверок fail-closed | A6-06 (P3): `state.condition` не перепроверяется при admit — миссия по уже SATISFIED-миру возможна |
| N4 | PARTIAL («fairness/aging NOT_RUN») | **Механизма fairness/aging не существует**, а не «не измерен»: `ConflictPort.claim` — только priority, без возраста/очереди; CONFLICT_HELD-отказы сбрасываются на пол без счётчика; «owner-visible blocker» из плана (EPOCH_5_PLAN.md:73-74) не реализован нигде; PENDING-бронь без TTL блокирует цель бесконечно | A6-01 (P1): N4 недооценён — «NOT_RUN» должно быть «ABSENT»; тест H07 кодифицирует голодание как ожидаемый исход |
| N5 | PARTIAL | `objective_improvement.may_promote` возвращает только eligibility | Вне находок (canary-hook см. A6-08) |
| N6 | PARTIAL | Не в области аудита (workspace) | — |
| N7 | PARTIAL | H01–H10 in-process (см. карту §3) | A6-04 (P2): клауза H06 «invalidate source» не проверяема end-to-end — proposal не биндит source_revision для перепроверки на effect boundary |
| N8 | PARTIAL (canary/rollback NOT_RUN) | `prepare_rollback` — только данные; ROLLBACK_ORDER без исполнителя; «fence_effects» нигде не реализован; canary-хука в admission нет; снапшот неполный | A6-07 (P3), A6-08 (P3); см. также A6-01 (fence без TTL) |

## 2. Находки

### A6-01 · P1 — Fairness/aging отсутствует как механизм; отказы не порождают owner-блокер; PENDING-бронь без TTL
- **file:line**: `bossman_shared/objective_admission.py:131-136` (ConflictPort.claim — только `priority`, нет «waiting_since/age»); `objective_admission.py:534` (CONFLICT_HELD/COOLDOWN_ACTIVE-отказ просто возвращается и теряется); `bossman_shared/objective_store.py:57-75` (в `v5_objectives` нет колонок consecutive_refusals/blocked_reason/last_refusal_at); `objective_store.py:561-563` (`objective_has_unreconciled_admission` — блок цели без ограничения по времени); `tests/test_v5_golden_missions.py:423-424` (5 повторных отказов лузера закодированы как ожидаемое поведение).
- **symptom**: цель B с более низким priority при постоянно удерживаемом conflict-key целью A отбивается навсегда; повторные отказы не накапливаются, владелец ничего не видит; застрявшая RESERVED-бронь (ADMISSION_RECOVERY_REQUIRED, `objective_admission.py:511-533`) блокирует все будущие admit цели до ручного `recover` — без ограничения по времени.
- **root-cause**: план N4 требует «Bounded queue aging prevents starvation» (EPOCH_5_PLAN.md:87-88) и «repeated failure produces an owner-visible blocker and stops automatic proposals» (EPOCH_5_PLAN.md:73-74); в V5-коде нет ни очереди, ни счётчика отказов, ни aging-буста, ни TTL на PENDING — отсюда и честное «fairness NOT_RUN» в scorecard, но формулировка «NOT_RUN» скрывает, что покрывать нечем.
- **fix**: (1) добавить в `v5_objectives` счётчики последовательных отказов admit + `blocked_reason` и писать их в refusal-пути `AdmissionKernel.admit`; (2) при N подряд отказах переводить objective в owner-visible blocked (новое поле, не lifecycle); (3) в `ConflictPort.claim` передавать waiting-since и выбирать держателя по (age, priority) при конфликте; (4) TTL на RESERVED-бронь в `recover`/`claim_admission` (например, PARKED после таймаута).
- **репродукция**: два активных objective с общим `conflict_key`, A — priority 10, B — priority 5, A циклически пере-admit'ится (свежие наблюдения, cooldown=0); каждый proposal B получает `CONFLICT_HELD` бесконечно; счётчиков/блокера нет — голодание вечное и невидимое владельцу.

### A6-02 · P2 — Миграции схемы store нет; старая/частичная схема падает неконтролируемо; импорт PAUSED противоречит плану
- **file:line**: `bossman_shared/objective_store.py:211-220` (проверяется только `int(row["value"]) > SCHEMA_VERSION`; ветка «старее» отсутствует); `objective_store.py:54-100` (SCHEMA только `CREATE TABLE IF NOT EXISTS` — колонки не добавляются); `objective_store.py:244-245` (`create` отказывает в PAUSED); `docs/v5/EPOCH_5_PLAN.md:101` (N1 exit evidence: «migration from populated V4 data»), `:145` (H10 «default paused on migration»), `:191` («Import is DRAFT or PAUSED»).
- **symptom**: БД, записанная схемой меньшего номера (или с недобавленными колонками), открывается без ошибки, а затем падает с `IndexError`/`sqlite3.OperationalError` на `row["<новая колонка>"]` (`_row_state`, `objective_store.py:180-199`) вместо ясного «needs migration». Пути v4→v5 нет ни в коде, ни в тестах; «default paused on migration» реализовать не на чем (импорт жёстко DRAFT).
- **root-cause**: инициализация обрабатывает только forward-несовместимость; миграционный шаг H10/N1/N8 не был написан, но N1 в scorecard помечен PASS с «migration» в основании.
- **fix**: при `version < SCHEMA_VERSION` — либо явный отказ с кодом «migration required», либо реальный мигратор (`ALTER TABLE` + дефолт lifecycle=DRAFT/PAUSED); добавить migration-тест «populate old-schema DB → open → всё читается, lifecycle по умолчанию PAUSED».
- **репродукция**: создать файл v5.sqlite3, руками записать в `v5_schema` version='0' и таблицу без колонки `stopped`; открыть `ObjectiveStore(path)` → ошибки нет; `store.get(...)` → IndexError вместо диагностики.

### A6-03 · P2 — `set_stopped` не проверяет владельца: чужой может снять owner-stop
- **file:line**: `bossman_shared/objective_store.py:406-421` (сигнатура без `owner_id`, проверка владельца отсутствует) vs `transition` `:300-319`, `revise` `:362-384`, `enroll_sources` `:330-349` (везде identity-check).
- **symptom**: `set_stopped(objective_id, False, reason=..., expected_version=v)` снимает остановленную владельцем цель без какого-либо подтверждения владения; после этого admission снова пропускает работу (`objective_admission.py:410-411`).
- **root-cause**: stop описан в докстринге как «Resolve the owner's stop conditions», но-owner-bound проверка забыта; все остальные owner-действия её имеют.
- **fix**: добавить обязательный `owner_id` и отказ при `owner_id != state.owner_id` (как в `revise`).
- **репродукция**: создать/остановить цель owner-1; вызвать `store.set_stopped(oid, False, reason="x", expected_version=store.get(oid).version)` от имени любого другого кода — успех, цель снова admits.

### A6-04 · P2 — «Invalidate source» (H06) непроверяем на effect boundary: proposal не биндит source_revision
- **file:line**: `bossman_shared/objective_admission.py:167-200` (`AdmissionProposal` несёт только `observation_digests` — source_revision не извлекаемы для перепроверки); `objective_admission.py:553-579` (`reauthorize_at_effect_boundary` перепроверяет digest/revision/expiry/freshness-по-времени, но не текущие ревизии источников); план: `docs/v5/EPOCH_5_PLAN.md:141` (H06: «…invalidate source; stale evidence cannot admit work»).
- **symptom**: если источник инвалидирован (bump `source_revision`) между proposal и эффектом, READY-бронь и effect-boundary recheck остаются зелёными — единственная защита это time-based `freshness_deadline`. Юнит-покрытие только `tests/test_v5_observers.py:282` (`wrong_source_revision_is_unknown`); end-to-end «source invalidation blocks queued work» отсутствует.
- **root-cause**: идентичность наблюдения включает source_revision (`objective_observer.py:154-163`), но в payload proposal'а сохраняется лишь дайджест — обратной связи «дайджест ↔ текущая ревизия источника» при повторной проверке нет.
- **fix**: биндить в `AdmissionProposal`/payload mapping `source_ref → source_revision` (из observation batch при build) и в `reauthorize_at_effect_boundary` сверять с текущим заявленным состоянием источника (новый порт) либо с `store`-уровневым реестром ревизий; отказ с новым кодом `SOURCE_INVALIDATED`.
- **репродукция**: пропустить H06-сценарий «bump source_revision после admit, до эффекта» — `reauthorize_at_effect_boundary` вернёт True (если не истекло время), т.е. клауза плана не выполняется; теста, фиксирующего это, нет.

### A6-05 · P2 — Transient FS-ошибка в одном observer'е роняет весь batch collect (Windows sharing violation)
- **file:line**: `bossman_shared/objective_observer.py:460` (`observations = tuple(observer.observe(now=now) for observer in kept)` — без try/except); `objective_observer.py:206-218` (`_hash_file`: `stat()` → race, потом чтение); `objective_observer.py:234-243` и `:286-323` (FileState/Directory: is_file→open TOCTOU).
- **symptom**: файл, удалённый/залоченный между `is_file()` и чтением (WinError 32 / PermissionError / FileNotFoundError), выбрасывает исключение из `collect()` — вся сенсорная итерация теряется, деградации в UNKNOWN/dropped нет; на Windows это штатное событие (ср. родственный класс ошибок в OPEN_FINDINGS `CC-VIDEO-READVERIFICATION-WINFILE`, но там другой компонент).
- **root-cause**: инвариант «refusal degrades to UNKNOWN» реализован в `evaluate`, но не в месте сборки наблюдений: `collect` не изолирует падение отдельного observer'а.
- **fix**: в `collect()` оборачивать `observer.observe(...)` в try/except OSError и добавлять `(ref, "observe_failed")` в `dropped` — UNKNOWN-семантика сохраняется, один битый файл не глушит сенсор.
- **репродукция**: `DirectoryStateObserver` на каталоге, где параллельный поток удаляет файл во время скана → `collect(...)` поднимает исключение вместо ObservationBatch с dropped.

### A6-06 · P3 — Admission не перепроверяет `condition`: миссия по уже SATISFIED-миру
- **file:line**: `bossman_shared/objective_admission.py:408-411` (admit проверяет lifecycle и stopped, но не `state.condition`); сценарий разрешён цепочкой: DEVIATED-proposal → параллельный reconcile пишет SATISFIED → admit всё ещё пропускает.
- **symptom**: цель, чьё состояние уже подтверждено как SATISFIED (мир починился сам или другим миссион-каналом), получает новую миссию по «зависшему» DEVIATED-предложению — траты бюджета и осцилляция (риск против H07 «no repair oscillation»).
- **root-cause**: «CURRENT canonical stores» перепрочитываются не полностью: condition — текущий durable факт, но исключён из recheck.
- **fix**: в `admit` (и/или `claim_admission`) отказывать при `state.condition == "SATISFIED"` с кодом `condition_not_deviated` (UNKNOWN не блокировать — там переоценка легитимна).
- **репродукция**: admit → до settle написать `store.set_condition(..., "SATISFIED", evidence_ref=...)`; повторный admit свежего PENDING-предложения проходит, миссия строится.

### A6-07 · P3 — Rollback-снапшот неполный; шаги ROLLBACK_ORDER неисполнимы (fence не существует)
- **file:line**: `bossman_shared/objective_recovery.py:266-275` (снапшот без `spec_json/spec_digest/revision/enrolled_sources/stopped`, без journal/proposals/evidence); `objective_recovery.py:65-71` (ROLLBACK_ORDER — «fence_effects»/«drain_park_missions» не имеют реализации нигде в репо); признание границы — `objective_admission.py:550-552` («dispatch still needs the canonical executor's fencing/authorization guard»).
- **symptom**: восстановление из снапшота даёт неразбираемую цель (нет spec-байтов); «Subsequent unauthorized effects = 0» (H05) после reauthorize=True держится только на исполнительном fence, которого в этом срезе нет.
- **root-cause**: N8 «rollback rehearsal NOT_RUN» честно заявлен, но конкретные дыры стоит зафиксировать до репетиции.
- **fix**: включить в снапшот полный `spec_json`, digest, revision, enrollment, stopped + ссылку на ledger/evidence; для fence — минимальный store-флаг «effects_fenced_until», проверяемый в `reauthorize_at_effect_boundary`.
- **репродукция**: `prepare_rollback(...).snapshot()` — ключей spec_json/revision нет; `grep -r "fence" bossman_shared` — только план-данные.

### A6-08 · P3 — Canary не имеет точки входа в admission: cohort некуда ограничить
- **file:line**: `bossman_shared/objective_improvement.py:116-150` (`may_promote` возвращает только eligibility); `bossman_shared/objective_admission.py` — нет ни поля canary/cohort, ни порта; план: `docs/v5/EPOCH_5_PLAN.md:192-193` («Canary cohort is opt-in and finite»).
- **symptom**: даже при реализации N8 нечем ограничить cohort: spec/admission не несут признака canary, лимита cohort-размера и opt-in флага — canary-«admission» пришлось бы приклеивать снаружи.
- **fix**: добавить в ObjectiveSpec (или runtime-состояние) `canary_cohort: bool/size` и порт canary-реестра, проверяемый в `AdmissionKernel.admit` до reserve.
- **репродукция**: grep `canary` в `bossman_shared/objective_admission.py` — 0 вхождений.

### A6-09 · P3 — Store-уровень: cross-owner чтение по умолчанию; FK-прагма без FK-ограничений
- **file:line**: `bossman_shared/objective_store.py:280-296` (`list_objectives(owner_id=None)` возвращает цели ВСЕХ владельцев); `objective_store.py:263-278,674-679` (`get/get_spec/journal` — только objective_id, владелец не проверяется; ID типа "build-health" угадываемы); `objective_store.py:227` (`PRAGMA foreign_keys=ON` при полном отсутствии FOREIGN KEY в SCHEMA `:54-100` — прагма no-op).
- **symptom**: на уровне store возможен cross-owner доступ/листинг; proposals могут ссылаться на несуществующие objective_id (`insert_proposal_once:487-508` — проверок нет, UPDATE по чужому id тихо влияет на 0 строк).
- **root-cause**: store сознательно «thin», но инвариант «zero cross-owner leakage» (EPOCH_5_PLAN.md:155) не имеет ни enforced-границы, ни теста на store-уровне.
- **fix**: обязательный owner_id в read-API store (или документированный сервисный барьер + тесты); добавить `FOREIGN KEY(objective_id) REFERENCES v5_objectives` для proposals/reservations.
- **репродукция**: `store.list_objectives()` после создания целей двух владельцев — видны оба; `insert_proposal_once(objective_id="ghost", ...)` — строка вставлена.

### A6-10 · P3 — Граничные семантики cooldown
- **file:line**: `bossman_shared/objective_spec.py:51` (`cooldown_seconds` допускает 0 — валидно), `:387` (`now - last < cooldown` — строгое «меньше»: при `now-last == cooldown` пропуск разрешён); `objective_admission.py:447-450` (та же семантика в kernel); `objective_store.py:504-505` (`last_proposal_at` ставится при вставке предложения, даже если оно никогда не будет admit'нуто — например, его admission отклонён портами).
- **symptom**: (а) ровно на границе cooldown пропуск разрешён — недокументировано и не покрыто тестом; (б) cooldown-часы тикают от создания proposal'а, поэтому серия предложений, которые так и не стали работой (отказ политикой/бюджетом), глушит новые предложения на весь window — консервативно, но это скрытый фактор starvation (см. A6-01); (в) `cooldown_seconds=0` легален при `max_missions>0` — единственный тормоз осцилляции остаётся квота.
- **fix**: зафиксировать границу тестом (`<=` vs `<`) и задокументировать; рассмотреть учёт cooldown от момента последнего УСПЕШНОГО admit (READY) вместо creation; для (в) — минимальный ненулевой cooldown при max_missions>0.
- **репродукция**: proposal с `created_at=T`, cooldown=60, admit в `T+60.0` — проходит; proposal, отклонённый политикой в T, блокирует следующую попытку до `T+60` даже при свободных портах.

## 3. Карта H01–H10 → реальные тесты (file:line)

| H | Основной тест | Дополнительное покрытие | Чего не хватает |
|---|---|---|---|
| H01 | `tests/test_v5_golden_missions.py:218` | `test_v5_reconcile`-блок: `test_v5_golden_missions.py:533,547,558,574`; `test_v5_recovery.py:229,242,258` | Реального «build/test artifact»-оракула нет: эффект — запись файла в тесте (in-process); tier по плану (:147-149) не достигнут |
| H02 | `test_v5_golden_missions.py:276` | `test_v5_observers.py:239` (no-change gate) | — |
| H03 | `test_v5_golden_missions.py:299` | `test_v5_admission.py:277` (ungranted capability) | «Read-back of draft» оракул упрощён до отказа admit |
| H04 | `test_v5_golden_missions.py:320` | `test_v5_recovery.py:129,142,155,165,175,187,197,210,312,323,335`; `test_v5_connection_lifetime.py:15,24`; `test_v5_admission_binding_regressions.py:150,170,263` | — |
| H05 | `test_v5_golden_missions.py:346` + `:369` (expiry) | `test_v5_admission.py:212,223,234`; `test_v5_admission_binding_regressions.py:93,112,131` | Диспетчерского fence между reauthorize=True и эффектом нет (A6-07) |
| H06 | `test_v5_golden_missions.py:381` | `test_v5_admission.py:259`; `test_v5_admission_binding_regressions.py:170,251`; `test_v5_observers.py:190,346`; out-of-order ingest `test_v5_observers.py:346` | Клауза «invalidate source» — только юнит `test_v5_observers.py:282`, e2e нет (A6-04) |
| H07 | `test_v5_golden_missions.py:402` | `test_v5_admission.py:298`; `test_v5_admission_binding_regressions.py:251` | Priority-порядок и interactive-over-background (план :87-88) не покрыты; тест кодифицирует голодание (A6-01) |
| H08 | `test_v5_golden_missions.py:427` (PARTIAL) | `test_v5_admission.py:287,311`; `test_v5_admission_binding_regressions.py:212` | Egress floor NOT_RUN (честно заявлено) |
| H09 | `test_v5_golden_missions.py:446` | `test_v5_observers.py:164` (non-JSON check); `test_v5_objective_store.py:692` (tampered spec); `test_v5_intelligence_preservation.py:273` | Половина плана («manipulated recipe») не покрыта — recipes в срез не входят |
| H10 | `test_v5_golden_missions.py:472` | `test_v5_objective_store.py:339,354,366,377,388,677,692`; `test_v5_recovery.py:363,380,387`; `test_v5_human_speed.py:103` | Реальной миграции/rollback-репетиции нет (A6-02, A6-07) |

Прочее покрытие: производительность — `test_v5_human_speed.py:55,79,103` (CAS<10ms — известный host-гейт, см. HOST-SENSITIVE-PERF-GATES в OPEN_FINDINGS); connection lifetime — `test_v5_connection_lifetime.py:15,24,35`; adversarial admission — `test_v5_admission_binding_regressions.py` (22 теста, самое плотное место по гонкам).

## 4. Резюме

Наиболее значимые: **A6-01** (fairness/aging — механизм отсутствует, а не «не измерен»; N4 должен быть «ABSENT-mechanism», тест H07 закрепляет голодание) и **A6-02** (миграция схемы отсутствует при PASS-статусе N1). Далее: **A6-03** (снятие owner-stop без проверки владельца), **A6-04** (source invalidation непроверяем на effect boundary), **A6-05** (collect() падает от transient FS-ошибки вместо UNKNOWN). P3: A6-06..A6-10. Ни одна находка не дублирует OPEN_FINDINGS.json; A6-05 коррелирует с известным классом Windows file-handle ошибок, но относится к другому компоненту (objective_observer, а не video_studio).
