# Jeff Elite Local Voice Stack — техзадание владельца (2026-09-27)

**СТАТУС: ЗАБЛОКИРОВАНО** до `FREEZE_1_5_1_7=PASS`, `READY_FOR_1_8=YES`, `P0_OPEN=0`, `P1_OPEN=0`.
До этого: не качать модели, не трогать Jeff, не менять canonical.
После freeze: зафиксировать `FINAL_FREEZE_SHA`, ветку freeze не трогать, одна feature-ветка от неё.

## Миссия
Владелец отдаёт ~15 минут авторизованной записи своего голоса (MP3; также MP4 — берётся только звук).
Bossman локально чистит и анализирует запись, выбирает лучшие сегменты, создаёт переиспользуемый
локальный профиль голоса. Jeff отвечает в Telegram голосовыми этим голосом, по запросу — с фоном
(полиция на улице, ремонт у соседей, улица, кафе, дождь, аэропорт, стройка, вечеринка за стеной, офис).
Итог — обычное голосовое сообщение Telegram. **Основной путь работает офлайн после загрузки моделей.**

## Архитектура
- Единый нативный runtime: **audio.cpp** (`github.com/0xShug0/audio.cpp`) — C++, GGUF, TTS/ASR/клонирование/
  генерация аудио, HIP/ROCm, Windows, цель gfx1151 (Strix Halo). Один сервер/API вместо набора Python-демонов.
- Бэкенд: HIP/ROCm, сборка под gfx1151 (`--backend hip`). При избыточном резервировании unified memory —
  отключить graph-caching. NVIDIA CUDA не ставить.
- Железо: AMD Ryzen AI Max+ 395, Radeon 8060S (gfx1151), 128 GB unified memory, Windows 11.

## Финалисты (только они)
| Модель | Роль | Лицензия |
|---|---|---|
| Qwen3-TTS-12Hz-1.7B-Base | основной кандидат клонирования (Base, не CustomVoice), русский | Apache-2.0 |
| Fish Audio S2 Pro (4B) | претендент на макс. выразительность | research/non-commercial — только личный бенчмарк |
| Higgs Audio v3 TTS 4B | претендент voice-agent | research/non-commercial — только личный бенчмарк |
| AuK-Flash (Tencent-Hunyuan) | редактирование/улучшение аудио | допуск в продакшн только после русского теста на голосе владельца |
| Qwen3-ASR-0.6B (fallback 1.7B) | транскрибация, русский обязателен | проверить |
| Stable Audio 3 Small SFX | генерация фонов | проверить |

Не ставить по умолчанию: IndexTTS-2.5, Chatterbox, XTTS, F5-TTS, CosyVoice, случайные HF-модели —
только если все финалисты провалят конкретное требование. Whisper-large — только если Qwen3-ASR провалит тест.
Non-commercial модель может выиграть по качеству, но **не становится молча** коммерческой зависимостью.

## Пайплайн записи
MP3/MP4 → ffmpeg decode → VAD → Qwen3-ASR → детекция тишины/шума → проверка одного говорящего →
оценка сегментов → 5–10 лучших сегментов по 5–30 с (чистая речь, один голос, без музыки/клиппинга/эха).
По возможности сохранить манеры: нейтральная, разговорная, энергичная, серьёзная, тихая.
Не подавать 15 минут целиком как референс. Сначала zero-shot; fine-tune — только если замеры покажут, что zero-shot не хватает.

## Профиль
`owner_voice/{profile.json, references/, transcripts/, embeddings/, benchmark/, generated_samples/}`;
`profile.json`: voice_profile_id, owner_authorized=true, language=ru, references, transcripts,
preferred_engine, fallback_engine, model_revision, created_at, quality_metrics. Без секретов.
Сырое аудио — только локально. Удаление профиля удаляет и производные данные.

## Хранение и загрузка моделей
Один каталог моделей Bossman вне репозитория; веса не коммитятся; без дублирующих кэшей HF.
Загрузка возобновляемая, последовательная: audio.cpp → Qwen3-ASR-0.6B → Qwen3-TTS-1.7B → Fish S2 Pro →
Higgs v3 → AuK-Flash → Stable Audio SFX. После каждой: LOAD → INFERENCE → MEMORY CHECK → UNLOAD.
Не держать все 4B-модели одновременно; guard unified memory Bossman остаётся главным.
`voice_models_manifest.json`: model, source, revision, license, commercial_status, path, size, backend, hash, tested, benchmark_result.

## Реальная AMD-приёмка
Компиляция ≠ работа. Нужно: ROCM_DETECTED, HIP_RUNTIME, GPU_ARCH=gfx1151, GPU_NAME, MODEL_BACKEND;
по каждой модели GPU_LOAD, CPU_LOAD, PEAK_UNIFIED_MEMORY, TTFA, RTF, OUTPUT_SECONDS, CRASHES.
Модель, тихо работающая на CPU, — не `ROCM_PASS`.

## Чемпионат голосов
Один русский набор для Qwen3-TTS, Fish S2 Pro, Higgs v3 (и AuK, если прошёл русский гейт): нейтральная речь,
разговорный ответ, энергичный, тихий/серьёзный, длинный абзац. Метрики: сходство голоса, WER через Qwen3-ASR,
латентность, RTF, память, доля отказов, клиппинг, ошибки тишины, повторяемость. Плюс анонимные A/B для прослушивания.
Победитель — по замерам на железе владельца: PRODUCTION_VOICE_ENGINE, FAST_FALLBACK, EXPRESSIVE_FALLBACK, AUDIO_EDIT_ENGINE.

## Фоны (SFX)
Кэшированные пресеты: police_distant, renovation_neighbors, rain_window, street_city, cafe, airport,
construction, party_distant, office. «Скажи голосом, будто у соседей ремонт» = голос владельца + тихий ремонт,
не другой синтетический голос. Микширование ffmpeg/медиа-слой Bossman: ducking, без клиппинга, речь доминирует,
fade-in/out, нормализация громкости. Уровни none/low/medium/high, по умолчанию low.
Ограничения безопасности — `docs/v1.5/VOICE_AND_PHONE.md` §5a.

## Интеграция с Jeff
Текст ответа Bossman → существующий runtime Jeff → VoiceRouter → TTS → (AuK) → (SFX) → ffmpeg mix → OGG/Opus →
**существующий** Telegram-транспорт. Не создавать: нового бота, второго поллера, второй системы approvals,
новой личности владельца, автономного отправителя. Генерация голоса — ноль новых полномочий.
Проверки Telegram: правильный чат, одна отправка, играбельное голосовое, один поллер, STOP работает.

## Офлайн-гейт
После загрузки отключить внешние API генерации: ASR_LOCAL, TTS_LOCAL, SFX_LOCAL, MIX_LOCAL, OGG_LOCAL = PASS.
Интернет нужен только для доставки в Telegram.

## Codex-лаунчер
`scripts/windows/start-codex-jeff-voice-test.ps1` + `docs/prompts/CODEX_JEFF_VOICE_ELITE_E2E.md`:
проверяет репо, ветку, ожидаемый SHA, Codex CLI, ROCm, audio.cpp, манифест моделей; Codex — аудитор/тестер,
не дизайнер; не ослабляет гейты. Также `scripts/windows/bootstrap-jeff-elite-voice.ps1`.

## Лицензионная матрица
`docs/voice/MODEL_LICENSE_MATRIX.md`: для каждой модели PERSONAL_USE / COMMERCIAL_USE / ATTRIBUTION / RESTRICTIONS.

## Успех
Запустить Bossman → отдать `owner_voice.mp3` → «это мой голос, создай профиль» → профиль создан локально →
«ответь голосовым» → ответ голосом владельца → «ответь голосом, будто у соседей ремонт» → тот же голос + тихий ремонт.

Мёрж в canonical — только при `P0_OPEN=0`, `P1_OPEN=0` и маршрут прошёл на реальном AMD-железе владельца.
