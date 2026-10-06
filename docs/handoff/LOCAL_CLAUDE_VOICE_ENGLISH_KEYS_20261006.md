# Задание локальному Claude (ПК владельца) — 06.10.2026, одним сообщением

Same-product Terminal Run: пульт, CLI, дашборд — один Bossman; ничего отдельного не заводить. Ключи, деньги и стабильный код — только по существующим одобрениям; ключи не печатать и не коммитить.
Что уже в ветке `goal/bossman-self-improvement-tree-20261005` (после `git pull`): фикс EchoGuard (`6d9c1f56`), английский Jeff (`857d2758`), `tools/jeff_voice_samples.py`, запрос самоулучшения с жёсткими границами (`d2571594`).
Облако без ваших ключей и без голосовых моделей — то, что ниже, можно проверить только у вас.

## 1. Самоулучшение — настоящий прогон ($0)
Ключи OpenRouter/NVIDIA лежат в Vault Bossman (или в `.env` рядом с данными). Проверь, что бесплатный ключ виден бэкенду, не печатая его:
`python -m bcc.pit.cli doctor` (строка free_route = PASS). Затем:
`python tools\tree_self_improve.py --worker openrouter-free` (при отказе — `--worker nemotron-ultra-free`).
Зачёт — ТОЛЬКО `TREE_SELF_IMPROVE=VERIFIED_CANDIDATE`; тогда в Coding → «Применить» (подтверждает владелец). Любой другой вердикт — записать как есть в `docs/audits/2026-10-06-owner-pc-run.md` и запушить (fetch+merge, без force).

## 2. Jeff по-английски
* Скачать английский Piper-голос (например `en_US-lessac-medium.onnx` + `.onnx.json`) рядом с русским; задать `BOSSMAN_PIT_TTS_MODEL_PATH_EN=<абсолютный путь к .onnx>` (в тех же местах, где задан `BOSSMAN_PIT_TTS_MODEL_PATH`).
* Для звонка по-английски: в настройках звонков `language: "en"` (или `"auto"`), приветствие/раскрытие ИИ говорятся по-английски.
* Проверка: `bossman call selftest echo` и `call selftest basic` (оффлайн), затем один настоящий короткий звонок владельцу.

## 3. Образцы голосов в пульт: 3 русских + 3 английских
`python tools\jeff_voice_samples.py --out samples --voice ru-irina=<…ru_RU-irina-medium.onnx> --voice ru-ruslan=<…ru_RU-ruslan-medium.onnx> --voice ru-denis=<…ru_RU-denis-medium.onnx> --voice en-lessac=<…en_US-lessac-medium.onnx> --voice en-ryan=<…en_US-ryan-high.onnx> --voice en-amy=<…en_US-amy-medium.onnx>`
Файлы `samples/*.ogg` отправить владельцу в пульт (Telegram) подписями «1 ru-irina … 6 en-amy». Выбор голоса — за владельцем, ничего не включать самостоятельно.

## 4. Тестовое английское голосовое «всем»
Владелец дал добро на тестовое голосовое сообщение участникам Jeff о том, что он теперь понимает и говорит по-английски. Отправлять ТОЛЬКО через штатный путь Jeff (`jeff.broadcast`, согласия участников,
чёрный список и лимиты рассылки соблюдаются; STOP побеждает). Текст — короткий, английский, с раскрытием «AI assistant». Перед отправкой показать владельцу список получателей и количество.

## 5. Отчёт
Всё, что запускалось, — с логами в `docs/audits/2026-10-06-owner-pc-run.md` (чекпоинт 2) и push в ветку. Ничего не заявлять PASS без лога.
