# Viggle Qwen-Image-2.1 Turbo: owner-run status

**Дата проверки статуса:** 2026-10-02  
**Целевая машина:** Ryzen AI Max+ 395 / Radeon 8060S gfx1151 / 128 GB unified memory / Windows 11  
**Статус:** SPECIFICATION; OWNER GENERATION NOT RUN

## Профиль и назначение

Кандидат — `Viggle/Qwen-Image-2.1-viggle-turbo`, адаптер v0.3 `Qwen-Image-2.1-viggle-turbo-v0.3-6step-lora-r256.safetensors`. Это локальный профиль генерации изображений и instruction editing с 1–3 референсами. Это не чат-, видео- или animation-модель.

Предлагаемые профили:

- `VIGGLE_FAST`: 6 шагов, sigmas `[1.0, 0.9375, 0.875, 0.75, 0.5, 0.25]`, guidance 1.0.
- `VIGGLE_QUALITY`: 9-шаговый гибрид только в поддерживаемом runtime.
- `VIGGLE_EDIT`: 6 шагов, 1–3 локальных референса.
- `QWEN_BASE_FALLBACK`: существующий проверенный профиль для сложных правок после подтверждения владельца.

Пять шагов допустимы как отдельный экспериментальный preset только с расписанием, указанным в официальной карточке; он не считается принятым Fast-профилем без прямого сравнения. Произвольные шаги и CFG не открывать в простом UX.

## Runtime и безопасность

Первичный Windows-кандидат — изолированный WanGP AMD runtime. TheNoise остаётся кандидатом, пока его запуск и benchmark на Windows владельца не подтверждены. Linux upstream latency не является owner evidence.

- Image service должен слушать только `127.0.0.1`.
- LOCAL_ONLY и PRIVATE не отправляются в облако; fallback только с явным разрешением владельца.
- Ссылки/пути к референсам проверять по разрешённому media workspace; удалённые URL и traversal запрещать.
- Сначала сохранять в каталог job, проверять артефакт, затем переносить в библиотеку.
- STOP должен запрашивать cancellation; неопределённый результат — `CANCEL_UNKNOWN`, поздний файл изолируется.
- До отдельной коммерческой лицензии профиль только для некоммерческой оценки: commercial use блокируется до загрузки модели.

## Owner-run status на 2026-10-02

В этой сессии local execution/Computer Use не запустился, поэтому не удалось проверить установленный runtime, наличие весов, свободное место и состояние GPU. Генераций: **0**. Фото в Telegram: **0**. Владелец-ПК benchmark: **NOT RUN**, не PASS. Перед загрузкой весов сначала проверить дисковое место и ресурсы, затем выполнить один runtime-only smoke test и лишь потом UX/CMD job.

## Приёмка

Для каждого теста сохранять точные runtime/model revisions и SHA-256, профиль/шаги/sigmas/seed, размеры, времена, память, output SHA-256 и статус верификации. Проверить декодирование картинки, MIME, размеры, наличие sidecar manifest, сохранность исходного референса и чтение после остановки worker.

Сравнить последовательно: существующий Bossman Qwen Image, Viggle 6-step, Viggle 9-step и base fallback. Набор включает RU/EN text-to-image, текст в изображении, background replacement, identity-preserving edit, 2–3 references, STOP при загрузке/денойзинге, restart/recovery, отсутствие egress для LOCAL_ONLY и отзывчивость Bossman/Jeff. Холодный прогон плюс три тёплых на случай. Не сообщать upstream latency как результат этой машины.

Артефакты после реального owner-run: `artifacts/viggle_turbo/<run-id>/manifest.json`, `benchmark.json`, `network_audit.json`, `outputs/` и `docs/evidence/VIGGLE_TURBO_OWNER_RUN_<date>.md`. Сертификат возможен только для exact Bossman SHA, к которому привязаны результаты.

## Источники

- [Official Viggle card, files, schedules and license](https://huggingface.co/Viggle/Qwen-Image-2.1-viggle-turbo)
- [WanGP AMD installation](https://github.com/deepbeepmeep/Wan2GP/blob/main/docs/AMD-INSTALLATION.md)
- [TheNoise setup](https://github.com/lemonade-sdk/thenoise/blob/main/docs/setup.md)
- [TheNoise Qwen Image 2.1 benchmark profile](https://github.com/lemonade-sdk/thenoise/blob/main/docs/models/qwen-image-2.1.md)
- [TheNoise API](https://github.com/lemonade-sdk/thenoise/blob/main/docs/api.md)

