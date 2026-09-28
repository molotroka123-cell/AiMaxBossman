# Handoff для Claude — видеоконвейер, рассылка Jeff, находки по архитектуре (2026-09-28)

Автор: агент сессии (OpenCode/GLM) по поручению владельца. Секретов здесь нет и не должно быть.

## 1. Доказанный видеоконвейер (всё через инструменты Bossman в tools/)

### Higgsfield Genjutsu motion transfer — `tools/genjutsu_motion_transfer.py`
- Модель: `POST https://api.higgsfield.ai/higgsfield/genjutsu/motion-transfer/v1.0`,
  payload `{prompt, video_url, image_urls[], resolution}` → `{request_id, status_url}` → poll `status_url`
  до `completed|failed|nsfw|canceled`, результат в `video.url`.
- Загрузка входов: `POST /files/generate-upload-url` (`{"content_type":"video/mp4"|"image/jpeg"}`)
  → PUT по `upload_url` с `upload_headers` → использовать `public_url`. Upload URL живёт 1 час.
- Цены за секунду входа (округление вверх): 480p $0.318, 720p $0.681, 1080p $1.632.
  Тест владельца: 10 с @480p ≈ $3.18 — перенос движения и замена персонажа работают (фон исходного клипа сохраняется).
- Auth: заголовок `Authorization: Key KEY_ID:KEY_SECRET`. Ключ — только в env/локальном файле, не в коде.

### OpenRouter Video API — `tools/video_character_swap.py`
- `POST https://openrouter.ai/api/v1/videos`, poll `GET /api/v1/videos/{id}`, скачивание `{id}/content`.
- `alibaba/wan-3.0`: 480p $0.05/с (2–30 с), 9:16, `input_references` (изображения), `frame_images` (first/last).
- `bytedance/seedance-2.5`: 4–30 с, 480p/720p, video_tokens $0.0000107 (с видео-входом $0.0000064).
- Ограничение: видеореференс (перенос движения) через OpenRouter НЕ поддерживается — только картинки.
  Тест владельца: Wan 3.0, 16 с @480p = $0.68.

### Общие правила
- Ключи: env `OPENROUTER_API_KEY`, `HIGGSFIELD_API_KEY`, либо
  `%LOCALAPPDATA%\Bossman\secrets\openrouter-test.env` (формат `NAME=value`, читать `utf-8-sig` —
  PowerShell пишет BOM; в файле не должно быть дублей строк).
- Бюджет: предохранитель в инструментах (проверка `limit_remaining` через `GET /api/v1/auth/key`),
  state-файлы в `artifacts/video_factory/out/state_*.json`, артефакты у владельца.
- Доставка в Telegram: `tools/telegram_send_video.py`, `tools/telegram_send_text.py`
  (токен `TG_COMPANION_BOT_TOKEN` из `%LOCALAPPDATA%\Bossman\telegram-companion\companion.env`,
  chat владельца из `config.json people[].chat_id`; токены в память процесса, не печатать).

## 2. Рассылка через Jeff (голос)
- WinRT TTS: арабские «Online (Natural)» голоса (ar-QA) локальному движку НЕДОСТУПНЫ —
  проверка русского текста арабским голосом невозможна → по правилу владельца массовая отправка
  с ним запрещена; рабочий голос — локальный `ru-RU` (Irina), SSML prosody rate+8% volume+15% pitch+3%
  («энергично, дружелюбно, без крика»).
- Объективная проверка произношения: TTS → WAV → faster-whisper ASR → сверка ключевых слов.
- Дубликаты: журнал отправок в `artifacts/night_greeting/`; при неопределённом статусе — сначала
  проверка статуса, автоповтор запрещён.
- Голосовое в Telegram = OGG/Opus 48 kHz mono (`ffmpeg -c:a libopus`), `sendVoice`.

## 3. Находки по архитектуре Jeff/Bossman (для Command-приложения v0.1)
- Живой Jeff Telegram = `bcc.pit` из установленного билда `BOSSMAN-Windows-x64-84f5e0acce6a`
  (исходник идентичен worktree `C:\Users\asd\Bossman\wt19-verify`). Документовский чекаут старее билда.
- Персона: константа `PIT_ASSISTANT_SYSTEM` (`bcc/pit/participant_context.py`), шкалы поведения
  `behavior_scales` (initiative/curiosity/depth/brevity/warmth/humor/directness/creativity) в
  `pit-v1.7\config.json` → рендер `behavior_system_text()`.
- Лучший хук настроек-оверлея: `build_participant_context()` + файл
  `%LOCALAPPDATA%\Bossman\jeff-settings.json` (defaults + behavior_scales + system_extra +
  users{person_key:...}), читать при каждом сообщении с mtime-кэшем; пустой файл = сток.
  Отдельные пользователи — HMAC `person_key` → `pit-v1.7\personalities\<sha256>\`.
- Пульт-компаньон (`bcc.telegram_companion`) — отдельный бот, config.json + companion.sqlite3;
  облако-фоллбэк игнорирует персону из конфига (`adapters.py` SYSTEM) — известный баг, чинить.
- Прямой чат с Jeff без Telegram: `POST http://127.0.0.1:8850/api/jeff/chat` (тот же рантайм).
  Control API: `http://127.0.0.1:8801` (`/api/telegram/settings` уже есть для Пульта).
- «Паспорта»: `bcc/pit/passport_checkpoint.py` (bossman.jeff.passport-checkpoint.v1),
  principals `human:/agent:/policy:` в bossman_v3/fleet/credentials.py.

## 4. Незакрытое (следующие шаги)
- Bossman Command v0.1: UI-пульт шкалами Jeff + per-user overrides + reset + поля бюджета
  (роить агентов, хук из п.3).
- Аудит RC19: инвентаризация + археология падений (27 failed/2 errors; Command Center ~88%) —
  идёт роем; затем полные прогоны bossman-core и Command Center последовательно.
- Чинить: персону в облако-фоллбэке компаньона; рассинхрон ветки в Documents vs билд 84f5e0a.
