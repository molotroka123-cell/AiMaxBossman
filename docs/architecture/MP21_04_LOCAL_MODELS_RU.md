# 04. Локальные модели, offline-режим и приватность (сверено 11.10.2026)

Измерения и инвентарь машины: `docs/archive/2026-10/owner/MP21_LOCAL_MODELS_AUTONOMY_20261011.md` (read-only, ничего не скачивалось и не
загружалось). Здесь только то, что нужно знать для работы, плюс привязка к коду.

## 1. Железо

Одна машина: AMD RYZEN AI MAX+ 395, 16 ядер, 119.6 GiB общей памяти, ОДНА GPU Radeon 8060S (gfx1151), ROCm видит около
79.8 GiB общего пула. CUDA/NVIDIA нет. Мульти-GPU и распределённого инференса нет: в `llama-vulkan` лежат `ggml-rpc`
бинарники, но удалённый хост не настроен. Документ `docs/hardware/LOCAL_HARDWARE_INVENTORY.md` описывает ДРУГОЙ ноутбук
(i9-14900HX, RTX 4060) и к этой машине не относится.

## 2. Рантаймы

| Рантайм | Где | Состояние на 11.10 |
|---|---|---|
| Ollama 0.40.2 | `127.0.0.1:11434` | работает; 11 моделей, 141.3 GB |
| llama-server (ROCm, `--offline`) | процессы Ollama | 3 процесса, резидентны 35B-A3B Q5 (26 GB) и 0.8B decider |
| llama.cpp Vulkan b11223 | `C:\Users\asd\Bossman\llama-vulkan` | есть, не запущен; замер gemma3 27B Q8_0: pp64 220.8 t/s, tg32 6.47 t/s (единственный измеренный скоростной факт для этой GPU) |
| Адаптер OpenAI-совместимого API | `bcc/providers.py::OpenAICompatAdapter` | используется локальным smoke |

## 3. Лицензии (не выдумывать)

Лицензионный текст встроен только у 3 из 11 моделей Ollama (qwen3-coder:30b, qwen38-pi-ab, bytkim Qwen3.8-27B-pi: Apache 2.0).
Папки весов `C:\Users\asd\Bossman\models` (около 777 GiB, 20 папок) не содержат LICENSE. Для остальных лицензии НЕ
подтверждены локально; abliterated/uncensored пересборки наследуют условия базовой модели. Коммерческое использование
без проверки у источника недопустимо.

## 4. Offline и local-only

| Механизм | Код | Что делает | Статус |
|---|---|---|---|
| `GET /api/offline`, `/api/offline/can/{capability}` | `bcc/features/offline_mode.py` | отчёт: offline_ok / needs_network / unknown; ничего не блокирует | works (21 passed вместе с local-only) |
| `private` / `local_only` на запрос | `bossman_shared/privacy.py`, `engine.py` | запрещает облачные провайдеры для этого запроса | works |
| `BOSSMAN_LOCAL_ONLY=1` | `bossman_shared/privacy.py::local_only_switch`, `assert_provider_egress` | на всём процессе блокирует облачные виды (openrouter, anthropic, openai, gemini) и любой не-loopback/не-private хост на границе провайдера | works, только граница провайдера |
| Smoke | `tools/offline_smoke_local_only.py` | rc 0: облачные адаптеры отказали, локальный `ace-decider-08b` ответил за 2.9 с, 0 внешних соединений | works (один маленький чат) |

Дыры (именно они мешают сказать «полностью offline»): веб-поиск, браузерная навигация, Gmail, Telegram, обновление
каталога OpenRouter и скачивание моделей НЕ проходят через `assert_provider_egress`; `/api/offline` помечает их
`needs_network`. Флаг надо экспортировать в окружение backend ДО его старта: работающий :8801 его не имеет.

## 5. Выбор модели

Резидентный набор по памяти (арифметика размеров, не новый бенчмарк): Q5 27B + 35B-A3B + 0.8B decider помещаются вместе
(49 GiB запас); gpt-oss-120b (59 GB) один; 70B Q8 и deepseek-v4 Q2 на грани видимого пула. Качество 27B/35B этим smoke не
измерялось.

## 6. Чего не хватает

* Лицензии 8 из 11 моделей Ollama и всех папок весов.
* Реального замера скорости/качества основных моделей на этой GPU по единому протоколу.
* Полного local-only (поиск, Gmail, Telegram, загрузки).
