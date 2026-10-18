# Resource governor (BossmanOne-6-Governor)

Отдельный процесс (не бэкенд :8801, его не перезапускает). Опрос `GET /api/direct-gen/jobs` каждые 10 с. Лог: `%LOCALAPPDATA%\Bossman\CommandCenter\one-bossman\governor.log` (ротация), статус: `governor.py status`, настройки: `one-bossman\governor.json` (`governor.py init` создаёт файл с умолчаниями).

## Что делает
**Простой (нет job queued/loading/generating/postprocessing, >=60 с тишины):** читает веса моделей, которыми владелец реально пользуется (ранг по журналу jobs), в файловый кэш ОС. Низкий приоритет потока, подсказка I/O VeryLow, SEQUENTIAL_SCAN; в VRAM ничего не грузится. Не больше `max_models`, не больше `max_bytes`, RAM после чтения >= 30 ГБ. Мгновенно прекращается при появлении job. «Всегда горячие» модели (`hot_models`, по умолчанию qwen-image-2.1-uc-edit) проверяются замером (3 пробных чтения), при скорости < 1000 МБ/с перечитываются.
Память: Windows считает standby-кэш как available, поэтому `available` при прогреве не падает (измерено: +16 ГБ кэша, available 94.9 -> 95.1 ГБ); реальный рост виден в `cache_gb`.

**Во время генерации:** выгружает все модели Ollama (`keep_alive:0`) и держит выгруженными; sd-cli -> High; прочие опознанные фоновые python-процессы Bossman -> BelowNormal + низкий I/O (бэкенд, мобильное, пульт, Jeff, Codex/ChatGPT не трогаются); Jeff по режиму ниже. После последней job всё возвращается; локальный Jeff — только после `resume_delay_s` (120 с) простоя.

## Jeff во время генерации (`jeff_during_generation`)
- `cloud_free` (по умолчанию): Jeff отвечает бесплатной облачной моделью, локальная модель не грузится вообще. Маршруты по порядку: Experiential Labs (провайдер 13, id 100/101, бесплатны на плане владельца), затем OpenRouter `:free` (живой каталог, цена ровно 0/0). Платная gemini-4-argon (id 99) исключена. Первый ответ помечается «(облако, бесплатно)». ВАЖНО: тексты чатов уходят провайдеру; бесплатные облачные модели могут отказывать на «uncensored»-запросах; `venice/uncensored:free` в каталоге OpenRouter больше нет (проверено 18.10); Exp Labs `...-uncensored` — не доказано, флаг шлюза uncensored=false. Нужно `/privacy remote on` у участника (у всех 13 сейчас включено).
- Защита стоимости: если ответ сообщает `usage.cost > 0`, ответ отбрасывается, маршрут закрывается (`generation-cloud-violation.json`), governor уходит в `pause` на `cloud_ban_s` (1 ч).
- `pause`: локальный Jeff останавливается через `owner_one_bossman.py stop --kinds jeff --hold`, возвращается явным `launch`; чужой/изменённый hold не трогается.
- `cloud` (платные DeepSeek/GLM) и `cpu_small`: ЗАГЛУШКИ, не включены, падают в `pause`.
Механизм: governor пишет `pit-v1.7\generation-cloud.json` с heartbeat (TTL 300 с). `bcc/pit/config.py` читает его только если он свежий и валидный, иначе действует правило «Jeff только локально» от 17.10. Мёртвый governor -> облачные ответы прекращаются сами; локальная модель при этом не поднимается. Состояние переключения хранится в `governor-state.json`: после падения governor следующий запуск откатывает или продолжает его.
`restore_owner_hold=false`: старый hold-jeff после генерации не возвращается (владелец хочет Jeff).

## Запуск
`powershell -File tools\resource_governor\register_task.ps1` (логон-задача `BossmanOne-6-Governor`, включена, dry_run=false). `python governor.py run --dry-run --console` — только лог решений. `python governor.py measure <файл>` — холодное/тёплое чтение.
Тесты: `python -m pytest tools/resource_governor/test_governor.py` и `command-center/tests/test_jeff_generation_cloud.py`.

## Известные ограничения
- Диск при фоновом приоритете даёт ~50-100 МБ/с (холодное чтение 1.2 ГБ: 66 МБ/с; повторное из кэша: ~10 ГБ/с).
- Кто-то в бэкенде :8801 примерно каждые 11 минут отправляет в Ollama `/v1/chat/completions` с отменой (499) и грузит qwen3.6-35b на GPU; governor выгружает модель после каждого такого захода, но источник не найден и не остановлен.
- Модели-«думающие» (DeepSeek на Exp Labs) тратят токены на рассуждения; для них `max_tokens` поднят до >= 2048.
