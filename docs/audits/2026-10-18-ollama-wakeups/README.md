# Фантомные пробуждения Ollama каждые ~11 минут (18.10.2026)

## Симптом
В `%LOCALAPPDATA%\Ollama\server.log` примерно каждые 10-11 минут (06:55, 07:12, 07:24, 07:35, 07:47, 07:58, 08:10, 08:21, 08:32,
08:43, 09:05, 09:16, 09:37, 09:48, 10:00 ...) идут пары `POST /v1/chat/completions` длительностью ~42 с (иногда ~22 с) со статусом
**499** (клиент закрыл соединение) или 200. Перед каждым 499 Ollama пишет `client connection closed before llama-server finished
loading, aborting load` и `Load failed ... timed out waiting for llama-server to start: context canceled`. Перед этим планировщик
Ollama выгружает чужие модели (`model predicted to exceed available memory, evicting`), т.е. мешает генерации картинок.

## Причина (подтверждена чтением кода, не догадкой)
`command-center/bcc/features/model_freshness.py` - тик «свежесть моделей» (фича `model_freshness`):

* `TICK_SECONDS = 600` (10 мин) + длительность цикла (до `MAX_PER_CYCLE = 3` проб по `PROBE_TIMEOUT_S = 40` с) -> период ~10:50-11:00.
* каждая проба - `Registry.test_model` -> `adapter.chat(...)` -> `POST /v1/chat/completions` на локальный Ollama;
* для локальной модели, которой нет в памяти, это **загружает её на GPU** (десятки ГБ, 35-45 с); проба обрывается
  `asyncio.wait_for(..., 40 с)`, клиент закрывает соединение -> Ollama пишет **499** (длительность 41-43 с = 40 с таймаут + накладные);
* после неудачи запись здоровья становится `timeout`, на следующем тике модель снова «просрочена» -> цикл повторяется бесконечно.

Совпадение с логом: длительность 499-запросов 41.0-46.2 с (таймаут 40 с); модели в паре - `bossman-fast-qwen36-35b-a3b-q5`,
`bossman-fast-qwen36-vision`, `bossman-main-qwen38-27b-q5`, `gemma4-31b-uncensored` (локальные модели из таблицы `models`,
используемые агентами или здоровые за последние 14 дней - критерии `_candidates`). Других таймеров с периодом ~11 мин и таймаутом 40 с
в `bcc` нет (`model_guard` CANARY=300/90 с, `autonomy`, мобильные статусы не обращаются к `/v1/chat/completions`).

## Исправление (малое и безопасное)
`model_freshness.run_cycle`: пока direct-gen держит GPU (`svc.direct_gen._running` или очередь `_waiting` не пусты), **локальные модели
не пробуются** (причина в `skipped`: `gpu_job_active_local_not_probed`); облачные/бесплатные пробы идут как раньше. Тест:
`command-center/tests/test_model_freshness.py::test_local_model_is_not_probed_while_a_direct_gen_job_holds_the_gpu`
(с негативным контролем: без GPU-задачи та же модель пробуется).

Вступает в силу после перезапуска backend :8801.

## Что осталось (не сделано сознательно)
1. Между задачами (GPU свободен) проба холодной локальной модели по-прежнему грузит её и обрывается на 40 с (499). Предложение:
   для `kind == local` сначала спрашивать `GET /api/ps` Ollama (не загружает модель) и пробовать только уже загруженную модель; для
   невыгруженной - записывать «не загружена, проба не делалась» вместо `timeout`. Либо `BOSSMAN_MODEL_FRESHNESS=0` на время работы.
2. Генерация вне direct-gen (ComfyUI напрямую) этим сигналом не видна.
3. Немедленный обходной путь без правки кода: переменная окружения `BOSSMAN_MODEL_FRESHNESS=0` (требует рестарта backend).
