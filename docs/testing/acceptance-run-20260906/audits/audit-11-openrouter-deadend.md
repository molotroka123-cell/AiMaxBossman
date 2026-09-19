# AUDIT-11: OpenRouter/GLM 5.3 — «владелец дал ключ, список облачных моделей не загрузился»

Источник: репорт владельца (TESTING PERIOD, сессия 9af5ed6fc912, 2026-09-06 ~22:5x).
«Цель — включить GLM 5.3 внутри Bossman — провалилась: дал ключ, список облачных
моделей не подгружается». Код не исправлялся (по указанию владельца) — только
воспроизведение и evidence. Воспроизведение: изолированный CC на 127.0.0.1:8801
(чистый data-dir), API-сессия через X-BCC-Token, живой ключ OpenRouter.

## OR-001 (P1) — провайдера OpenRouter не существует без env; UI-пути создать нет

- **Факт на машине владельца** (копия bcc.db, read-only): таблица `providers`
  содержит ЕДИНСТВЕННУЮ строку — «Ollama (запасной порт)» id=1. Строки OpenRouter
  НЕТ. `provider_catalog_models`=4 (ollama), `model_capability_checks`=0.
- **Root-cause**: провайдер создаётся ТОЛЬКО env-bootstrap'ом при старте CC:
  `command-center/bcc/features/openrouter.py:271` — env `BOSSMAN_OPENROUTER_API_KEY`;
  без переменной `setup()` молча выходит (openrouter.py:301-303).
- **UI dead-end**: страница OpenRouter при пустом списке провайдеров рендерит
  empty-state и НЕ показывает панель ввода ключа — она существует только внутри
  контекста выбранного провайдера (`ui/pages/openrouter.js:34`, `:43-48`,
  ключ — `buildConnectPanel` → PATCH /key `:92`). Создание провайдера доступно
  лишь generic `POST /api/providers` (api.py:636) со страницы Models —
  не с той страницы, куда приходит владелец за ключом.
- **Репро (изолированный CC)**: `GET /api/providers` → `[]`;
  `GET /api/openrouter/1/status|catalog` → 404 «провайдер не найден».

## OR-002 (P1) — каталог прячет z-ai/*: cap 200 + алфавитная сортировка, пагинации нет

- `command-center/bcc/features/openrouter.py:148`:
  `query.order_by(remote_id).limit(min(limit, 200))`, offset отсутствует.
- После УСПЕШНОГО connect+sync (430 моделей) фактические выдачи:
  `limit=100 → 100 rows, glm=0`; `limit=500 → 200, glm=0`;
  `limit=1000 → 200, glm=0`; `limit=5000 → 200, glm=0`.
  «z-ai/…» — последний в алфавитном порядке → GLM не видна никогда.
- Спасает `q=glm` → 17 строк (включая z-ai/glm-5.3-flash), но UI по умолчанию
  не подсказывает об усечении: бейдж показывает полный счётчик каталога
  (см. audits/audit-11b-list-caps-deadends.md, A11b-00).

## OR-003 (P2) — три имени одной переменной окружения

- `BOSSMAN_OPENROUTER_API_KEY` — bootstrap провайдера (openrouter.py:271, README:376).
- `OPENROUTER_API_KEY` — кред плагина `plugin:openrouter.chat` (plugins.py:99),
  читается только из os.environ.
- `OPENROUTER_API_KEY` + `BOSSMAN_VIDEO_OPENROUTER` — video_factory ядра.
  Владелец, вставивший ключ «как подсказывает UI/плагин», получает ровно
  наблюдаемый эффект: либо провайдера нет, либо плагин SKIP_EXTERNAL_CREDENTIAL.

## Позитивный путь (проверено живьём; как включить GLM 5.3 уже сегодня)

1. `POST /api/providers` {name:"OpenRouter", kind:"openai_compat",
   base_url:"https://openrouter.ai/api/v1", api_key:"<ключ>"} → id.
2. `POST /api/openrouter/{id}/connect` → `{"ok":true,"models":430}`.
3. `GET /api/openrouter/{id}/catalog?q=glm` → z-ai/glm-5.3-flash (ctx 1 310 720,
   price_in $0.075/M, tools/vision).
4. `POST /api/openrouter/{id}/pin` {"remote_id":"z-ai/glm-5.3-flash",
   "alias":"or-z-ai-glm-5.3-flash"} → model_id в активном реестре BOSSMAN
   (проверено: /api/models отдаёт запись с pricing_known=true).
В UI то же самое достижимо через страницу Models (визард провайдера), затем
OpenRouter → выбрать провайдера → ключ/Connect → поиск "glm" → Pin.

## Рекомендации кодеру (без реализации)

1. OpenRouter-страница при 0 провайдеров должна показывать форму «вставьте ключ →
   создаст провайдера» (POST /api/providers + PATCH key + connect одним флоу).
2. Каталог: total + offset/has_more (или дефолт-сортировка не алфавитная) и
   честный бейдж «показано X из N» (A11b-00).
3. Единое имя env-переменной + предупреждение doctor'а при расхождении (см.
   audit-11a-env-bootstrap-deadends.md A11a-01, audit-10-doctor-ci.md A10-01).

## Связанные находки того же класса

- `audits/audit-11a-env-bootstrap-deadends.md` — A11a-01…A11a-08 (плагины-заглушки,
  telegram-approvals небутабельны, web_research противоречие, OpenClaw без UI, 15
  недокументированных BOSSMAN_*-флагов).
- `audits/audit-11b-list-caps-deadends.md` — A11b-00…A11b-09 (approvals >100
  недостижимы; счётчики-«ложь» в tasks/images; ошибки ручек = «пусто»).
