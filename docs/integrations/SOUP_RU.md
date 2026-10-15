# Soup: локальное обучение планировщика Bossman

Upstream: https://github.com/MakazhanAlpamys/Soup
Версия: **0.76.0**, проверенный upstream SHA: `5349282ef22d1d6bece5e88fdfaf9689f2c538b4`.
Лицензия upstream: Apache-2.0. Код Soup не скопирован в Bossman.
Документация: https://trysoup.dev/docs/configuration

## Подключение

Отдельный Python 3.10–3.12 venv, не runtime сервера:

```powershell
py -3.12 -m venv .venv-soup
.\.venv-soup\Scripts\python.exe -m pip install "soup-cli==0.76.0"
# Только когда потребуется реальное обучение и подтверждена совместимость GPU:
.\.venv-soup\Scripts\python.exe -m pip install "soup-cli[train]==0.76.0"
$env:BOSSMAN_SOUP_PYTHON = (Resolve-Path .\.venv-soup\Scripts\python.exe).Path
```

Переменная должна быть задана процессу backend при запуске. При отсутствии extra сервер работает;
`GET /api/training/soup/status` честно возвращает NOT_INSTALLED/CLI_ONLY/DEPENDENCIES_PRESENT.
Последнее означает наличие пакетов, а не доказанную совместимость GPU. `training_ready` остаётся false.
Пакет не добавлен в обязательные зависимости Windows-архива. Адapter поставляется с bcc.

## Общая очередь и разрешения

В существующем tool registry доступны `training.soup.status` и `training.soup.prepare`.
Параметры prepare: `base` — локальная папка Transformers-модели, `train` и `holdout` — локальные JSONL.
Все входы должны быть в разрешённых корнях terminal или собственной task scratch; соседние scratch закрыты.
Каждая строка: `{"source_id":"исходная-сессия", "messages":[{"role":"user","content":"задача"},{"role":"assistant","content":"проверенный план"}]}`.
source_id разделяют по исходной сессии/видео, не по отдельному кадру. Общие source_id и точные дубли между
train/holdout отвергаются. Семантические дубли/истинность target требуют независимого ревью.

Prepare пишет в собственную scratch: train.jsonl, holdout.jsonl, soup.yaml и manifest.json с хешами.
Holdout НЕ передаётся тренеру; внутренняя случайная validation Soup не заменяет независимую проверку.
Возвращаются doctor_argv/train_argv для `python -m bcc.soup_training doctor|train`.
Wrapper повторно проверяет SHA256 manifest и входов, локальную модель и фиксированную версию Soup.
Выполнение — только существующим `terminal.run` в разрешённой рабочей папке после approval,
`network=false`; wrapper задаёт HF_HUB_OFFLINE=1, TRANSFORMERS_OFFLINE=1 и WANDB_DISABLED=true
и не передаёт дочернему процессу ключи провайдеров из окружения Bossman.
Запуск не автоматический. Сессию наблюдать через terminal.status и останавливать через terminal.stop.
Не использовать интерпретатор Soup как универсальный обход политики terminal; его argv остаётся ASK.
Хеш manifest должен оставаться в одобренной команде; изменение входов после подготовки отвергается.
Модель/токенизатор должны быть полностью локальны; custom remote code с auto_map отклоняется.

## Genjutsu и ограничения

Это обучение **LLM-планировщика**: выбор масок, инструментов, параметров и правил обработки отказов.
Soup здесь не заявлен как тренер Wan/VACE/FaceFusion: видео-LoRA требует собственного совместимого тренера.
Синтетические ответы облачных агентов не являются проверенными targets, улучшением видео или обученными весами.
Личные фото/видео не отправляются в облако этим адаптером. Он не запускает платные GPU или запросы.
Новые веса остаются кандидатом; для внедрения нужны baseline, независимый holdout, проверка рестарта и
доказанный перенос. Stable модель автоматически не заменяется. North Star статус этим подключением не повышается.
