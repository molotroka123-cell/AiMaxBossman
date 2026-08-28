# APP #1 — AI WebCam Vision. Аудит и закрытие недоделок, этап 2

Дата: 2026-08-28. Ветка: `claude/bossman-control-v03-43igbk`, база `3999d5e`.

Среда замера — измерено, не предположено:

```
Linux-6.18.44-fc-v22-x86_64-with-glibc2.39, Python 3.11.15
ffmpeg / ffprobe в PATH:  ОТСУТСТВУЮТ
imageio_ffmpeg:           /usr/local/lib/python3.11/dist-packages/imageio_ffmpeg/
                          binaries/ffmpeg-linux-x86_64-v7.0.2  (исполняемый, 7.0.2-static)
GPU:                      нет
Камера Tapo C200:         нет
Клиническая CRM:          нет
```

**Windows в этой среде нет. Ничего "проверено на Windows" в этом отчёте нет и
быть не может.**

## Прогон

```
до аудита:     102 passed  (4.35 s)
после аудита:  207 passed  (8.73 s)
0 failed, 0 skipped, 0 xfail, 0 error
```

Ни один провал не спрятан. В наборе нет ни одного `skip`, `xfail`,
`except Exception: pass` и ни одной ветки, обходящей проверку по платформе.
Единственный условный путь — `conftest.ffmpeg_path`, который пропускает
тесты, если бинарника ffmpeg нет вообще нигде; в этой среде он не сработал
ни разу, потому что бинарник есть.

| Файл | Тестов | Новый | Что покрывает |
|---|---:|:---:|---|
| `test_crm_merge.py` | 23 | ✔ | схема, свежесть, пересечение записей, повтор, приоритет, личность |
| `test_classifier_temporal.py` | 17 | ✔ | выдержка, дребезг, провал детектора, TURNOVER, законность переходов |
| `test_pipeline.py` | 16 | | ворота движения, базовая линия, зоны, классификатор |
| `test_api_contract.py` | 14 | | контракт управления |
| `test_secret_hygiene.py` | 13 | | непрозрачность Secret, единая сборка URL, скрубер, канарейка |
| `test_privacy_defaults.py` | 11 | | закрытая по умолчанию поза |
| `test_metrics_daily.py` | 11 | ✔ | граница суток, TZ, перезапуск, двойной счёт, знаменатели |
| `test_video_input_stage2.py` | 10 | ✔ | канарейка, полный URL в логах, битый/устаревший кадр, подбор процессов |
| `test_privacy_stage2.py` | 10 | ✔ | отказ запуска, `-an`, отсутствие машинерии лиц/звука, манифест |
| `test_health_components.py` | 10 | ✔ | различение camera/CRM/detector, факты лаунчера |
| `test_transport_ffmpeg.py` | 9 | | реальная граница ffmpeg |
| `test_runtime_supervisor.py` | 9 | ✔ | долгоживущий цикл, восстановление, откат, отсутствие busy loop |
| `test_motion_ingress.py` | 9 | ✔ | честность про ONVIF, вендор-нейтральный `/hooks/motion` |
| `test_config.py` | 9 | | привязка окружения, валидация, запрещённые возможности |
| `test_storage.py` | 7 | | схема, соединения, метрики, права |
| `test_retry_reconnect.py` | 7 | | откат, бюджет, инъекция отказов |
| `test_shutdown.py` | 6 | | чистая остановка, подбор потомков |
| `test_queue_bounded.py` | 6 | | ограниченность памяти |
| `test_ffmpeg_discovery.py` | 6 | ✔ | поиск ffmpeg за пределами PATH |
| `test_independence.py` | 4 | | отсутствие импортов управляющего слоя |

Приложение осталось самостоятельным: `imports_bossman: false`,
`test_independence.py::test_no_control_plane_imports` проходит.

---

## Метки по семи пунктам

Ровно одна метка на пункт. **`MOCK` не превращается в `REAL`.**

| # | Пункт | Метка | Доказательство |
|---|---|---|---|
| 1 | Постоянный рантайм | `LOCAL PASS` | `test_runtime_supervisor.py` (9), в т.ч. `test_runtime_recovers_after_real_network_loss` на реальном ffmpeg; живой прогон: 18 циклов, 1 восстановление, чистый SIGTERM |
| 2 | Видеовход | `LOCAL PASS` | `test_video_input_stage2.py` (10) + `test_secret_hygiene.py` (13) + `test_transport_ffmpeg.py` (9); физическая камера — см. NOT RUN ниже |
| 3 | ONVIF | `NOT RUN` | подписка на события не реализована и не проверялась на прошивке; `test_motion_ingress.py` доказывает только запасной путь и честность декларации |
| 4 | Временной классификатор | `LOCAL PASS` | `test_classifier_temporal.py` (17) |
| 5 | Слияние с CRM | `MOCK CRM PASS` | `test_crm_merge.py` (23) против скриптованного клиента; реальная CRM — см. NOT RUN ниже |
| 6 | Метрики | `LOCAL PASS` | `test_metrics_daily.py` (11) + `test_storage.py` (7); живой прогон: `monitored_today: 8.35`, TZ `Europe/Prague` |
| 7 | Приватность | `LOCAL PASS` | `test_privacy_stage2.py` (10) + `test_privacy_defaults.py` (11) |

Отдельно, потому что это разные утверждения:

| Утверждение | Метка | Почему |
|---|---|---|
| Физическая камера Tapo C200: RTSP, аутентификация, stream1/stream2, ночной режим, PTZ | `NOT RUN` | камеры нет в среде |
| Подписка на события ONVIF на прошивке Tapo C200 | `NOT RUN` | камеры нет; клиента ONVIF нет в сборке |
| Реальная клиническая CRM | `NOT RUN` | эндпоинта нет |
| Windows | `NOT RUN` | платформы нет |

`REAL TAPO PASS` и `REAL CRM PASS` в этой среде недостижимы. Ни одна из
меток `LOCAL PASS` выше их не заменяет.

---

## Найденные расхождения между обещанным и действительным

Это главный результат аудита. Для каждого: что обещалось, что было на самом
деле, чем это грозило владельцу, и тест, который **падал до правки**.

### 1. ffmpeg объявлялся отсутствующим при живом бинарнике

**Обещано** (README): «`ffmpeg` is required for `file` and `rtsp` camera modes.
If it is missing the service says so … instead of pretending to work».

**Было**: `FfmpegRunner.resolve()` смотрел только явный путь и `shutil.which`.
На этой машине приложение отвечало
`"ffmpeg binary 'ffmpeg' not found in PATH"`, `health.status = unavailable`,
режимы `file` и `rtsp` недоступны — при том что рабочий статический бинарник
7.0.2 лежит в site-packages.

Хуже другого: **тесты этого не ловили, потому что тестовая обвязка сама
находила бинарник через `imageio_ffmpeg` и передавала путь явно.** Проверялся
не тот путь кода, которым пошёл бы продакшн. Отчёт `APP1_REPORT.md`
утверждал, что «ffmpeg-dependent tests found a real binary here through the
`imageio-ffmpeg` dev dependency» — это было правдой про тесты и неправдой про
приложение.

**Грозило владельцу**: развёртывание упирается в ложный отказ. Камера
объявлена неработающей там, где она работает.

**Тесты**: `test_ffmpeg_discovery.py`, 6/6 падали.
**Правка**: порядок поиска `AWV_FFMPEG_PATH → PATH → AWV_FFMPEG →
imageio_ffmpeg`; явно заданный путь никогда не подменяется запасным (опечатка
обязана всплыть); `health.ffmpeg` сообщает `source` и `searched`.

Живое подтверждение (сервис поднят без ffmpeg в PATH):

```json
{"available": true,
 "path": ".../imageio_ffmpeg/binaries/ffmpeg-linux-x86_64-v7.0.2",
 "source": "imageio_ffmpeg", "searched": ["PATH","AWV_FFMPEG","imageio_ffmpeg"]}
```

### 2. Побочный дефект того же корня: тесты могли проверять чужую копию кода

`conftest.py` не клал `src/` в `sys.path`. Editable-установка, указывающая на
другой рабочий каталог, молча выигрывала. Набор мог отчитываться о коде,
которого перед вами нет. Исправлено там же.

### 3. Постоянного рантайма не существовало

**Обещано** (`app.manifest.yaml`: `kind: workload`, README: «Operational
analytics for one fixed clinic camera»).

**Было**: работа только заданиями. `observe` жил не дольше
`duration_seconds` (потолок 3600 с), а при исчерпании бюджета повторов
(5 попыток) продюсер бросал исключение и наблюдение заканчивалось совсем.
Камера, пропавшая на минуту, останавливала сбор данных до следующей внешней
команды.

**Грозило владельцу**: дыры в данных, о которых он узнаёт по отчёту, а не по
тревоге. Ни одного места, где приложение само возвращается в строй.

**Тесты**: `test_runtime_supervisor.py`, 9/9 падали (модуля не было).
**Правка**: `runtime/supervisor.py` — чистый старт/остановка, идемпотентный
`stop`, пробуждение по сигналу остановки в середине отката, **ограниченный
откат вместо ограниченного числа попыток** (постоянному сервису нужен
capped backoff, а не конечный бюджет), восстановление после пропажи камеры,
после реального обрыва сети (refused RTSP на настоящем ffmpeg) и после
незапланированного исключения. Включается `AWV_RUNTIME_ENABLED`.

**Busy loop**: под каждым циклом есть пол `min_cycle_seconds = 0.05`, включая
пути отказа и «нет базовой линии»; `test_runtime_never_busy_loops` и
`test_missing_baseline_does_not_spin_the_loop` это фиксируют.

### 4. Здоровье не различало, что именно сломано

**Обещано** (`CONTRACT.md`): «`blockers` lists the exact reasons».

**Было**: `status ∈ {ok, degraded, unavailable}`. `degraded` одинаково
означал «нет базового кадра», «камера отвалилась» и «CRM молчит» — три разных
действия. **Отказ CRM вообще нигде в health не всплывал**: исключение
ловилось в `_crm_context`, писалось в `counters.last_error` и терялось.

**Грозило владельцу**: невозможно понять, кому звонить.

**Тесты**: `test_health_components.py`, 10/10 падали.
**Правка**: `components.camera / crm / detector` и `health_state` из
закрытого словаря `healthy | degraded | camera_offline | crm_unavailable |
detector_unavailable`, приоритет «детектор > камера > CRM». Выключенная CRM
это `disabled`, а не авария — иначе владелец научится игнорировать страницу
здоровья.

### 5. Карточка приложения в лаунчере читала поля, которых не существует

**Обещано** (`app.manifest.yaml`, комментарий в нём: «Карточка на главной
строится ОТСЮДА»).

**Было**: все три факта указывали в пустоту —

```
health.camera.state      -> нет такого поля
health.crm.state         -> нет такого поля
metrics.monitored_today  -> нет такого поля
```

**Грозило владельцу**: три прочерка на главной навсегда, без единого способа
понять почему.

**Тест**: `test_health_components.py::test_manifest_launcher_facts_resolve_against_live_payloads`
— падал со списком всех трёх путей.
**Правка**: пути исправлены на `health.components.*.state`; в `/api/v1/metrics`
добавлен `monitored_today` — и это **измеренное покрытие**, а не время по
часам.

### 6. Устаревший кадр выдавался за текущее состояние кабинета

**Было**: `Frame.ts` ставился в момент возврата из ffmpeg, и никто не
проверял, сколько кадр шёл. При медленном переподключении или ffmpeg,
отдавшем кадр на пятнадцатой секунде таймаута, анализировалась минута назад,
а результат записывался как состояние сейчас.

**Грозило владельцу**: выдуманная занятость. Пациент вышел, кадр опоздал — в
таймлайне продолжается работа, суточные минуты растут на пустой комнате.

**Тесты**: 3 из 10 в `test_video_input_stage2.py` падали.
**Правка**: `AWV_MAX_FRAME_AGE_SECONDS` (30 с), ошибка `StaleFrame`, счётчик
`frames_stale`; один опоздавший кадр не останавливает рантайм.

### 7. Гистерезис считал кадры, а не время

**Обещано** (`CRM_INTEGRATION.md`): «A new state must repeat
`AWV_DEBOUNCE_SAMPLES` times before it takes effect, so one noisy frame cannot
flip the room into `CLINICAL_WORK`».

**Было**: правда про «один кадр» и неправда про всё остальное. Два кадра при
1 Гц это две секунды, при 5 Гц — 0.4 секунды. Отсюда четыре дефекта разом:

1. **Короткий проход по кабинету становился работой.** Два кадра с креслом и
   движением при активной записи в CRM давали `CLINICAL_WORK`.
2. **Короткий провал детектора резал одну процедуру на несколько.** При отказе
   захвата состоянию никто ничего не сообщал; после восстановления кандидат
   собирался заново.
3. **`TURNOVER` выдавался по одному шумному кадру.** Достаточно было «CRM
   есть, записи нет, кресло не в базовой линии». Пустая комната «убиралась
   после пациента», которого не было.
4. **Невозможных переходов не существовало как понятия.** `EMPTY →
   CLINICAL_WORK` происходил за одно окно.

**Грозило владельцу**: завышенная загрузка кабинета и раздробленные
процедуры. Обе цифры правдоподобны, обе неверны, сверить их не с чем.

**Тесты**: `test_classifier_temporal.py`, 17/17 падали.
**Правка**: `TemporalStateMachine` —
выдержка по стенным часам (одинаково на любой частоте съёмки), своя выдержка
для дорогих утверждений (`CLINICAL_WORK` 30 с, `TURNOVER` 15 с),
`feed_dropout` с окном терпимости (внутри — держим состояние и копящегося
кандидата, снаружи — честный `UNKNOWN`), `TURNOVER` требует недавней реальной
занятости, `ALLOWED_TRANSITIONS` — полная таблица без петель. Незаконный
скачок не проглатывается и не разрешается: машина делает **один законный шаг**
по кратчайшему пути, поэтому реально заполнившийся кабинет доходит до
`CLINICAL_WORK`, но окном позже.

### 8. Строка `"false"` из CRM превращалась в активный приём

**Обещано** (`CRM_INTEGRATION.md`): пример ответа с булевыми полями,
«Unknown fields are ignored; missing fields fall back to their defaults».

**Было**: `bool(payload.get("appointment_active", False))`. В Python
`bool("false") is True`. CRM, отдающая булевы значения строками (обычное дело
для PHP-бэкендов и обёрток над SOAP), превращала **каждую** пустую комнату в
активный приём. Пиксели были ни при чём: любой шум в кресле становился
`CLINICAL_WORK` «с подтверждением CRM».

**Грозило владельцу**: отчёт, где загрузка кабинета взята не из того, что
было, а из формата ответа чужой системы. И ошибка неотличима на глаз.

**Тест**: `test_crm_merge.py::test_a_string_false_is_not_accepted_as_true`.

Там же, тем же корнем:

* **ни одного повтора** — один сетевой сбой терял контекст приёма;
* **устаревших данных не существовало как понятия** — расписание часовой
  давности считалось текущим;
* **пересечение записей игнорировалось** — при двух записях на один момент
  бралась та, что CRM положила первой; подтверждённое удаление зуба могло
  стать плановым осмотром.

**Правка**: `CrmSchemaError` (не повторяется — кривой ответ будет кривым и на
пятый раз), ограниченный повтор с растущей и ограниченной задержкой, поле
`as_of` со `stale` и жёстким пределом, разбор массива `appointments` по
приоритету «подтверждённая > запланированная > активная > прочее» с флагами
`overlapping` и `candidates`.

**Приоритет метки процедуры** зафиксирован явным `PROVENANCE_PRIORITY`:
`crm_confirmed > crm_planned > model_inferred > unknown`. Слот
`model_inferred` объявлен заранее и проверен тестами:
`test_a_model_guess_never_outranks_the_crm` и
`test_a_model_guess_is_worthless_without_a_crm_answer`. Модели в сборке нет —
слот существует, чтобы порядок был написан сегодня, а не придуман задним
числом, когда модель появится.

**Личность**: у `CrmContext` нет и не может быть поля пациента (проверяется
тестом по списку полей). Личность сотрудника берётся только из ответа CRM —
те же пиксели при двух разных ответах дают двух разных врачей
(`test_staff_identity_comes_only_from_the_crm_never_from_pixels`); без CRM
личности нет вообще и `CLINICAL_WORK` не заявляется.

### 9. Сутки резались по полуночи UTC

**Было**: `today_bounds` брал `datetime.now(timezone.utc)` и обрезал до
полуночи UTC. Часового пояса не существовало ни в конфигурации, ни в API.

**Грозило владельцу**: в клинике UTC+2 два вечерних часа каждый день уезжают
в чужие сутки. Отчёт за понедельник содержит кусок воскресенья.

Там же: `utilisation = clinical / window`, где `window` — полные календарные
24 часа. В полдень цифра занижена впятеро и не сравнима между днями.
И `add_observation` не имел ключа уникальности: повторная запись на тот же
момент (реплей после перезапуска воркера) создавала второй ряд.

**Тесты**: `test_metrics_daily.py`, 10/11 падали.
**Правка**: `AWV_TIMEZONE` (неизвестная зона — `ConfigError`, а не тихий
откат на UTC), naive и aware метки больше не сравниваются строками,
`monitored_seconds` и `unavailable_seconds`, два **явно названных**
знаменателя `utilisation_of_monitored` и `utilisation_of_window`,
дедупликация по `(room_id, ts)`.

### 10. Флаг записи принимался впустую

**Обещано** (`PRIVACY.md`): «`AWV_RECORDING_ENABLED` | `false` | nothing
records video; the app has no recorder» — в таблице «Off by default
(switchable, **and the switch works**)».

**Было**: переключателя нет. Рекордера в сборке нет и не было, но
`AWV_RECORDING_ENABLED=true` проходил валидацию и уезжал в
`/api/v1/capabilities` как `recording_enabled: true`.

**Грозило владельцу**: он читает API и видит «клиника пишет видео» там, где
не пишется ничего. Обратная ошибка не менее вредна: тот, кто включил флаг
ради записи, уверен, что она идёт.

**Тест**: `test_privacy_stage2.py::test_requesting_recording_fails_instead_of_being_ignored`.
**Правка**: падает с `privacy_denied` наравне с `face.identify` /
`patient.identify` / `audio.capture`; проверено и запуском процесса — он
завершается ненулевым кодом.

### 11. Отключение звука держалось только на том, что мы его не просили

**Обещано** (`app.manifest.yaml`): `audio.capture: deny`, «Each one is backed
by code that enforces it».

**Было**: в строке запуска ffmpeg не было `-an`. Источник с аудиодорожкой
декодировался вместе с ней. Запрет имел опору в намерении, не в коде.

**Тест**: `test_privacy_stage2.py::test_ffmpeg_invocations_never_request_an_audio_stream`
плюс `test_no_audio_reaches_a_real_capture` на реальном файле с дорожкой AAC.
**Правка**: `-an` в каждом вызове.

### 12. Про ONVIF не было машинно-читаемого утверждения

Документы были осторожны («if the exact firmware exposes a usable
subscription»), но ни `health`, ни `capabilities` не говорили, реализована ли
подписка и проверялась ли она. Формулировка в прозе — то, что тихо
превращается в «поддерживается».

**Тесты**: 2 из 9 в `test_motion_ingress.py` падали.
**Правка**: `capabilities.motion.onvif_subscription` = `{implemented: false,
verified_on_tapo_c200: false, evidence: "NOT RUN", blocked_by: "hardware"}`;
grep-тест по README и `docs/`, запрещающий вернуть утверждение
«ONVIF поддерживается/проверен» в прозу.

---

## Пункт за пунктом: что именно проверено

### 1. Постоянный рантайм — `LOCAL PASS`

| Требование | Доказательство |
|---|---|
| настоящий долгоживущий цикл | `test_runtime_loop_runs_and_stops_cleanly`; живой сервис: 18 циклов за ~9 с |
| чистый старт и остановка | там же + `test_stop_is_idempotent`, `test_starting_twice_does_not_create_a_second_loop`; после `stop()` не остаётся ни одной задачи asyncio |
| ограниченный откат при переподключении | `test_reconnect_backoff_is_bounded_and_capped`: задержки растут и упираются в `retry.max_delay`, никогда его не превышая |
| восстановление после пропажи камеры | `test_runtime_recovers_after_the_camera_disappears`: RECOVERING → RUNNING, `recoveries >= 1` |
| восстановление после обрыва сети | `test_runtime_recovers_after_real_network_loss`: **реальный ffmpeg**, отказанный RTSP-порт → рабочий вход; `components.camera.state` идёт `offline → ok` |
| никакого busy loop | `test_runtime_never_busy_loops`, `test_missing_baseline_does_not_spin_the_loop`: каждая задержка ≥ 0.05 с на всех путях |
| различение состояний здоровья | `test_health_components.py` (10): `healthy / degraded / camera_offline / crm_unavailable / detector_unavailable` + приоритет |

Живой прогон, SIGTERM: `runtime loop stopped after 18 cycles` → `service
stopped` → `Application shutdown complete`, процесс исчез, потомков ffmpeg не
осталось, `baseline.npy` и `vision.sqlite3` — `0600` в каталоге `0700`.

RSS живого сервиса после базовой линии и 17 наблюдений: **68.99 МБ**,
дочерних процессов: **0**.

### 2. Видеовход — `LOCAL PASS`

| Требование | Доказательство |
|---|---|
| канареечный секрет не утекает | `test_named_canary_never_escapes_any_channel`: `BOSSMAN_CANARY_SECRET_91f03f_DO_NOT_LEAK` прогнан через реальное падающее RTSP-соединение и не найден ни в логе, ни в семи ответах API, ни в двух упавших заданиях, ни в `health` / `capabilities` / `metrics`, ни в `repr(settings)`, ни в файлах состояния, ни в SQLite |
| его нет в исключениях | `test_canary_never_reaches_an_exception_or_its_traceback`: `str`, `repr`, `args`, полный traceback |
| полный `rtsp://user:pass@host` не логируется никогда | `test_a_full_credentialed_url_is_never_logged`: каждая строка лога с `rtsp://` и `@` обязана содержать `***:***@` или `<stream-url>` |
| ограниченный жизненный цикл ffmpeg, без зомби | `test_timed_out_ffmpeg_leaves_no_process_behind`, `test_repeated_timeouts_do_not_accumulate_children` (psutil, статус `STATUS_ZOMBIE` проверяется явно), `test_capture_timeout_kills_the_child` |
| битый кадр | `test_short_frame_is_rejected_not_padded`: короткий кадр отвергается, а не дополняется чёрным |
| устаревший кадр | `test_a_stale_frame_is_not_analysed_as_current`, `test_stale_frames_do_not_stop_the_persistent_runtime` |
| потеря кадров | `test_queue_bounded.py` (6): drop-oldest с бюджетом по числу и по байтам, 10 000 кадров через очередь на 8 |

Из десяти проверок этого пункта **три падали** (устаревший кадр) — остальные
семь проходили до правки и отмечены как подтверждённые, а не исправленные.

Остаточный риск, объявленный, а не скрытый: URL передаётся ffmpeg аргументом
и виден в `/proc/<pid>/cmdline`. Задокументировано в `PRIVACY.md`, есть тест,
что документ об этом говорит.

### 3. ONVIF — `NOT RUN`

Подписка на события Tapo C200 **не реализована** и **не проверялась ни на
одной прошивке**. Никакое утверждение об обратном в коде и в документах не
допускается — за этим следит grep-тест.

Что действительно проверено (и это не заменяет метку пункта):
вендор-нейтральный `/hooks/motion` принимает любую метку источника и пустую,
открывает окно частой съёмки, требует токен когда он задан и **игнорирует
подсунутый в теле адрес камеры** — это сигнал пробуждения, а не транспорт
(`test_the_webhook_never_takes_a_camera_address_or_credential`).

Запасной путь по разности кадров тоже не реализован и объявлен таковым.

### 4. Временной классификатор — `LOCAL PASS`

Состояния: `EMPTY`, `TRANSIT`, `STAFF_NONCLINICAL`, `PREP`, `CLINICAL_WORK`,
`TURNOVER`, `IDLE_OCCUPIED` (+ `UNKNOWN` как честное «не знаю»).

| Атака | Тест |
|---|---|
| короткий проход не должен стать работой | `test_a_short_walk_through_the_room_never_becomes_clinical_work` |
| настоящая процедура всё же распознаётся | `test_a_real_procedure_is_recognised_once_it_lasts` |
| выдержка по часам, а не по числу кадров | `test_minimum_dwell_is_wall_clock_not_a_sample_count` (20 кадров за 2 с не проходят) |
| короткий провал детектора не режет процедуру | `test_a_short_detector_dropout_does_not_split_one_procedure` (`transitions` не меняется) |
| длинный провал не выдаётся за состояние | `test_a_long_dropout_becomes_unknown_rather_than_a_pretended_state` |
| провал не сбрасывает копящегося кандидата | `test_a_dropout_does_not_reset_a_pending_candidate` |
| TURNOVER требует временного свидетельства | `test_turnover_needs_temporal_evidence_not_one_noisy_frame` |
| …и оно протухает | `test_turnover_expires_when_the_occupancy_is_ancient` |
| таблица переходов полна и без петель | `test_the_transition_table_is_total_and_has_no_self_loops` |
| EMPTY не прыгает в CLINICAL_WORK | `test_empty_never_jumps_straight_into_clinical_work` |
| каждый зафиксированный переход законен | `test_every_committed_transition_is_legal` |
| подавление дребезга | `test_alternating_evidence_never_commits_anything` (100 чередований → 0 переходов) |
| один шумный кадр внутри процедуры | `test_a_single_noisy_frame_inside_a_procedure_changes_nothing` |

Машина показывает наружу, что она удерживает: `metrics.temporal` содержит
`pending`, `pending_seconds`, `pending_samples`, `rejected_transitions`,
`dropouts` и действующую политику.

### 5. Слияние с CRM — `MOCK CRM PASS`

| Требование | Доказательство |
|---|---|
| таймаут | `test_the_timeout_is_the_configured_one` |
| повтор с откатом | `test_a_transport_failure_is_retried_with_backoff` (задержки `[0.1, 0.2]`), `test_the_retry_budget_is_bounded` |
| проверка схемы | `test_a_string_false_is_not_accepted_as_true`, `test_a_non_object_payload_is_rejected`, `test_wrong_types_are_rejected_not_coerced`, `test_unknown_fields_are_ignored_without_failing`, `test_a_schema_error_is_never_retried` |
| устаревшие данные | `test_a_dated_answer_is_marked_stale_not_treated_as_current`, `test_an_answer_older_than_the_hard_limit_is_not_available`, `test_a_stale_context_is_visible_in_the_classification` |
| пересечение записей | `test_overlapping_appointments_are_resolved_by_priority_not_by_order`, `test_a_single_appointment_is_not_reported_as_overlapping` |
| приоритет: подтверждённая > запланированная > модель > неизвестно | `test_procedure_priority_is_explicit_and_ordered`, `test_confirmed_beats_planned_beats_model_beats_unknown`, `test_a_model_guess_never_outranks_the_crm` |
| личность пациента не выводится из пикселей | `test_the_context_has_no_place_to_put_a_patient_identity` |
| личность сотрудника — из CRM, не по лицу | `test_staff_identity_comes_only_from_the_crm_never_from_pixels`, `test_no_crm_means_no_identity_at_all` |
| токен CRM не попадает в ошибку | `test_the_crm_token_never_appears_in_an_error` |

**Метка `MOCK CRM PASS`, не `REAL CRM PASS`.** Всё выше прогнано против
скриптованного HTTP-клиента. Реальная клиническая CRM в этой среде не
существует; ни один из этих тестов не говорит, что чужая система отвечает так,
как описано в `docs/CRM_INTEGRATION.md`. То, что мок прошёл, метку не меняет.

### 6. Метрики — `LOCAL PASS`

| Требование | Доказательство |
|---|---|
| граница суток | `test_day_boundary_follows_the_configured_timezone` (23:30 местного попадает во вчера), `test_day_boundary_defaults_to_utc` |
| часовой пояс | там же + `test_unknown_timezone_is_a_config_error`, `test_naive_timestamps_are_never_mixed_with_aware_ones` |
| перезапуск воркера | `test_a_worker_restart_does_not_double_count` |
| пропущенные кадры | `test_missed_frames_do_not_inflate_the_previous_state` |
| дублирующиеся наблюдения | `test_duplicate_observations_are_not_counted_twice` |
| окна недоступности | `test_a_restart_gap_is_an_unavailability_window_not_occupancy` (`unavailable_seconds = 580`) |
| взвешенная по времени агрегация | `test_monitored_seconds_and_both_utilisation_denominators`, `test_storage.py::test_metrics_sum_time_by_state_and_skip_gaps` |
| никакого двойного счёта | дедупликация по моменту + `test_out_of_order_timestamps_never_produce_negative_time` |

Живой прогон, `AWV_TIMEZONE=Europe/Prague`:
окно `2026-08-29T00:00:00+02:00 … 2026-08-30T00:00:00+02:00`,
`samples: 17`, `counted_intervals: 16`, `monitored_seconds: 8.35`,
`skipped_gaps: 0`, `unavailable_seconds: 0.0`.

### 7. Приватность — `LOCAL PASS`

| Требование | Доказательство |
|---|---|
| дефолты закрыты | `test_defaults_are_closed_end_to_end`, `test_recording_and_snapshots_are_off_by_default` |
| нет распознавания лиц | `test_no_face_or_audio_machinery_exists_in_the_source_tree` — grep по дереву: нет `face_recognition`, `dlib`, `insightface`, `CascadeClassifier` |
| нет звука | `test_ffmpeg_invocations_never_request_an_audio_stream` (`-an` в каждом вызове), `test_no_audio_reaches_a_real_capture` (реальный файл с дорожкой AAC) |
| нет постоянного хранения сырого видео | `test_no_snapshot_files_are_written_during_a_normal_sample`; рекордера в сборке нет, и теперь его нельзя даже попросить |
| нет идентификации пациента по изображению | `test_the_context_has_no_place_to_put_a_patient_identity` |
| запрошенные `face.identify` / `audio.capture` роняют запуск | `test_denied_capabilities_fail_startup_not_continue`, `test_a_denied_capability_makes_the_process_exit_nonzero`, `test_audio_capture_also_refuses_to_start` — **процесс** завершается ненулевым кодом с `{"code": "privacy_denied"}` в stderr, а не «тихо продолжает» |
| манифест соответствует коду | `test_the_manifest_permissions_match_the_code` |
| нет исходящего трафика по умолчанию | `test_default_run_opens_no_outbound_socket` |

---

## Что осталось незакрытым и почему

Честный список. Ничего из этого не помечено PASS.

1. **Физическая камера Tapo C200** — `NOT RUN`, нет оборудования. Код и
   тестовая обвязка полные: транспорт целиком прогоняется на настоящем
   ffmpeg против сгенерированных фикстур и отказанного RTSP-порта, но это не
   камера.
2. **Подписка на события ONVIF** — `NOT RUN`, клиента нет в сборке. Запасной
   путь `/hooks/motion` вендор-нейтрален и проверен.
3. **Реальная клиническая CRM** — `NOT RUN`, эндпоинта нет.
4. **PTZ и инвалидация базовой линии после поворота камеры** — `NOT RUN`.
5. **Калибровка порогов (`room`/`chair`/`work`) в настоящем кабинете** —
   `NOT RUN`. Значения по умолчанию — догадки до калибровки на месте. То же
   касается выдержек временной политики: 30 с для `CLINICAL_WORK` выбраны
   как разумные, а не измерены.
6. **Эпизоды процедур** — не реализованы. Наблюдения остаются точечными
   замерами; сборка эпизодов, сравнение запланированной и наблюдённой
   длительности, эффективность по врачу — этого нет.
7. **Политика хранения** — таблица `observations` растёт без прополки.
8. **Состояние в одном процессе** — ворота движения, временная машина и
   очередь живут в памяти. Несколько воркеров uvicorn разделят их. Запускать
   один воркер.
9. **Нет аутентификации по умолчанию** — безопасно только потому, что
   привязка по умолчанию `127.0.0.1`. Установка `AWV_HOST` в маршрутизируемый
   адрес без `AWV_API_TOKEN` приложением не блокируется.
10. **Скрубер литералов не трогает значения короче 4 символов** — пароль
    короче маскируется только в форме URL.
11. **URL виден в `/proc/<pid>/cmdline`** — свойство передачи аргумента
    ffmpeg. Смягчение: отдельный непривилегированный пользователь.
12. **Windows** — `NOT RUN`, платформы нет.
