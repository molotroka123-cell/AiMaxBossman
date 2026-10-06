# Поэлементный аудит синих листьев «код написан» (06.10.2026)

Same-product Terminal Run: пульт, CLI, дашборд и Telegram — одна поверхность одного Bossman; этот аудит ничего отдельно не вводит.
Лестница North Star: достигнут только `SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT`. Аудит покрытия тестами её **не поднимает**.

## Что именно проверено

Листья со статусом `code` в `command-center/bcc/capability_tree_seed.json` на ветке `goal/bossman-self-improvement-tree-20261005` (после влива линии poker-vision/poker-lora):
**475 листьев**. Для каждого `tools/blue_leaf_audit.py` находит тестовые файлы, импортирующие его исходник (или загружающие скрипт по пути), и сверяет с
результатом настоящего прогона в облаке (Linux, Python 3.11): `bossman-core` 3446 passed / `command-center` 8691 passed / корень 2980 passed / poker-vision 129 passed (10 skipped) / poker-lora 23 /
ai-webcam-vision 224 passed. Все JUnit-файлы и инструмент воспроизводимы; данные по листьям — `docs/audits/blue-leaf-audit-20261006.json`.

| Вердикт | Листьев | Что это значит |
|---|---:|---|
| covered | 439 | тесты, импортирующие модуль, прошли на этом прогоне |
| covered-skipped | 2 | тесты есть, но не выполнялись (пропущены) |
| untested | 33 | ни один тест не импортирует этот исходник |
| non-python | 1 | не Python-источник, автотест не сопоставлен |

## Чего аудит НЕ доказывает (важно)

* **Польза не доказана ни для одного листа.** Для «доказанной пользы» нужны задача, базовый уровень, метрика и повторяемое сравнение без регрессий; этого в аудите нет.
  `covered` — это только «код загружается и тесты по нему проходят» (регрессионное покрытие), не «Bossman стал лучше».
* Прогон один, в облаке; не прогон на ПК владельца и не живая работа с моделями.
* Тест, импортирующий модуль, не обязательно проверяет его поведение. Совпадение по импорту — оценка сверху.
* Статусы листьев на дереве **не менялись**; цвет «проверено» без настоящего прогона не ставится.

## Решения по листьям (механические, по вердикту)

* `covered` (439): **оставить в продукте**, дальше измерять пользу на задачах с базовым уровнем (не сделано).
* `untested` (33): **доработать — добавить тест** до любых выводов (список ниже). За этот проход закрыт `bcc/pit/secret_filter.py` (15 тестов) — он учтён уже как покрытый.
* `covered-skipped` (2, UI покера): **доработать** — проверка вживую в браузере; в облаке e2e-тесты страницы запускались отдельно (2 passed), в полном прогоне пропущены.
* `non-python` (1): **отложить** до решения владельца.
* **Исключено: 0.** Дубликаты кода не искались; найдено другое: у **28** исходных файлов больше одного листа (109 листьев делят файл с другим листом — «возможность» и «модуль» на один и тот же файл).
  Это дубли в карте, а не в коде; свёртка карты — отдельная работа, без проверки на действующих сценариях не делается.

## Красные и пропущенные в этом прогоне — и почему

* `bossman-core`, 8 тестов бенчмарка (`ShaMismatch`): тесты запоминают SHA при загрузке и сравнивают с HEAD при выполнении; мой коммит посреди прогона давал расхождение.
  Повтор без коммитов: **17 passed** (дважды). Это свойство теста, не дефект продукта; правила репозитория (`run-isolated`) это и предполагают.
* `command-center`, `test_agents_deeplink_stale_modal`: реальная нестабильность теста (склейка одинаковых GET в `api.js` и запрос оболочки в полёте). Исправлено в `3adf779b`, повтор: **2 passed**; в CI это был единственный красный из 8138.
* `command-center`, `test_the_wheel_carries_the_interface` и `test_real_chromium_app_window_renders_command_center`: падают **только в этой песочнице** (нет предустановленного Chromium по пути `/opt/pw-browsers/chromium-1243`, ограничения сборки wheel). В CI эти два теста проходили; на ПК не проверялось.
* Незакрытые внешние гейты: `Intelligence Preservation` / `measured intelligence retention` (нужны парные замеры владельца), Windows-артефакт (ждёт раннера), самообучение (нужен ПК с ключами).

## Листья без единого теста (33)

Метод приписывает тест модулю, если тест его импортирует, загружает скрипт по пути или импортирует пакет, чей `__init__.py` реэкспортирует модуль (последнее добавлено после того, как первая версия аудита дала 60 «без теста»: `bossman/learning_guard/service.py` покрыт `test_learning_guard.py` через реэкспорт). Остаток — это оценка сверху: часть модулей может проверяться косвенно и всё равно значиться здесь.

| зона | id | исходник |
|---|---|---|
| agents | `cap-39` | `command-center/bcc/features/coding_sessions.py` |
| agents | `mod-coding_sessions` | `command-center/bcc/features/coding_sessions.py` |
| agents | `mod-nl_orchestra` | `command-center/bcc/features/nl_orchestra.py` |
| agents | `mod-organization` | `command-center/bcc/features/organization.py` |
| agents | `cap-38` | `command-center/bcc/features/rave.py` |
| agents | `mod-rave` | `command-center/bcc/features/rave.py` |
| computer | `cap-26` | `command-center/bcc/features/jev.py` |
| computer | `mod-jev` | `command-center/bcc/features/jev.py` |
| jeff | `module-d794cbd77cf3` | `command-center/bcc/pit/categories.py` |
| jeff | `module-4a1f6b8497c8` | `command-center/bcc/pit/master_parser/corpus.py` |
| jeff | `module-09f63bf64e58` | `command-center/bcc/pit/master_parser/passport_sink.py` |
| media | `cap-30` | `command-center/bcc/features/studio.py` |
| media | `mod-studio` | `command-center/bcc/features/studio.py` |
| media | `mod-studio_review` | `command-center/bcc/features/studio_review.py` |
| memory | `module-54de14cfacf9` | `bossman-core/bossman/context_engine/telemetry.py` |
| ops | `module-73976a1cf54d` | `bossman-core/bossman/apprentice/_bootstrap.py` |
| ops | `module-771a003071ca` | `bossman-core/bossman/benchmark/fixture_runtime.py` |
| ops | `module-d3f5977af5ba` | `bossman-core/bossman/benchmark/sandbox_row.py` |
| ops | `module-9f29d5a73f7f` | `bossman-core/bossman/benchmark/sandbox_runtime.py` |
| ops | `module-6e615a668c7b` | `bossman-core/bossman/cost_control/routes.py` |
| ops | `module-df0754d72d22` | `bossman-core/bossman/cost_control/subsystem.py` |
| ops | `module-725a466f4b8f` | `bossman-core/bossman/notifications/bridge.py` |
| ops | `module-59f6fbe27bd5` | `bossman-core/bossman/toolkit/_proc.py` |
| ops | `module-ae9598734759` | `bossman-core/bossman/toolkit/office.py` |
| ops | `mod-agentmap` | `command-center/bcc/features/agentmap.py` |
| ops | `mod-benchlab` | `command-center/bcc/features/benchlab.py` |
| ops | `mod-jeff_master_parser` | `command-center/bcc/features/jeff_master_parser.py` |
| ops | `mod-opencode` | `command-center/bcc/features/opencode.py` |
| ops | `mod-oss_integrations` | `command-center/bcc/features/oss_integrations.py` |
| ops | `mod-owner_input` | `command-center/bcc/features/owner_input.py` |
| ops | `mod-workflow` | `command-center/bcc/features/workflow.py` |
| plugins | `plugins-oss` | `command-center/bcc/features/oss_integrations.py` |
| pv | `pv-eval` | `apps/poker-vision/pokervision/eval/run_eval.py` |
