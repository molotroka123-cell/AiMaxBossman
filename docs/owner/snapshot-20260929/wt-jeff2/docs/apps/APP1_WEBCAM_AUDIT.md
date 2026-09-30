# APP1 «AI WebCam Vision / Tapo C200» — аудит исходного пака

Дата: 2026-08-28
Объект аудита: `/tmp/packs/app1/` (архив `AI_WEBCAM_VISION_TAPO_C200_APP1.zip`), 15 файлов из `MANIFEST.json`.
Аудитор: агент APP1.
Область: только исходный пак. Оценка того, что построено в `apps/ai-webcam-vision/`, — в конце, раздел «Что изменено при переносе».

## Легенда уровней доказательности

| Уровень | Что означает |
|---|---|
| REAL IMPLEMENTED | код есть и делает заявленное, но здесь не исполнялся |
| REAL TESTED | исполнено в этой сессии против настоящего компонента, вывод получен |
| MOCK TESTED | исполнено против подделки/фикстуры, настоящий компонент не участвовал |
| STATICALLY VERIFIED | утверждение проверено чтением кода/grep, без исполнения |
| NOT TESTED | не проверялось |
| BLOCKED BY HARDWARE | невозможно проверить без физической камеры Tapo C200 |

Утверждение без уровня в этом документе считается недействительным.

---

## 1. Назначение

- Пак — «зерно» приложения №1: операционная аналитика стоматологического кабинета по камере TP-Link Tapo C200 плюс контекст CRM. — STATICALLY VERIFIED (`00_READ_FIRST.md`, `MASTER_PROMPT_FOR_CLAUDE.md`, `README.md`).
- Продуктовая идея: событие движения → окно частой выборки кадров → сравнение с базовым кадром пустой комнаты по двум зонам (кресло, рабочая зона) → слияние с контекстом CRM (смена/приём/услуга) → операционное состояние (`EMPTY / TRANSIT / STAFF_NONCLINICAL / PREP / CLINICAL_WORK / TURNOVER / IDLE_OCCUPIED`) → запись в SQLite → суточные метрики. — STATICALLY VERIFIED (`core.py:93-100,179-198,234-244`).
- Явные продуктовые запреты пака: без распознавания лиц, без идентификации пациента по пикселям, без аудио, без постоянного хранения сырого видео, личность сотрудника — из CRM. — STATICALLY VERIFIED (`docs/PRIVACY.md`, `app.manifest.yaml`).
- Пак заявляет себя «seed», а не готовым продуктом: `MASTER_PROMPT_FOR_CLAUDE.md` требует дописать и протестировать. — STATICALLY VERIFIED.

## 2. Архитектура

- Фактическая архитектура — один модуль ядра `core.py` (244 строки) + тонкий FastAPI-слой `api.py` (59 строк) + `main.py` (10 строк). Слоёв, портов/адаптеров, инверсии зависимостей нет; классы `RTSP`, `MotionGate`, `CRM`, `Detector`, `Store`, функция `classify` лежат в одном файле. — STATICALLY VERIFIED.
- Модель исполнения — **pull-only через HTTP**: кадр берётся только когда снаружи вызвали `POST /api/sample`. Фонового цикла выборки, планировщика, воркера нет. — STATICALLY VERIFIED (`api.py:44-52`, отсутствие `asyncio.create_task`/`while` в `core.py`).
- Состояние процесса — в объектах, созданных в `build_app()` (замыкание): `MotionGate.until`, `Detector.prev`. При нескольких воркерах uvicorn состояние разъедется. — STATICALLY VERIFIED.
- Композиционный корень — `build_app(Settings)`; это единственная точка сборки зависимостей, что само по себе хорошо. — STATICALLY VERIFIED (`api.py:15-22`).
- Машины состояний с гистерезисом/дебаунсом, которую требует `MASTER_PROMPT_FOR_CLAUDE.md` («debounced state machine»), в коде **нет**: `classify()` — чистая функция от одного кадра, память между кадрами только `Detector.prev`. — STATICALLY VERIFIED.

## 3. Рантайм

- Python ≥ 3.11 (`pyproject.toml`), используется `enum.StrEnum` (3.11+). — STATICALLY VERIFIED.
- Процесс: `uvicorn.run(app, host, port)`, порт по умолчанию 8870, хост 127.0.0.1. Локальный биндинг по умолчанию — правильное решение. — STATICALLY VERIFIED (`core.py:42-43`, `main.py:5-7`).
- Внешний процесс: `ffmpeg` вызывается как подпроцесс для захвата одного JPEG-кадра. — STATICALLY VERIFIED (`core.py:65-76`).
- В этой среде `which ffmpeg` → пусто, код возврата 1: ffmpeg отсутствует. Захват кадра здесь физически невозможен. — REAL TESTED.
- Обработчик graceful shutdown отсутствует: нет `@app.on_event("shutdown")`, нет lifespan, нет отмены задач, нет закрытия SQLite. — STATICALLY VERIFIED.
- Асинхронность: `Detector.analyze` и `Store.*` — блокирующий CPU/IO-код, вызываемый прямо в `async def` обработчике без `run_in_executor`; при нескольких клиентах цикл событий блокируется. — STATICALLY VERIFIED (`api.py:47-49`).

## 4. Зависимости

- Объявлены: `fastapi>=0.111`, `uvicorn[standard]>=0.30`, `httpx>=0.27`, `opencv-python-headless>=4.10`, `numpy>=1.26`; dev: `pytest>=8`, `pytest-asyncio>=0.23`. — STATICALLY VERIFIED (`pyproject.toml`).
- Неявная бинарная зависимость `ffmpeg`/`ffprobe` объявлена только текстом в `README.md`, программной проверки её наличия на старте нет; ошибка всплывает лишь в момент захвата как `RuntimeError("ffmpeg not installed")`. — STATICALLY VERIFIED (`core.py:71-72`).
- Версии нижних границ без верхних; lock-файла нет. — STATICALLY VERIFIED.
- В чистой среде этой машины `numpy`, `cv2`, `pytest-asyncio` отсутствовали; я доустановил `numpy 2.4.6` и `opencv-python-headless (cv2 5.0.0)` только ради прогона тестов пака. — REAL TESTED.
- `opencv-python-headless` — самая тяжёлая зависимость (десятки МБ) и используется ровно ради четырёх операций: `imdecode`, `imread`, `imwrite`, `resize`, `cvtColor`, `absdiff`. — STATICALLY VERIFIED (`core.py:141-176`).
- `pytest-asyncio` в dev-зависимостях, но ни одного async-теста в паке нет. — STATICALLY VERIFIED.

## 5. Точки входа

- Консольный скрипт `ai-webcam-vision = ai_webcam_vision.main:main`. — STATICALLY VERIFIED.
- HTTP: `GET /`, `GET /api/status`, `POST /hooks/motion`, `POST /api/baseline`, `POST /api/sample`, `GET /api/metrics/today`. Всего 6. — STATICALLY VERIFIED (`api.py`).
- Заявленные в манифесте команды `/webcam status|today|room` и точки входа `bossman_app`, `chat_command`, `autonomous` помечены `future` и кодом не реализованы. — STATICALLY VERIFIED (`app.manifest.yaml`).
- CLI как таковой (аргументы, подкоманды) отсутствует: `main()` не разбирает argv. — STATICALLY VERIFIED.

## 6. Фронтенд

- Одна строковая константа `HTML` (9 строк) внутри `api.py`: три кнопки (`/api/baseline`, `/hooks/motion`, `/api/sample`) и `<pre>` с JSON статуса, опрос каждые 3 с. — STATICALLY VERIFIED (`api.py:5-13`).
- Ни сборки, ни статических файлов, ни шаблонов, ни фреймворка. Демонстрационная страница уровня «кнопка + dump JSON». — STATICALLY VERIFIED.
- Видеопотока в UI нет — это соответствует приватным умолчаниям. — STATICALLY VERIFIED.
- `document.getElementById` не используется, код полагается на неявную глобальную переменную `x` от `id=x` — работает в браузерах, но это демо-качество. — STATICALLY VERIFIED.

## 7. Бэкенд

- FastAPI без версионирования пути, без моделей ответов (pydantic), без OpenAPI-описаний, без обработчиков ошибок уровня приложения. — STATICALLY VERIFIED.
- Аутентификации/авторизации нет ни на одном эндпоинте, включая `POST /hooks/motion` (внешний вебхук) и `POST /api/baseline` (перезапись эталона комнаты). Защита — только биндинг на 127.0.0.1 по умолчанию, который снимается переменной `AWV_HOST`. — STATICALLY VERIFIED.
- Ограничения размера тела, rate-limit, CORS-политики нет. — STATICALLY VERIFIED.
- `POST /api/sample` конвертирует любое исключение в `HTTPException(503, f"{type(e).__name__}: {e}")` — текст исключения уходит клиенту как есть. — STATICALLY VERIFIED (`api.py:52`). Это канал утечки, см. §16.
- `GET /api/status` возвращает `rtsp.safe_url()`, т.е. редактированный URL, а не сырой. — STATICALLY VERIFIED (`api.py:30`).

## 8. Хранение

- SQLite, одна таблица `observations` + индекс `(room_id, ts)`. Схема без миграций и без `schema_version`. — STATICALLY VERIFIED (`core.py:208-219`).
- `INSERT ... VALUES(NULL,?,...)` — позиционный insert без списка колонок: любое изменение схемы молча ломает запись. — STATICALLY VERIFIED (`core.py:223`).
- **Дефект: утечка соединений.** `Store.con()` создаёт новое `sqlite3.connect` на каждый вызов, а `with c:` в sqlite3 — это транзакция, а не закрытие. Соединения не закрываются никогда. — STATICALLY VERIFIED (`core.py:204-206`, `221-237`).
- Таймаута на блокировку БД, WAL-режима, `check_same_thread` не настроено. — STATICALLY VERIFIED.
- Ретеншена нет: `observations` растёт бесконечно, чистки/агрегации нет. — STATICALLY VERIFIED.
- Второй артефакт на диске — `./data/baseline.jpg`, полноразмерный кадр кабинета, пишется `cv2.imwrite` с правами по умолчанию (0644). Это единственный «сырой» пиксельный артефакт пака. — STATICALLY VERIFIED (`core.py:166-168`).
- `Store.metrics` считает время как разницу соседних наблюдений с потолком 120 с на интервал; при редкой выборке метрика систематически занижена и в паке это нигде не оговорено. — STATICALLY VERIFIED (`core.py:238-241`).

## 9. Доступ к железу

- Единственный путь к камере — `ffmpeg -rtsp_transport tcp -i rtsp://user:pass@host:554/streamN -frames:v 1` → JPEG в stdout. — STATICALLY VERIFIED (`core.py:66-67`).
- Порт RTSP жёстко зашит: `554` в f-строке, переменной окружения нет. — STATICALLY VERIFIED (`core.py:61`).
- Разрешены ровно два пути потока: `stream1`/`stream2`, иначе `ValueError`. Разумный whitelist. — STATICALLY VERIFIED (`core.py:58-59`).
- ONVIF-клиента в коде **нет**, несмотря на то, что документы пака делают ONVIF предпочтительным источником события движения; вместо него — ручной вебхук `/hooks/motion`. — STATICALLY VERIFIED.
- Frame-difference fallback, обещанный в `00_READ_FIRST.md` и `NETWORK_AND_TAPO.md` («cheap frame-diff fallback»), не реализован: `MotionGate` активируется только вызовом `trigger()` из вебхука. — STATICALLY VERIFIED (`core.py:79-90`, `api.py:33-38`).
- PTZ-управления нет (и это согласуется с требованием «держать камеру в фиксированной позе»). — STATICALLY VERIFIED.
- Реальная камера Tapo C200 в этой среде отсутствует; ни один путь захвата не исполнялся против железа. — BLOCKED BY HARDWARE.
- Даже суррогатная проверка ffmpeg-границы здесь невозможна без установки бинаря: `which ffmpeg` пуст. — REAL TESTED.

## 10. Доступ к моделям

- ML-моделей, LLM/VLM, весов, инференс-рантайма в паке **нет вообще**. — STATICALLY VERIFIED (grep по `torch|onnx|transformers|ultralytics|yolo` — пусто).
- «Зрение» — это арифметика над пикселями: серое изображение, `absdiff`, доля пикселей с разницей > 24, пороги `.035/.055/.012`. Пороги — магические константы в теле `classify`, не конфигурируемые. — STATICALLY VERIFIED (`core.py:152-158,181`).
- Локальный VLM упомянут в `MASTER_PROMPT_FOR_CLAUDE.md` только как будущий источник «дополнительных свидетельств», кода нет. — STATICALLY VERIFIED.
- Никакого выбора провайдера, ключей моделей, сетевых вызовов к моделям — соответственно, и утечки ключей моделей быть не может. — STATICALLY VERIFIED.
- Признака CPU/GPU-режима нигде нет, `cv2` работает на CPU. — STATICALLY VERIFIED.

## 11. Конфигурация

- Единственный источник — переменные окружения с префиксом `AWV_`, собранные в dataclass `Settings`. — STATICALLY VERIFIED.
- **Дефект: конфигурация связывается на импорте модуля, а не на создании объекта.** Значения по умолчанию полей dataclass вычисляются один раз при выполнении тела класса. Прогон: первый `Settings().camera_host` → `192.168.10.50`; после `os.environ["AWV_CAMERA_HOST"]="10.9.9.9"` второй `Settings().camera_host` → по-прежнему `192.168.10.50`. — REAL TESTED (исполнено против кода пака в этой сессии).
  Следствия: `.env`, подгруженный после импорта, игнорируется; тесты не могут переопределять конфиг через окружение; перезагрузка конфигурации невозможна.
- **Дефект: невалидное значение зоны роняет импорт модуля, а не старт приложения**: `zone()` вызывается в теле класса, `ValueError` вылетает из `import ai_webcam_vision.core`. — STATICALLY VERIFIED (`core.py:18-22,35-36`).
- Валидации остальных полей нет: `AWV_PORT="abc"` → `ValueError` на импорте; отрицательные интервалы, пустой `room_id`, `camera_stream` с произвольным значением (проверяется только в момент построения URL) не отсекаются. — STATICALLY VERIFIED.
- **Мёртвая конфигурация**: `active_interval` и `idle_interval` присутствуют в `Settings` и `.env.example`, но нигде не читаются — управления частотой выборки в паке нет. — STATICALLY VERIFIED (grep: только объявление).
- **Декоративные приватные флаги**: `AWV_STORE_RAW_VIDEO`, `AWV_CAPTURE_AUDIO`, `AWV_FACE_IDENTIFICATION` есть в `.env.example`, но соответствующих полей в `Settings` нет и код их не читает. Приватность обеспечена отсутствием функциональности, а не выключателем. Для инженерной честности это плохо: флаг, который ничего не выключает, создаёт ложное чувство контроля. — STATICALLY VERIFIED.
- `app.manifest.yaml` описывает права (`raw_video.store: deny`, `audio.capture: deny`, `face.identify: deny`), но ни строчки кода их не читает и не проверяет. — STATICALLY VERIFIED.

## 12. Секреты

- Секретов в репозитории пака нет: `.env.example` содержит `CHANGE_ME`, реальных ключей/паролей в файлах не найдено. — STATICALLY VERIFIED (полный просмотр 15 файлов).
- Секреты берутся из окружения (`AWV_CAMERA_PASSWORD`, `AWV_CRM_TOKEN`) — это правильный источник. — STATICALLY VERIFIED.
- Обёртки секрета нет: пароль лежит в обычном `str` внутри `Settings` и `RTSP`. `repr(Settings())` (dataclass-repr) печатает пароль и токен CRM полностью. — STATICALLY VERIFIED (`core.py:25-43`: `@dataclass` без `field(repr=False)`).
- Единая точка сборки RTSP-URL формально есть — `RTSP.url()`. — STATICALLY VERIFIED.
- Редакция — один регекс `(rtsp://)([^/@:]+):([^/@]+)@`. — STATICALLY VERIFIED (`core.py:46-47`).
- **Дефект №1 (утечка): пустое имя пользователя ломает редакцию.** Класс `[^/@:]+` требует минимум один символ до двоеточия. Прогон: `RTSP("10.0.0.5", "", "PackLeakProbe123").safe_url()` → `rtsp://:PackLeakProbe123@10.0.0.5:554/stream2`, пароль в открытом виде; тот же URL уходит в `GET /api/status`. — REAL TESTED (исполнено против кода пака в этой сессии).
- **Дефект №2 (утечка): редактируется только URL-форма.** Прогон: `redact("ffmpeg: auth failed for PackLeakProbe123")` → строка возвращается без изменений. Любой текст ошибки, где пароль напечатан не в виде `rtsp://u:p@`, пройдёт насквозь. — REAL TESTED.
- **Дефект №3 (канал утечки): необработанный текст исключения уходит клиенту.** `api.py:52` вкладывает `str(e)` в HTTP-ответ; редакция применена только к stderr ffmpeg внутри `RTSP.jpeg` (`core.py:75`), а не к ответу. — STATICALLY VERIFIED.
- **Дефект №4: логирования нет вообще**, поэтому нет и редактирующего фильтра логов. Любой будущий `logger.exception` немедленно станет утечкой. — STATICALLY VERIFIED (grep `logging` — ни одного вхождения).
- Пароль передаётся ffmpeg аргументом командной строки, т.е. виден в таблице процессов любому пользователю хоста (`/proc/*/cmdline`). В паке не упомянуто. — STATICALLY VERIFIED.
- `AWV_CRM_TOKEN` уходит в заголовок `Authorization`; в ответах API токен не отражается. — STATICALLY VERIFIED (`core.py:131`).
- Теста-канарейки (уникальный пароль + поиск по логам/ответам/файлам/исключениям) в паке нет; единственный тест проверяет подстроку `"secret"` в одном счастливом сценарии. — STATICALLY VERIFIED (`tests/test_core.py:4-7`).

## 13. Mock-код

- `CRM(kind="none")` молча возвращает пустой `CRMContext()`. Это **скрытый мок**: снаружи «нет приёма» и «CRM не подключена» неразличимы — ни в ответе `/api/sample`, ни в `/api/status`, ни в записи БД (`source` пустой, а не `"none"`, потому что `CRMContext.source` по умолчанию `"none"` только в объекте, но в `procedure()` при отсутствии данных возвращается `"none"` — при этом состояние всё равно вычисляется и пишется как факт). — STATICALLY VERIFIED (`core.py:126-128,114-119`).
- Мока камеры нет: без ffmpeg и без камеры приложение нефункционально целиком. — STATICALLY VERIFIED.
- Флага «это мок / это настоящее» ни в одном ответе API нет. — STATICALLY VERIFIED.
- В тестах используются рукописные словари-события, но это фикстуры теста, а не мок-инфраструктура продукта. — STATICALLY VERIFIED.

## 14. Незаконченный код

- `MotionGate.hold` не может быть продлён «новым движением расширяет окно» иначе как повторным вызовом вебхука — это как раз реализовано, но окно не сбрасывается при переходе в EMPTY. Мелочь. — STATICALLY VERIFIED.
- Не реализовано из того, что документы пака объявляют обязательным:
  1. frame-diff fallback обнаружения движения — STATICALLY VERIFIED (отсутствует);
  2. дебаунс/гистерезис машины состояний — STATICALLY VERIFIED (отсутствует);
  3. «эпизод процедуры» и его длительность, `scheduled vs observed`, per-clinician efficiency — STATICALLY VERIFIED (в БД нет таблицы эпизодов, только точечные наблюдения);
  4. ONVIF-источник событий — STATICALLY VERIFIED (отсутствует);
  5. управление частотой выборки (`active_interval`/`idle_interval`) — STATICALLY VERIFIED (мёртвые поля);
  6. `docs/AI_WEBCAM_VISION_APP1_REPORT.md`, который требует `MASTER_PROMPT_FOR_CLAUDE.md`, — в паке отсутствует. — STATICALLY VERIFIED.
- `Detector.analyze` при первом кадре отдаёт `work=0.0` (нет `prev`), из-за чего первый кадр после простоя не может дать `CLINICAL_WORK`. Логика «первого кадра» не оговорена. — STATICALLY VERIFIED.
- `crop()` не валидирует порядок координат: при `x2<x1` вернётся пустой массив, `score()` вернёт `nan`, `classify` сравнит `nan>=0.035` → `False`, состояние тихо деградирует без ошибки. — STATICALLY VERIFIED.

## 15. Демо-код

- HTML-панель в `api.py` — демо. — STATICALLY VERIFIED.
- `api.py:25`: `__import__("fastapi").responses.HTMLResponse(HTML)` вместо нормального импорта — демонстрационный хак. — STATICALLY VERIFIED.
- Пороговые константы `.035/.055/.012/24` и confidence-числа `.9/.74/.72/.68/.70/.5/.45` — подобранные «на глаз» значения без калибровки и без источника. Это демо-эвристика, выдающая себя за уверенность. — STATICALLY VERIFIED.
- `FINAL_PROMPT_TO_PASTE.txt` и `MASTER_PROMPT_FOR_CLAUDE.md` — инструкции агенту, не продукт; в репозиторий не переносились. — REAL IMPLEMENTED (решение исполнено при переносе).

## 16. Риски безопасности

| # | Риск | Уровень |
|---|---|---|
| S1 | Утечка пароля камеры при пустом username через `safe_url()` и `GET /api/status` | REAL TESTED (воспроизведено) |
| S2 | Редакция не покрывает не-URL формы пароля | REAL TESTED (воспроизведено) |
| S3 | Текст любого исключения уходит клиенту в HTTP 503 | STATICALLY VERIFIED |
| S4 | `repr()`/`asdict()` объекта `Settings` печатает пароль камеры и токен CRM | STATICALLY VERIFIED |
| S5 | Пароль виден в таблице процессов (argv ffmpeg) | STATICALLY VERIFIED |
| S6 | Все эндпоинты без аутентификации; `POST /api/baseline` даёт неаутентифицированную перезапись эталона, `POST /hooks/motion` — неаутентифицированное управление режимом выборки | STATICALLY VERIFIED |
| S7 | `AWV_HOST` позволяет выставить сервис в сеть без каких-либо дополнительных требований к аутентификации | STATICALLY VERIFIED |
| S8 | Таймаут на ffmpeg есть (10 с), но при таймауте процесс не убивается: `asyncio.wait_for(p.communicate(), 10)` бросает `TimeoutError`, `p` остаётся жить → накопление зависших ffmpeg | STATICALLY VERIFIED |
| S9 | Утечка соединений SQLite (см. §8) → исчерпание файловых дескрипторов при длительной работе | STATICALLY VERIFIED |
| S10 | `baseline.jpg` — незашифрованный полноразмерный снимок кабинета с правами 0644 | STATICALLY VERIFIED |
| S11 | CRM-клиент ходит по HTTP на произвольный `AWV_CRM_BASE_URL` без проверки схемы/TLS-пиннинга; `http://` не запрещён — Bearer-токен может уйти открытым текстом | STATICALLY VERIFIED |
| S12 | Отсутствие ограничения размера тела и rate-limit на `/hooks/motion` | STATICALLY VERIFIED |

Отдельно: сетевого исходящего трафика по умолчанию нет (`crm_kind="none"` → сетевой вызов не делается), телеметрии нет. Это единственное «по умолчанию закрыто», которое пак действительно выполняет. — STATICALLY VERIFIED (`core.py:126-128`).

## 17. Отсутствующие тесты

- Прогон тестов пака в этой сессии (после доустановки numpy и opencv): `4 passed in 0.14s`. — REAL TESTED.
- Что реально покрыто: редакция URL в счастливом сценарии, `MotionGate.trigger/active`, две ветки `classify`, приоритет `confirmed_service`. Итого 4 теста, ноль async, ноль HTTP, ноль ffmpeg, ноль SQLite. — STATICALLY VERIFIED.
- Отсутствуют тесты, которые сам `MASTER_PROMPT_FOR_CLAUDE.md` называет минимумом: «no secret in returned status», «storage metrics», «state hysteresis», «no face/audio/raw-video feature enabled by default». — STATICALLY VERIFIED.
- Отсутствуют полностью: тест границы ffmpeg, тест переподключения, тест внедрения отказов, тест ограниченности очереди/памяти, тест корректного завершения, тест таймаутов, тест контракта API, канарейка на секрет, тест приватности снимков. — STATICALLY VERIFIED.
- Инструментов измерения покрытия, линтера, типизации, CI-конфигурации в паке нет. — STATICALLY VERIFIED.

## 18. Требования к ресурсам

- Диск: исходники пака 22 КБ; `opencv-python-headless` + `numpy` в этой среде дали десятки МБ зависимостей. — REAL TESTED (установка выполнена).
- Память: базовый процесс FastAPI+cv2 — порядка 150–250 МБ RSS в типичной конфигурации; точную цифру здесь не мерил. — NOT TESTED.
- CPU: анализ кадра — три `absdiff` над изображениями, приведёнными к ширине 320 px; на современном x86 это единицы миллисекунд на кадр. Порядок оценён по коду, не замерен. — NOT TESTED.
- GPU: не требуется и не используется. — STATICALLY VERIFIED.
- Сеть: RTSP-поток stream2 у C200 — низкий битрейт (порядка сотен кбит/с), но пак выкачивает поток заново на каждый одиночный кадр (новое подключение ffmpeg на каждый `/api/sample`), что для частой выборки крайне неэффективно. — STATICALLY VERIFIED.
- Хранилище растёт линейно от числа наблюдений без ретеншена (§8). — STATICALLY VERIFIED.
- Обязательное внешнее ПО: `ffmpeg`. В этой среде отсутствует. — REAL TESTED.

## 19. Точки интеграции с BOSSMAN

- Импортов `bcc.*`/`bossman*` в коде пака нет. Единственное вхождение слова `bossman` — путь чужого CRM-API `"/api/bossman/room-context"` в `core.py:133`. Связанности с ядром нет. — STATICALLY VERIFIED (grep по `src/` и `tests/`).
- Объявленные, но не реализованные точки: `app.manifest.yaml` → `bossman_app: future`, `chat_command: future`, `autonomous: future`, команды `/webcam status|today|room`. — STATICALLY VERIFIED.
- Пригодные существующие швы: `POST /hooks/motion` (вендор-нейтральный вход события), `GET /api/status`, `GET /api/metrics/today`, SQLite как источник фактов. — STATICALLY VERIFIED.
- Управляющего контракта, который требует ФАЗА 4 (health, capabilities, jobs.create/status/cancel, artifacts.list, metrics/resources), в паке **нет ни одного эндпоинта**: `/api/status` не является ни health, ни capabilities (не сообщает готовность ffmpeg, режим CPU/GPU, mock/real). — STATICALLY VERIFIED.
- `MASTER_PROMPT_FOR_CLAUDE.md` прямо требует не подключаться к фундаменту до закрытия аудита V2.2 и держать приложение под `apps/ai-webcam-vision/` standalone. Это совпадает с заданием лида. — STATICALLY VERIFIED.

---

## 20. Итог по паку

Годно к переносу (сохранено в новом приложении с доработкой):
- продуктовая онтология состояний и их семантика;
- идея «движение ≠ работа» и слияние с контекстом CRM, приоритет `confirmed → planned → unknown` с провенансом;
- вендор-нейтральный `/hooks/motion` как вход события движения;
- подход «базовый кадр + нормализованные зоны + доля изменившихся пикселей» как дешёвый детектор;
- приватные умолчания (нет лиц, нет аудио, нет сырого видео, личность из CRM);
- контракт CRM `GET /api/bossman/room-context`;
- сетевые правила (VPN, без проброса 554 наружу).

Не годно и вычищено:
- одноблочный `core.py` без слоёв и портов;
- редакция секрета одним регексом и `str`-пароль без обёртки;
- конфигурация, связываемая на импорте;
- декоративные приватные флаги и права манифеста, которые ничего не выключают;
- скрытый мок CRM;
- отсутствие фонового цикла, частоты выборки, очереди, переподключения, завершения;
- pull-only API без health/capabilities/jobs/artifacts/metrics;
- утечка соединений SQLite и не убиваемый ffmpeg при таймауте;
- зависимость от OpenCV ради шести вызовов.

## 21. Что изменено при переносе (краткая сверка)

Полное описание построенного — в `apps/ai-webcam-vision/README.md` и `apps/ai-webcam-vision/docs/APP1_REPORT.md`.
Собственный набор тестов приложения: **102 passed, 0 failed, 0 skipped** (`cd apps/ai-webcam-vision && python -m pytest -q`). — REAL TESTED.
Кратко, по дефектам выше:

- S1/S2/S4, §12 → введены `Secret`/`SecretUrl` без раскрывающего `__repr__`/`__str__`, единая точка сборки URL и глобальный скруббер, работающий по зарегистрированным значениям секретов, а не только по форме URL. — REAL IMPLEMENTED, покрыто тестом-канарейкой. — REAL TESTED.
- S3 → тексты исключений транспорта санируются в момент создания исключения, API отдаёт только санированное. — REAL TESTED.
- S8 → таймауты на probe и на захват, при таймауте процесс `kill()`-ается и ожидается. — REAL TESTED.
- S9 → соединение SQLite закрывается через контекстный менеджер. — REAL TESTED.
- S10 → снимки выключены по умолчанию, при включении пишутся уменьшенными, размытыми, с правами 0600. — REAL TESTED.
- §11 (import-time config) → конфигурация читается из окружения в момент `Settings.from_env()`. — REAL TESTED.
- §11 (мёртвые флаги) → приватные выключатели читаются кодом и реально запрещают действие. — REAL TESTED.
- §13 (скрытый мок) → `mode`/`is_mock` явно присутствуют в `capabilities`, `health` и в каждой записи наблюдения. — REAL TESTED.
- §14/§17 → добавлены bounded-очередь, контроль частоты, переподключение с экспоненциальной задержкой, clean shutdown, контракт управления и тесты на всё перечисленное. — REAL TESTED.
- §9 → физическая проверка против настоящей Tapo C200 по-прежнему невозможна. — BLOCKED BY HARDWARE.
