# ТЗ: мелкие ошибки → архитектурный полиш → слияние в main (23.10.2026, Claude)

Директива владельца: «Сначала пропиши себе ТЗ устранения мелких ошибок, потом вноси архитектурные решения,
разгружай лимиты облачными моделями, только полиш общего кода, ничего не переписывай, ничего не спрашивай,
делай самые эффективные решения и сводим в main». Датасеты: включать по моей рекомендации.

Исходная точка: FINAL = `integrate/one-bossman-final-20261023` @ d356f4c7, живой `:8801` на 56b81f17. G1 на FINAL:
command-center 11665 pass, root 3934, war-terminal 260, mobile 242; красных 5 (ниже). Все изменения — полиш
существующего кода, ни один модуль не переписывается.

## Часть 1. Мелкие ошибки (каждая = ≤ 15 строк диффа, свой тест зелёный)

| # | Красный тест | Причина | Решение (полиш) | Кто |
|---|---|---|---|---|
| 1 | `test_mandatory_model_policy::test_no_active_route_or_default_names_a_liquid_model` | `bcc/free_models.py:33` держит литерал `"lfm-2.5-2.6b"`; политика запрещает любой литерал Liquid/LFM в рантайме | убрать литерал, фильтровать через существующий `bcc.pit.model_policy.banned_refusal(model_id)` (тот же хелпер, что в `provider_governance`) | Claude |
| 2 | `test_single_flight::test_no_module_went_back_to_asyncio_shield` | `bcc/features/agent_extras.py:383` использует `asyncio.shield` (на 3.14 отменяет общую работу) | заменить на `await_shared(asyncio.ensure_future(...))` ровно как в `bcc/direct_gen/service.py:1712` | Claude |
| 3 | `test_cu_participant_perimeter::test_no_pit_module_can_reach_the_engine_tools_or_core_client` | `bcc/pit/jeff_kb_cloud.py:166` импортирует `bcc.features.free_providers.is_chat_model` (pit не имеет права ходить в `bcc.features`) | перенести 4-строчный `is_chat_model` по id в `bcc/pit/model_policy.py` (pit-модуль, уже разрешён), `free_providers.is_chat_model` вызывает его для не-preset случая, `jeff_kb_cloud` импортирует из pit; дублирования нет | Claude |
| 4 | `test_code_mode_tokens::test_facade_cost_is_flat_while_the_baseline_grows_with_the_tool_count` | тест растит реестр до фиксированных 1000 инструментов и требует baseline > 8× малого; реестр вырос до >125 инструментов, 1000/125 = 8 — утверждение стало математически невыполнимым при любом коде | расти до `max(1000, 10 × len(реестра))`: смысл теста (линейный baseline, плоский фасад) сохранён, порог 8× не трогаем | Claude (тест) |
| 5 | `test_code_mode_tokens::test_search_listing_stays_small_and_finds_the_tools_of_the_scenarios` | `catalog.search` (BM25 + бонус за термин в имени) ставит новые `plugin:*.read` выше `browser.open` на запросе «open a web page and read its text» | бонус за покрытие нескольких разных терминов запроса в имени/описании; пороги теста не трогаем | Gemini 3 Flash (облако), Claude ревью |

Не красное, но в том же духе: эталонные решения бенча (`tests/test_llm_bench_tasks.py`) — задача для бесплатного
кодера на завтра, когда у Gemini обновится дневная квота (20 запросов/день на модель).

## Часть 2. Архитектурный полиш (без переписываний)

1. **Один рантайм = одно дерево.** Все pip-editable (`bossman-core`, `command-center`, `mobile`, `shared`,
   `war-terminal`), задачи автозапуска, governor и `--src` лаунчера указывают на одно рабочее дерево
   (сделано 23.10 для wt-final). Правило в `AGENTS.md`: при смене кандидата перенацеливать все пять точек, иначе
   `-I`-подпроцессы (сайдкар) работают на чужом коде.
2. **Воркеры-кодеры: честный отказ вместо сжигания шагов.** `MAX_NO_TOOL_CALL_TURNS=3` в сайдкаре (сделано);
   `nvidia-nim` на живой модели (сделано); auto-swap `:free` моделей (Argon). Политика выбора: Gemini — только на
   задачи, где нужен один-два прогона; локальный `qwen3-coder:30b` — правки тестов и 1–5-строчные фиксы; всё,
   что требует понимания архитектуры, — Claude (полиш) с явной пометкой в коммите.
3. **Датасеты (рекомендация, принята):** в веса идут только источники с владельческим происхождением —
   `jeff_kb`, `training_drops`, `passport_facts`, `lessons`, `owner_docs`; `web_learn`, `war_terminal`, `open_news`
   — только в RAG (`WEIGHTS_UNCHANGED`). Закреплено флагом `--for-weights` в `tools/training_secure_gate.py`.
4. **Гейт на слияние в main:** секрет-скан PASS, затронутые тестовые модули зелёные, G1 числа в коммите слияния,
   живой рантайм перезапущен на SHA main.

## Часть 3. Слияние и запуск

1. Коммиты полиша в FINAL, секрет-скан, тесты затронутых модулей.
2. `origin/main` (33ed54ee) — предок FINAL → fast-forward `main` на FINAL (директива владельца «сводим в main»
   = явное «да»; без force-push).
3. Перезапуск backend/Jeff/компаньона на SHA main, `source_identity PASS`.
4. Отчёт в пульт с числами и экономией; память обновлена.
