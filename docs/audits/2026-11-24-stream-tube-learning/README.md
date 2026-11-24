# Stream Tube → обучение Боссмана: SponsorBlock, RYD, контур управления загрузкой (24.11.2026)

Статус: **PARTIAL — исходники и юнит-тесты PASS; живой прогон на ПК владельца NOT_RUN.** Это не сертификат.

## Что взято из Stream Tube и зачем

Stream Tube (нативный клиент YouTube на Rust, GitHub `Avaneesh-Inamdar/Stream-Tube-Desktop`, альфа от 22.11.2026)
— GUI-плеер для человека. Для обучения агента сам плеер не нужен, нужны **идеи его бэкенда**. Код не копировался;
вызываются только публичные API SponsorBlock и Return YouTube Dislike.

| Идея Stream Tube | Что стало в Боссмане | Файл |
|---|---|---|
| SponsorBlock (вырезает рекламу/спонсоров) | реклама, самопиар, «подпишитесь», интро/аутро удаляются из субтитров **до** нарезки — не превращаются в claims | `command-center/bcc/learning_video/stream_signals.py` |
| Return YouTube Dislike | приоритет источника: нижняя граница Уилсона по лайкам/оценочным дизлайкам — какие видео учить первыми; только совет, ничего не отклоняет | там же |
| Выбор playback-клиента (visionOS / Android VR / yt-dlp) | цепочка `player_client`: default → android_vr → tv → web_safari → mweb | `command-center/bcc/learning_video/fetch_control.py` |
| yt-dlp проверяется по SHA-256 | `BOSSMAN_YTDLP_SHA256` → `BLOCKED_BINARY_MISMATCH` до запуска | там же |
| Честная ошибка «Sign in to confirm you're not a bot» | `BLOCKED_BOT_CHECK`; cookies и вход **никогда** не используются | там же |

## Улучшение архитектуры управления

Раньше `tools/youtube_trader_ingest.py` вызывал yt-dlp один раз и глотал ошибки. Теперь все загрузки для обучения
идут через один `FetchController`:

```
URL → проверка id → [SponsorBlock k-анонимно (4 символа SHA-256), RYD] → ранжирование по prior
    → FetchController: pin SHA-256 → предохранитель открыт? → клиент 1..N
        классификация stderr: OK / NO_SUBS / BOT_CHECK / RATE_LIMITED / UNAVAILABLE / NETWORK / UNKNOWN
        BOT_CHECK, UNKNOWN → следующий клиент
        RATE_LIMITED       → стоп + открыть предохранитель (30 мин, состояние на диске)
        UNAVAILABLE        → терминально, без повторов
        NETWORK            → стоп, повтор позже
        3 видео подряд с бот-стеной → предохранитель открыт
    → run_pipeline(skip_segments=…, prior=…) → claims (UNVERIFIED) → claims.jsonl
```

Квитанция на каждую попытку: клиент, вердикт, rc, время, число файлов. Без текста субтитров, cookies и аккаунтов.
Подозрительные сегменты (вырезали бы >60 % речи) игнорируются целиком (`SUSPICIOUS`), заминусованные — не доверенные.

CLI: `python tools/youtube_learning_fetch.py URL [URL …] --out D:\Bossman\learning\yt [--lang ru] [--no-signals]`

## Доказательства (это окружение: Linux-песочница, не ПК владельца)

- Новые тесты: `command-center/tests/test_learning_video_stream_signals.py` (36), `tests/test_youtube_learning_fetch.py` (4) — PASS.
  Позитивный контроль: без SponsorBlock рекламная фраза **попадает** в claims, с ним — нет.
  Негативные контроли: частичное перекрытие не режет; заминусованный сегмент игнорируется; «тролль-сегмент» отклонён;
  бот-стена на всех клиентах = BLOCKED, а не успех; cookies не передаются; несовпадение SHA блокирует запуск.
- Регрессия старого конвейера: `test_learning_video_claude.py`, `test_video_learning_primitives.py`,
  `tests/test_youtube_trader_ingest*.py`, `tests/test_youtube_claim_verification.py` — PASS (поведение по умолчанию не изменено).
- Живой прогон с настоящим **yt-dlp 2026.11.23**: все клиенты `android_vr/tv/web_safari/mweb` приняты как аргументы;
  YouTube/SponsorBlock/RYD закрыты прокси песочницы → вердикт `NETWORK`, prior `INSUFFICIENT_EVIDENCE`, exit 2. Ложного успеха нет.
- **Найденный живьём дефект:** строка yt-dlp `Unable to download API page … Unable to connect to proxy` классифицировалась
  как UNKNOWN (перебор всех клиентов впустую). Исправлено, дословная строка добавлена в регрессионный тест.

## Не проверено (NOT_RUN)

- Реальные субтитры с YouTube, ответы SponsorBlock/RYD вживую — нужен прогон на ПК владельца.
- Путь через пульт/чат (правило 23.10 «PROVEN, not wrapped») — фича **не считается добавленной**, пока нет живой квитанции.
- Предсуществующие красные (не от этой ветки): `tests/test_ci_secret_scan.py::test_repository_itself_is_clean`
  падает и на базовом коммите `75f7b76` (старые файлы в `docs/audits/harness-tier4-20261105`, `tools/local3d`);
  новые файлы этой ветки сканер не помечает. Ещё 5 красных на базе, без изменений этой веткой:
  `telegram_contracts/test_companion_learning.py` (3), `test_fable_video_chaos.py::test_corrupt_media_upload…`,
  `test_telegram_settings.py::test_persona_learning_profiles_and_export`.

## Как проверить на ПК владельца

```powershell
python tools\youtube_learning_fetch.py https://youtu.be/<урок_с_рекламной_вставкой> --out D:\Bossman\learning\yt-proof
```
Ожидается: `fetch.verdict = OK`, `pipeline.sponsor_strip.status = STRIPPED`, `prior.status = OK`; квитанцию положить сюда.
