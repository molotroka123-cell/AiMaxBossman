# Epoch 8 — Архитектура и направления работ

Студия строится **поверх** существующих модулей и не заводит второго ядра,
второго реестра, второго менеджера сайдкаров и второй очереди. Ниже — что и
где меняется. Пути от корня репозитория; `bcc` = `command-center/bcc`.

## 0. Карта: что переиспользуется

| Существующий модуль | Роль в студии |
|---|---|
| `bcc/features/images.py`, `bcc/v2/images_tables.py`, `bcc/v2/images_runtime.py` | очередь прогонов, захват одной заявки, кооперативная отмена, хранилище файлов, коллекции — обобщаются с `image` на `image | video | audio` |
| `bcc/oss/comfyui.py` | локальный провайдер изображений (как есть) |
| `tools/hailuo_rescue.py`, `tools/seedance_shorts.py` | проверенные на практике вызовы OpenRouter `/api/v1/images`, `/api/v1/videos`, `/api/v1/auth/key` — переносятся в продукт как адаптер, скрипты становятся тонкими обёртками |
| `bcc/v2/model_router.py`, `bcc/v2/model_intelligence.py`, `bcc/v2/capability_probe.py` | выбор модели по задаче, «проба важнее каталога», `verified=None ≠ False` |
| `bcc/model_health.py`, `bcc/reality/recovery.py`, `bcc/v2/openrouter_ext.py` | словарь состояний и лестница восстановления — без изменений, применяются к генерации |
| `bcc/v2/openrouter_identity.py`, `bcc/secrets.py` | ключи: Vault ⟶ env, конфликты названы, в логах `…last4` |
| `bcc/provider_governance.py`, `bcc/v2/governor.py` | локальность, цена и бюджет на каждом вызове |
| `bcc/v2/mcp_hub.py`, `bcc/v2/mcp_runtime.py`, `tools/mcp_acceptance.py` | MCP-путь к Higgsfield — только через полосу |
| `bcc/run_provenance.py` | образец неизменяемого снимка происхождения — для результатов студии заводится своя запись той же дисциплины |
| `bcc/video_studio/*` (`commands.media.import`, `storyboard.py`, `read_verification.py`, `export_receipt.py`) | потребление результатов, план пакетной генерации, независимая проверка байтов |
| `bcc/features/web_designer.py` | потребление изображений |
| `tools/acceptance_registry.json`, `tools/require_acceptance_results.py`, `tools/astra6_freeze.py`, `tools/installed_ui_sweep.py` | приёмка на архиве и заморозка — расширяются сценариями студии |
| страница `command-center/ui/pages/images.js` | становится «Студией»; id `images` сохраняется, чтобы не плодить экраны и не ломать ссылки |

Новый код помещается в один пакет `bcc/studio/` (каталог, протокол
провайдера, адаптеры, галерея) и один файл данных `tools/studio_models.json`.
Новых зависимостей в `pyproject.toml` — ноль (httpx уже есть).

## W1. Каталог моделей генерации — источник истины

`tools/studio_models.json` (рядом с `acceptance_registry.json`, тот же стиль
загрузчика с самопроверкой) — одна запись на модель:

```json
{
  "id": "openrouter:bytedance/seedance-2.0-mini",
  "provider": "openrouter",
  "surface": "video",
  "label": "Seedance 2.0 Mini",
  "roles": {"start": 1, "end": 1, "reference": 4, "audio": 1},
  "settings": {
    "aspect_ratio": {"type": "enum", "values": ["16:9", "9:16", "1:1"], "default": "16:9"},
    "duration": {"type": "range", "min": 4, "max": 15, "default": 5, "unit": "s"},
    "resolution": {"type": "enum", "values": ["480p", "720p"], "default": "720p"},
    "generate_audio": {"type": "boolean", "default": true},
    "seed": {"type": "range", "min": 0, "max": 2147483647, "default": null}
  },
  "price": {"kind": "per_second", "usd": null, "source": "unknown"},
  "free": false,
  "deadline_seconds": 600,
  "license": "provider-terms",
  "answers": {"BUNDLED": false, "CONFIGURED": "OPENROUTER_API_KEY", "MODEL_REQUIRED": false,
              "EXTERNAL_SERVICE_REQUIRED": "openrouter.ai", "VERIFIED": false},
  "verified_capabilities": {}
}
```

Правила:
- `settings` — только `enum | range | boolean` с default; интерфейс рисует
  ровно их (идея open-higgsfield; у него же — роли медиа с лимитом).
- `price.usd = null` для облачной модели → маршрутизатор её
  **дисквалифицирует** для автоматического выбора (правило `model_router`),
  но владелец может выбрать вручную — с показом «цена неизвестна».
- `VERIFIED` в файле всегда `false`; проверенные возможности приходят из проб
  (`capability_probe`) и хранятся в БД как `verified_capabilities`, не в файле.
- Сверка каталога с провайдером — задача, а не фоновый демон: «Обновить
  каталог» в интерфейсе вызывает `models_explore`/`/models` и показывает
  разницу; ничего не включается автоматически.
- Тест-сторож: сумма записей, уникальность id, каждая запись валидна по
  схеме, у каждой облачной — `deadline_seconds` и `answers`.

## W2. Протокол провайдера и адаптеры

`bcc/studio/provider.py`:

```python
class GenerationProvider(Protocol):
    name: str
    async def submit(self, plane: GenerationPlane) -> Submitted      # request_id, cancel_ref
    async def status(self, request_id: str) -> ProviderStatus         # state + reason + outputs
    async def cancel(self, request_id: str) -> None
    async def fetch(self, output: ProviderOutput, dest: Path) -> Fetched  # bytes, mime, sha256
```

`ProviderStatus.state ∈ {queued, running, completed, failed, refused, canceled,
timeout}`; `reason` — из словаря `model_health` (`unauthorized`, `throttled`,
`provider_down`, `malformed`, `silent`) или `content_policy` для nsfw/refusal.
Шесть причин отказа, которые никогда не сливаются:

| Ситуация | state / reason | Действие лестницы (`recovery.py`) |
|---|---|---|
| ключа нет или отвергнут (401/403) | `failed / unauthorized` | остановка, `OWNER_REQUIRED`; ожидание не помогает |
| 429, квота | `failed / throttled` | ограниченная пауза; без скрытого повторного submit |
| нет кредита (402) | `failed / insufficient_credit` | `OWNER_REQUIRED`, без повтора; health-проекция `throttled` не стирает причину |
| модель/маршрут недоступны (404, 5xx) | `failed / provider_down` | другая модель того же класса, если владелец разрешил |
| отказ по контенту | `refused / content_policy` | без повтора; показать причину |
| ответ битый или пустой | `failed / malformed | silent` | одна повторная проба, затем отказ |

Адаптеры (`bcc/studio/providers/`):
- `comfyui.py` — обёртка над существующим `ComfyUIImageProvider` (text-to-image, локально).
- `openrouter.py` — `/api/v1/images`, `/api/v1/videos`, опрос `/api/v1/videos/{id}`,
  остаток `/api/v1/auth/key` до отправки; ключ из `openrouter_identity.resolve`;
  каждый вызов через `GovernedAdapter` (локальность, цена, бюджет). Логика
  страховки бюджета переносится из `tools/seedance_shorts.py` (потолок,
  «остаток < порога → не отправлять дальше»).
- `higgsfield_mcp.py` — **официальный путь номер один**. Через
  `mcp_runtime.call_tool` к официальному MCP-серверу Higgsfield
  (`generate_image / generate_video / generate_audio`, `jobs_wait`,
  `show_generation_by_ids`, `models_explore` для сверки каталога, `balance`
  для остатка кредитов). Допускается только после полосы
  `tools/mcp_acceptance.py` (16 проб) со строкой вердикта в
  `docs/final/CONNECTOR_INTEGRATION_REPORT.md`; при < 15 PASS — `REFERENCE_ONLY`.
  MCP-сервер — отдельный процесс: ленивый старт, остановка по бездействию,
  числа покоя до/после обязательны. Отказ по кредитам (`credits = 0`,
  бесплатный план) отображается в причину `insufficient_credit` →
  `OWNER_REQUIRED`, никогда в `provider_down` и никогда в FAIL.
- `higgsfield_http.py` — **пишется только по официальной документации**.
  На 17.09.2026 официальный REST-контракт не подтверждён: `docs.higgsfield.ai`
  закрыт egress-прокси среды, а форма `POST /{model-path}` →
  `request_id/status_url/cancel_url`, `GET /requests/{id}/status`, заголовок
  `Authorization: Key <id:secret>` известна только из чужого клиента
  `wide-trace/open-higgsfield` (без LICENSE и без тестов) — это гипотеза о
  чужом API. До официального контракта запись в каталоге несёт
  `OWNER_REQUIRED: official REST contract not supplied`, и ни один эндпоинт не
  вызывается. Подбирать пути экспериментом запрещено. Когда контракт получен:
  base URL и ключ — настройки владельца в Vault, статус
  `ADOPT_AS_OPTIONAL_BACKEND`, выключен по умолчанию.

Эгресс: адаптер получает медиа-входы только как локальные ассеты; перед
первой отправкой байтов владельца провайдеру интерфейс показывает, что и куда
уходит, и ждёт подтверждения (один раз на провайдер и роль, отзывается в
настройках). Публичных загрузок нет.

## W3. Одна строка — composer

Страница `images` → «Студия». Композер: поле промпта (⌘/Ctrl+Enter), выбор
модели (поиск по каталогу, фильтр image/video/audio, метки «локально»,
«бесплатно», «цена неизвестна», «не проверено»), панель настроек из схемы
модели, лоток медиа по ролям (из библиотеки студии, медиа Video Studio,
загрузки), счётчик пакета, кнопка «Подсказать модель» — `RouteRequest(
task_type=surface, cloud_allowed=<настройка>, max_price_out=<бюджет>,
prefer_local=True, require_verified=True)` → объяснимый шорт-лист.

Отправка = один объект `{model, prompt, media[roles], settings, seed, count}`
(«plane» в терминах open-higgsfield). Сервер валидирует по схеме модели —
лишние или чужие значения отвергаются с именем поля, а не «400».

## W4. Очередь и жизненный цикл

Таблица `studio_jobs` (обобщение `image_jobs`: `surface`, `model_id`,
`provider`, `plane` JSON, `request_id`, `state`, `reason`, `deadline_at`,
`cost_usd`, `progress`) и `studio_runs` (законченные результаты). Дисциплина
`process_one()`: атомарный захват одной заявки, рендер в отдельной задаче,
опрос отмены каждые 0,5 с (BL-066), дедлайн из каталога, никаких слепых
повторов — только лестница W2. Опрос провайдера — пакетом на все активные
заявки (урок `poll.ts`: один вызов на интервал, не по заявке).

Стоимость: до отправки — оценка по каталогу (или «неизвестна»); после — факт
(`/auth/key` до/после для OpenRouter, `balance` для Higgsfield), записывается
в `studio_runs.cost_usd` или `NOT_CAPTURED` с причиной. Дневной и прогонный
лимиты — в `governor.py` (`cloud_budget_usd`), превышение → `stop`.

## W5. Единая галерея и provenance

`studio_runs` — по одной записи на результат:

```
run_id, job_id, surface, model_id, provider, prompt_text, prompt_sha256,
settings_resolved (JSON), seed, inputs [{asset_id, role, sha256}],
output {path, sha256, bytes, mime, width, height, duration_ms, fps},
provider_request_id, state, reason, queued_at, started_at, finished_at,
cost_usd | NOT_CAPTURED, harness {repository_sha, catalog_sha256},
favorite, collection_id, deleted_at (корзина)
```

Запись неизменяема после `finished_at` (триггер, как в
`db._install_provenance_guard`). Галерея: скоупы Изображения / Видео / Звук /
Избранное / Корзина, сетка по настоящему соотношению сторон, просмотрщик с
provenance, действия reuse / избранное / коллекция / удалить (в корзину, 30
дней, восстановление), выделение и массовые операции, скачивание пакетом.
Потолка записей нет; старые ассеты не «протухают», потому что байты локальные.
Существующие `image_assets` мигрируют в `studio_runs` аддитивно (старая
таблица остаётся до следующего мажорного этапа).

API: `GET /api/studio/models`, `POST /api/studio/models/refresh`,
`POST|GET /api/studio/jobs`, `GET /api/studio/jobs/{id}`,
`POST /api/studio/jobs/{id}/cancel|retry`, `GET /api/studio/runs`
(фильтры: surface, model, favorite, collection, q), `GET /api/studio/runs/{id}`,
`GET /api/studio/runs/{id}/file`, `POST /api/studio/runs/{id}/reuse`,
`DELETE /api/studio/runs/{id}` (корзина), `POST /api/studio/runs/{id}/restore`,
`GET /api/studio/budget`. Существующие `/api/images/*` остаются алиасами до
переключения интерфейса.

## W6. Связка с движками

- Video Studio: результат → `media.import` с `provenance_ref = run_id`; панель
  «ИИ» получает действие «Сгенерировать кадр/клип для выбранного места»;
  `analysis.generation_status()` возвращает `AVAILABLE` только при наличии
  провайдера с `VERIFIED = true` в БД, иначе прежний `BLOCKED` с причиной.
- Раскадровка: `storyboard.plan_storyboard()` → список кадров → N заявок
  студии с одним `collection_id`; порядок и рецепт записываются в `settings_resolved`.
- Web Designer: выбор изображения из галереи в существующий поток `edit`.
- Агенты: инструмент `studio.generate` (по образцу `video.*`), исполнитель с
  `gate_completion`: «completed» провайдера — наблюдение; завершение — после
  `read_verification` байтов (декодирование, размеры, длительность).

## W7. Пост-обработка (кандидаты, не обещания)

Рефрейм (кроп/пад/масштаб) — FFmpeg уже в архиве; апскейл (Real-ESRGAN и
аналоги), удаление фона (rembg/BiRefNet) — записываются в матрицу пяти ответов
`docs/oss/README.md` и проходят тот же путь, что Docling и faster-whisper:
пин SHA, лицензия по файлу, стоимость покоя, тест на настоящем бинаре или
`OWNER_REQUIRED`. Веса не входят в архив и не скачиваются автоматически.

## W8. Что НЕ строится

Второй роутер, второй Vault, второй MCP-хаб, отдельная страница «Видео-
генерация», фоновые опросы провайдеров, публичное хранилище загрузок,
автопродление подписок, «безлимит», TikTok/сайты/3D.
