# SELF-IMPROVEMENT UX LAB — чекпоинт 2026-09-22 (конец дня)

Остановлено по команде владельца («завтра продолжим»). Все процессы Bossman/моделей
остановлены, машина разгружена. Этот файл — точка продолжения.

## Сделано сегодня

### Фикс, найденный по дороге (PRODUCT_CODE)
Проба модели (`bcc/registry.py:test_model`) давала 32 токена; рассуждающая модель
(GPT-OSS-120B) тратит их на размышление, и Bossman записывал её как «модель ответила
пустотой» — то есть не пускал на неё задачи. Теперь при пустом ответе с `finish=length`
одна повторная проба с 1024 токенами; модель, молчащая при `finish=stop`, по-прежнему FAIL.
Регрессия `command-center/tests/test_model_probe_reasoning.py` (красная до фикса); соседние 61 passed.

### Базовая линия установленных текстовых моделей (llama.cpp b10964 Vulkan, ctx 32768)
| Роль | Модель | Квант | Порт | Статус |
|---|---|---|---|---|
| MAIN | Qwen3.8-27B | UD-Q5_K_M (19,8 ГБ) | 8081 | работает |
| MAIN-candidate | Qwen3.8-27B | UD-Q4_K_M (16,5 ГБ) — **скачан сегодня**, sha256 `322e194f…` сверен с HF | 8081 | работает |
| FAST | Qwen3.6-35B-A3B | UD-Q5_K_M | 8082 | не мерился сегодня |
| VERIFIER/REVIEW | GPT-OSS-120B | MXFP4 (2 части, ~63 ГБ) | 8083 | работает; **не было в Bossman** — зарегистрирован сегодня (модель id 3, alias `oss`) |
| — | Nex-N2.5-mini | Q4_K_M | 8084 (новая цель) | не мерилась |
| — | Occamy-1.0 | Q4_K_M | 8085 (новая цель) | не мерилась |

`start-models.ps1` получил цели `oss`, `nex`, `occamy` (снимок в этой папке). Все с `--jinja --reasoning off`;
GPT-OSS рассуждает всегда (флаг на него не действует).

### Model scout (кратко)
Benchmark-лидеры (Kimi K2.6, DeepSeek-V4-Pro, GLM-5.3) — сотни ГБ, на 128 ГБ не помещаются.
Qwen3.8-Flash-Next (~177B MoE) — нужен собственный форк llama.cpp → не квалифицирован.
Best-for-this-machine: тот же Qwen3.8-27B, но Q4_K_M (гайд Strix Halo: ~20 т/с на Vulkan против наших ~10 на Q5).
Решение: **KEEP CURRENT** модели + **BENCHMARK CANDIDATE** Q4_K_M (скачан, не заменяет Q5).

### Bake-off A–G (`bakeoff.py`, одинаковые промпты, авто-проверка исполнением)
| Модель | Прошло | Время | Генерация | Провал |
|---|---|---|---|---|
| GPT-OSS-120B MXFP4 | **7/7** | **90 с** | **49 т/с** | — |
| Qwen3.8-27B Q5_K_M | 6/7 | 316 с | 10,0 т/с | F: после ошибки инструмента ушёл в `search_code` вместо исправленного пути |
| Qwen3.8-27B Q4_K_M | 6/7 | 239 с | 11,8 т/с | F: то же |

Замечание: Q4 дал +18 %, а не ×2 — наш llama.cpp b10964, не Ollama из гайда; измерено, не предполагалось.
FAST, Nex, Occamy — NOT_RUN.

**Предварительный LAB_PRIMARY: GPT-OSS-120B** (единственный 7/7, в ~3 раза быстрее по времени).
Не финализирован: нужен прогон FAST/Nex/Occamy тем же набором. Production-модель не менялась.

## Главная находка про UX Bossman (до запуска агентов)
У агентов command-center **нет инструмента правки файлов**: есть `code.search/expand` (чтение),
`terminal.run` (через подтверждение), `opencode.*` (мост v1, установлен OpenCode v2 — несовместим).
Штатный путь самоулучшения — **coding-задачи** (`bcc/features/coding_tasks.py`): одноразовый
изолированный клон без remote → sidecar по протоколу `bossman.openhands.v1` (JSON stdin/stdout) →
Bossman сам выводит diff и проверяет scope. Нужен sidecar в `BOSSMAN_OPENHANDS_COMMAND` — **не настроен**.

## С чего продолжить завтра
1. `start-models.ps1 -Target fast` → `bakeoff.py --tag fast`; затем `nex`, `occamy` (по одной). Финализировать LAB_PRIMARY.
2. Написать sidecar `bossman.openhands.v1` поверх LAB_PRIMARY (агентный цикл: read/search/edit/run_tests
   внутри клона), указать в `BOSSMAN_OPENHANDS_COMMAND` для LAB-экземпляра (не стабильного).
3. Агенты A–F (RAW, TOOL-FIRST, MEMORY, PLAN-VERIFY, RED-TEAM, USER-UX) = одна задача, разные инструкции,
   через coding-задачи, по 40 минут; CLAUDE-AUDITOR / RESULT VERIFIER / UX OBSERVER — как в ТЗ.
4. Verified lesson → рестарт → transfer-тест.

## Состояние машины на момент остановки
Остановлены: llama-server, Bossman :8800, Telegram-компаньон, тесты, Ollama. Свободно ~105 ГБ.
Bossman владельца: рабочая база `%LOCALAPPDATA%\Bossman\CommandCenter` (в ней сегодня добавлены
модель `oss` и агент «Решатель»); запуск — `C:\Users\asd\Bossman\start-bossman-src.ps1`.
Telegram-бот (с /claude и /codex) — `scripts\Start-TelegramCompanion.ps1 -RepoRoot C:\Users\asd\Bossman\wt-final -SkipChecks`.
Мост Codex (`codex-telegram`) выключен, автозапуск в `runtime\codex-telegram-disabled-20260922b`.
