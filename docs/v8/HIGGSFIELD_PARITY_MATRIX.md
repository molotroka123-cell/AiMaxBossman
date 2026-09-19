# Матрица паритета: Higgsfield, open-higgsfield, Bossman сегодня, цель V8

Все факты ниже получены 17.09.2026 инструментами, а не пересказом: каталог
Higgsfield — вызовом `models_explore list` подключённого MCP-сервера
(98 моделей); open-higgsfield — чтением клона `b16a0efe`; Bossman — чтением
кода на `e39610de`. Числа, которых нет в источниках, не выдуманы: там стоит
«не измерено».

## 1. Что такое Higgsfield для разработчика (по каталогу MCP)

- **98 моделей**: video 41, image 34, 3d 17, audio 6. Владельцы весов:
  Higgsfield 20, Meshy 10, Google 9, Bytedance 7, Black Forest Labs 5, Kling 5,
  xAI 4, Wan 4, OpenAI 3, FAL 3, Meta 2, MiniMax 2, Tripo 2, Tencent 2, прочие
  по одному; у 14 записей владелец не указан.
- **Запись модели**: `id, name, provider_name, description, output_type,
  parameters[{name, required, type, description, options|min|max, default,
  nullable}], medias[{name, type, roles[], max?}], aspect_ratios[], tags[],
  supports_unlim`. Самые частые параметры: `resolution` (38 моделей),
  `duration` (27), `quality` (12), `folder_id` (12), `generate_audio` (11),
  `mode` (11), `variant` (8), `seed` (7), `batch_size` (4).
- **Пример глубины**: `seedance_2_5` — `mode ∈ {t2v, omni_reference, video_edit,
  video_extension}`, `duration 4–30`, `resolution 480p|720p|1080p`,
  `generate_audio`, `bitrate_mode`, `extension_mode`; роли медиа
  `start_image / end_image / image_references / video_references /
  audio_references`; соотношения `auto, 21:9, 16:9, 4:3, 1:1, 3:4, 9:16`.
- **Семейства**: изображения — Soul 2.0 / Cinema / Cast / Location, GPT Image
  2 / 2.5, Cinema Studio 2.5, Marketing Studio Image, DTC Ads, `image_auto`,
  Nano Banana (Pro / 2 / 2 Lite), Seedream 4.5 / 5.0, FLUX.2 (+ Pro Outpaint),
  Flux Kontext, Kling O1 Image, OpenAI Hazel, Grok Image / 2.0, Recraft 4.1,
  Z Image; видео — Cinema Studio Video (v1/v2/3.0), Marketing Studio, FLUX 3
  Video (+ Edit), Grok Video / 1.5, MiniMax Hailuo / H3 / H3 Max, Wan 2.6 /
  2.7 / 3.0 / 3.0 Prime, Seedance 1.5 Pro / 2.0 / 2.0 Mini / 2.5, Ad
  Multiplier, Kling 2.6 / 3.0 / 3.0 Turbo / Omni Edit, Happy Horse, Genjutsu
  (перенос движения, замена объекта), Gemini Omni Flash / 1.1, Veo 3 / 3.1 /
  3.1 Lite, Sync Lipsync 3; звук — Seed Audio, Qwen TTS Flash, Sonilo Music,
  Mirelo, Inworld TTS, Text to Speech V2 (ElevenLabs / MiniMax / Seed Speech /
  Vibe Voice / Cozy Voice); обработка — Topaz / Bytedance апскейл, удаление
  фона (SAM 3), деффликер, Outpaint, Clipify; 3D — Meshy 5/6/7, Tripo,
  Hunyuan3D, SAM 3 3D, риггинг, ремеш, ретекстура.
- **MCP-инструменты** (имена из этой сессии): `generate_image / video / audio`
  (+ `_batch`, `jobs_wait`, `show_generation_by_ids`), `motion_control`,
  `reframe`, `remove_background`, `upscale_image / video`, `outpaint_image`,
  `dubbing`, `voice_change`, `create_voice`, `list_voices`, `animation_actions`,
  `generate_3d`, `scene_builder_3d_*`, `shorts_studio_*`, `video_analysis_*`,
  `virality_predictor`, `tiktok_*`, `show_characters`, `show_reference_elements`,
  `presets_show`, `show_marketing_studio_v2`, `apps_search / describe / invoke`,
  `create_website / deploy / publish`, `media_upload / import_url`,
  `balance / transactions / show_plans_and_credits`; 16 бандл-воркфлоу
  (ad-multiplier, brand-asset-creation, character-sheet, faceless-video,
  narrator, product-photoshoot, subtitles, thumbnail-generation, шесть
  UGC-сценариев, video-editing, website-builder-flow).
- **Аккаунт владельца, измерено `balance`**: `{"credits": 0,
  "subscription_plan_type": "free"}`. Любая живая генерация через официальный
  Higgsfield сейчас упирается в это: причина `insufficient_credit` →
  `OWNER_REQUIRED` (оплата/план), не FAIL и не «провайдер лежит».
- **Официальная документация REST в этой среде недоступна**:
  `docs.higgsfield.ai` закрыт egress-прокси (`EGRESS_BLOCKED`). Поэтому base
  URL, заголовок и пути submit/status остаются **непроверенными**, а
  официальным считается MCP-путь.
- **Бесплатное**: 20 моделей помечены `supports_unlim` (безлимит пробного
  периода); для аккаунта, подключённого к этой сессии, `unlim.available =
  false`. Цены в каталоге не отдаются — стоимость узнаётся по факту
  (`balance`, `transactions`).

## 2. Что такое open-higgsfield на самом деле

- **Тонкий клиент к платформенному API Higgsfield**, не альтернатива моделям.
  `.env`: `HF_API_BASE_URL` (origin API, только сервер) и
  `OPEN_HIGGSFIELD_READ_WRITE_TOKEN` (Vercel Blob). Ключ владельца `id:secret`
  хранится в httpOnly-cookie; заголовок `Authorization: Key id:secret`.
- **Контракт платформы** (`src/generation/platform.ts`): `POST /{model-path}`
  (например `higgsfield-ai/soul/v2/standard`, `kling-video/v3.0/pro/image-to-video`,
  `bytedance/seedance-2.5/video-edit`) → `{request_id, status, status_url,
  cancel_url}`; `GET /requests/{id}/status` → `{status, images[{url}] |
  video{url}, error}`; терминальные статусы `completed | failed | nsfw |
  canceled`; опрос каждые 4 с, дедлайн 10 мин, три промаха подряд роняют все
  ожидания (`poll.ts`).
- **Каталог — источник истины** (`catalog/types.ts`): `ModelEntry {id, surface
  image|video, label, roles{start,end,reference,video,audio: max}, settings{enum
  | range | boolean, default}, paths}`; 38 записей (8 image, 30 video):
  soul-2, soul-cinema, seedance-2.5 (+edit, +extend), seedance-2 (+fast,
  +mini), kling-3 turbo/std/pro/4k/motion, flux-2, flux-3, grok-imagine-2,
  ideogram-4, recraft-4.1, qwen-image-3, z-image-turbo, wan-2.6/2.7/3/3-prime,
  minimax-h3, minimax-hailuo-2.3, happy-horse-1/1.1, kling-2.5/2.6/o1/o3,
  ltx-2.5 fast/pro, grok-imagine-video-1.5, pixverse-6, dop.
- **Студия**: один composer (модель решает image/video), панель настроек из
  allow-list модели, роли медиа с лимитом, asset picker (загрузки + прошлые
  прогоны), пакет до 4, живой цикл (скелеты → poll), галерея четырёх скоупов,
  reuse (модель + настройки + промпт), просмотрщик, выделение и массовые
  операции, отмена удаления 6 с.
- **Состояние**: история — 60 записей в IndexedDB браузера; URL результатов
  принадлежат CDN платформы и по README «могут пережить срок жизни CDN и
  показывать дыры»; загрузки — в **публичный** Vercel Blob; тестов в
  репозитории нет (каталога `tests` нет, `scripts/` — только бренд-ассеты);
  **файла LICENSE нет**; 1 контрибьютор; provenance (seed, стоимость, хэши) не
  записывается; нет ни бюджета, ни различения 429 / нет ключа / модель
  недоступна (одна `PlatformError` с текстом ответа).

## 3. Что есть у Bossman сегодня (`e39610de`)

| Область | Есть | Нет |
|---|---|---|
| Изображения | очередь `image_jobs` с атомарным захватом и кооперативной отменой посреди рендера (BL-066), библиотека/коллекции/шаблоны, `/api/images/*`, страница «Изображения»; провайдеры `mock-image` и `comfyui` (loopback, только text-to-image, PNG) | облачный провайдер в продукте; схема параметров на модель (одна общая `ImageJobIn`: 256–4096 px, steps 1–200, count 1–8); видео/звук |
| Видео | Video Studio: монтаж, undo/redo, CAS-ревизии и аренды, FFmpeg preview/export с проверкой потоков и декодирования, подписанная квитанция экспорта, раскадровка (`storyboard.py`, рецепты hook/reveal/detail/benefit/cta/proof), 24 инструмента `video.*` для агентов, локальный LoRA-черновик команд | **генерация видео отсутствует**: `generation_status()` → `BLOCKED: No verified image/video generation provider is connected to Video Studio` |
| Звук | ASR faster-whisper (WAV ≤ 10 мин), субтитры SRT/VTT, EN→RU перевод субтитров | TTS, клонирование голоса, дубляж, музыка — нет ничего |
| Маршрутизация | `model_router.py` (проба важнее каталога, дисквалификация по неизвестной цене, `prefer_local`), `model_health.py` (healthy / silent / malformed / throttled / unauthorized / provider_down / timeout / unmeasured), `openrouter_ext.explain_status` (401/402/403/404/429), `openrouter_identity` (Vault ⟶ env, конфликты названы), `provider_governance.GovernedAdapter` (локальность и цена на каждом вызове), `governor.py` (бюджет → stop) | выключателя «только бесплатные» в коде нет — это политика прогона |
| Облачная генерация | `tools/hailuo_rescue.py` (OpenRouter `/api/v1/videos`, `minimax/hailuo-3-max`, страховка бюджета 0,10 $), `tools/seedance_shorts.py` (`bytedance/seedance-2.0-mini`, потолок 3,40 $, `/auth/key` для остатка) — вне продукта | эти вызовы в `bcc` |
| MCP | `mcp_hub.py` (allowlist бинарей для stdio), `mcp_runtime.py` (здоровье, смерть транспорта), полоса `tools/mcp_acceptance.py` (16 проб: запуск, рукопожатие, tools/list, схема, валидный/невалидный запрос, таймаут, отмена, падение, рестарт, неверная версия, битый JSON, дубль ответа, oversize, неизвестный инструмент, отказ в правах) | коннектор Higgsfield любого вида |
| Provenance | `run_provenance.py` для прогонов агентов (снимок на старте, неизменяем триггером БД: модель, инструменты, бюджет, SHA репозитория) | provenance для сгенерированных изображений/видео; единая галерея (сегодня — библиотека Images и экспорты Video Studio порознь) |
| Приёмка | контракт v2, реестр `windows-installed` 42 сценария, развёртка UI, вечерняя приёмка из архива | сценарии студии |

## 4. Матрица возможностей

Обозначения: ✅ есть и проверено; ◐ есть частично; ✗ нет; ⛔ намеренно не
делаем. Колонка «как измерить» — тест или строка вердикта, которая появится в
V8; до её появления возможность не считается сделанной.

| Возможность | Higgsfield | open-higgsfield | Bossman сегодня | Цель V8 | Как измерить |
|---|---|---|---|---|---|
| Одна строка промпта для image + video | ✅ | ✅ | ◐ (только image) | ✅ image + video + audio, модель решает поверхность, роутер подсказывает | `test_studio_composer_ui.py`: один ввод → две поверхности |
| Настройки ровно по схеме модели | ✅ (parameters в каталоге) | ✅ (allow-list) | ✗ (общая форма) | ✅ каталог `tools/studio_models.json` → панель | тест: панель = схема, лишних полей 0, чужие значения отвергнуты |
| Роли медиа (start/end/reference/video/audio) с лимитами | ✅ | ✅ | ◐ (`reference_asset_ids`) | ✅ | тест: лимит роли соблюдён, лишний референс отвергнут до отправки |
| Число моделей | 98 | 38 | 2 исполняемых | не цель; цель — **каждая объявленная модель проверена пробой** | `BOSSMAN_STUDIO_CATALOG=<n> verified=<k> unverified=<m>` |
| Локальная генерация без сети | ✗ | ✗ | ◐ ComfyUI t2i | ✅ ComfyUI t2i; видео — только если пройдёт пять ответов | матрица `docs/oss/README.md` + тест с настоящим ComfyUI (OWNER_REQUIRED без него) |
| Облачная генерация | ✅ | ✅ (одна платформа) | ✗ в продукте | ✅ OpenRouter, Higgsfield API — выключены по умолчанию | `BOSSMAN_STUDIO_LIVE=<provider>:PASS|OWNER_REQUIRED|FAIL` |
| Higgsfield как провайдер | — | ✅ | ✗ | ◐ HTTP-адаптер (optional backend); MCP — по вердикту полосы | 16 проб `mcp_acceptance.py` + сравнение с HTTP-путём |
| Пакет (batch) | ✅ | ✅ до 4 | ◐ `count ≤ 8` | ✅ с дедупом по (модель, настройки, промпт, seed) | тест: повторная отправка того же — «уже есть» |
| Живой цикл прогона | ✅ | ✅ poll 4 с / 10 мин | ✅ tick 0,7 с | ✅ дедлайн на модель из каталога | тест: истёкший дедлайн → `timeout`, не «running навсегда» |
| Отмена | ✅ `cancel_url` | ✗ (только локально) | ✅ освобождает воркер | ✅ + отмена у провайдера, где есть `cancel_url` | `test_images_cancel_button` расширен на video |
| Галерея всех прогонов | ✅ | ✅ (браузер, 60) | ◐ (порознь) | ✅ единая, без потолка, локальные байты | развёртка UI; тест: 61-й прогон не вытесняет 1-й |
| Reuse (модель + настройки + промпт) | ✅ | ✅ | ✗ | ✅ | тест: reuse даёт идентичный запрос к провайдеру |
| Provenance (seed, входы sha256, выход sha256, стоимость, время, request_id) | ◐ (в UI) | ✗ | ◐ (только агенты) | ✅ на каждый результат | `GET /api/studio/runs/{id}` содержит все поля или `NOT_CAPTURED` с причиной |
| Байты у владельца | ✗ (CDN) | ✗ (CDN) | ✅ (Images) | ✅ всё | тест: провайдер «забыл» URL → результат открывается |
| Пять разных отказов | ✗ | ✗ | ✅ словарь есть | ✅ применён к генерации | тесты-пары: нет ключа / 429 / 404 модели / nsfw / битый ответ |
| Бюджет | кредиты | ✗ | ◐ (скрипты, governor) | ✅ дневной и прогонный, бесплатные по умолчанию | тест: превышение → `stop`, платный fallback не вызван |
| Эгресс медиа владельца с предупреждением | — | ✗ (публичный Blob) | ✗ | ✅ | тест: без подтверждения байты не уходят |
| Апскейл / удаление фона / рефрейм | ✅ (Topaz, SAM 3, reframe) | ✗ | ◐ рефрейм FFmpeg | ◐ кандидаты через пять ответов | матрица OSS; тест на настоящем бинаре или OWNER_REQUIRED |
| TTS / дубляж / музыка | ✅ | ✗ | ✗ | ◐ TTS через провайдера (OpenRouter/Higgsfield), локально — кандидат | `BOSSMAN_STUDIO_LIVE=audio:…` |
| Результат → монтаж | ◐ (Higgsedit) | ✗ | ✗ | ✅ `media.import` в Video Studio с provenance | e2e: генерация → клип на таймлайне → экспорт с квитанцией |
| Результат → сайт / агент | ✗ | ✗ | ✗ | ✅ Web Designer, `studio.generate` под gate_completion | e2e существующих потоков B/E (`owner_flows.json`) |
| Раскадровка → пакет кадров | ◐ (воркфлоу) | ✗ | ✅ план без рендера | ✅ план → N прогонов | тест: рецепты → N заявок, порядок сохранён |
| Персонажи/референс-элементы (Soul ID) | ✅ | ✗ | ✗ | ◐ референс из памяти владельца (vault) | позже фазы 4 |
| 3D, TikTok, сайт-билдер, виральность | ✅ | ✗ | ✗ | ⛔ | — |
| Тесты | неизвестно | 0 | 3 535 (CC) + 42 на архиве | + сценарии студии в профиле | `require_acceptance_results --profile windows-installed` |
| Лицензия кода | закрытая | нет файла | своя | своя | — |

## 5. Где Bossman лучше по построению, а где честно не догонит

**Лучше по построению**: байты и история у владельца; несколько провайдеров с
одной честной семантикой отказов; бюджет как гейт, а не как счётчик; каждая
возможность — тест на установленном архиве; результат сразу живёт в
редакторах и у агентов под гейтом улик; никакого публичного Blob для
референсов.

**Не догоним и не будем делать вид**: число моделей (98 у Higgsfield — их
собственные и партнёрские веса), собственные модели Soul/Cinema Studio,
Genjutsu, 3D-конвейер, Marketing Studio с брендкитом, публикация в TikTok,
«безлимитные» пробные генерации. Всё это доступно Bossman только как
провайдер по ключу владельца — и так и будет названо в интерфейсе.
