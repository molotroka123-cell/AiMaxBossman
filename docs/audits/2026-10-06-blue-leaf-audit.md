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
| covered | 412 | тесты, импортирующие модуль, прошли на этом прогоне |
| covered-skipped | 2 | тесты есть, но не выполнялись (пропущены) |
| untested | 60 | ни один тест не импортирует этот исходник |
| non-python | 1 | не Python-источник, автотест не сопоставлен |

## Чего аудит НЕ доказывает (важно)

* **Польза не доказана ни для одного листа.** Для «доказанной пользы» нужны задача, базовый уровень, метрика и повторяемое сравнение без регрессий; этого в аудите нет.
  `covered` — это только «код загружается и тесты по нему проходят» (регрессионное покрытие), не «Bossman стал лучше».
* Прогон один, в облаке; не прогон на ПК владельца и не живая работа с моделями.
* Тест, импортирующий модуль, не обязательно проверяет его поведение. Совпадение по импорту — оценка сверху.
* Статусы листьев на дереве **не менялись**; цвет «проверено» без настоящего прогона не ставится.

## Решения по листьям (механические, по вердикту)

* `covered` (412): **оставить в продукте**, дальше измерять пользу на задачах с базовым уровнем (не сделано).
* `untested` (60): **доработать — добавить тест** до любых выводов (список ниже). За этот проход закрыт `bcc/pit/secret_filter.py` (15 тестов) — он учтён уже как покрытый.
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

## Листья без единого теста (60)

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
| memory | `module-fc4458307ec6` | `bossman-core/bossman/context_engine/compact.py` |
| memory | `module-5da7135e967e` | `bossman-core/bossman/context_engine/memory.py` |
| memory | `module-35d5cb7d9ebd` | `bossman-core/bossman/context_engine/retrieval.py` |
| memory | `module-54de14cfacf9` | `bossman-core/bossman/context_engine/telemetry.py` |
| memory | `module-74d93ed6921a` | `bossman-core/bossman/dev_factory/evidence.py` |
| memory | `module-d33b374941cd` | `bossman-core/bossman/learning_guard/service.py` |
| ops | `module-5386412e9f11` | `bossman-core/bossman/ai_lab/sanitizer.py` |
| ops | `module-73976a1cf54d` | `bossman-core/bossman/apprentice/_bootstrap.py` |
| ops | `module-771a003071ca` | `bossman-core/bossman/benchmark/fixture_runtime.py` |
| ops | `module-d3f5977af5ba` | `bossman-core/bossman/benchmark/sandbox_row.py` |
| ops | `module-9f29d5a73f7f` | `bossman-core/bossman/benchmark/sandbox_runtime.py` |
| ops | `module-6e615a668c7b` | `bossman-core/bossman/cost_control/routes.py` |
| ops | `module-df0754d72d22` | `bossman-core/bossman/cost_control/subsystem.py` |
| ops | `module-904a8396aa6c` | `bossman-core/bossman/dev_factory/executor.py` |
| ops | `module-7280885daae5` | `bossman-core/bossman/dev_factory/routes.py` |
| ops | `module-725a466f4b8f` | `bossman-core/bossman/notifications/bridge.py` |
| ops | `module-45ef5a5cbb02` | `bossman-core/bossman/notifications/routes.py` |
| ops | `module-aaff14c2d261` | `bossman-core/bossman/notifications/subsystem.py` |
| ops | `module-531fc123cd17` | `bossman-core/bossman/profiles/subsystem.py` |
| ops | `module-5bfdc6603e86` | `bossman-core/bossman/remote_client/subsystem.py` |
| ops | `module-cb1aad058144` | `bossman-core/bossman/research/models.py` |
| ops | `module-0ef2333ef23c` | `bossman-core/bossman/resource_brain/brain.py` |
| ops | `module-22827787ecc5` | `bossman-core/bossman/resource_brain/models.py` |
| ops | `module-fe0ffb0d2f88` | `bossman-core/bossman/resource_brain/routes.py` |
| ops | `module-5fcedb9e4e99` | `bossman-core/bossman/resource_brain/subsystem.py` |
| ops | `module-abd99037a01e` | `bossman-core/bossman/sandbox/dataset.py` |
| ops | `module-43a773f7d5a6` | `bossman-core/bossman/sandbox/manager.py` |
| ops | `module-a11338c485f5` | `bossman-core/bossman/sandbox/resources.py` |
| ops | `module-1622458b008a` | `bossman-core/bossman/sandbox/routes.py` |
| ops | `module-0dc288edd324` | `bossman-core/bossman/sandbox/toolbox.py` |
| ops | `module-e11418351fd7` | `bossman-core/bossman/search_everything/connectors.py` |
| ops | `module-5579b793e038` | `bossman-core/bossman/search_everything/service.py` |
| ops | `module-76ae682ed2e4` | `bossman-core/bossman/search_everything/tools.py` |
| ops | `module-59f6fbe27bd5` | `bossman-core/bossman/toolkit/_proc.py` |
| ops | `module-ae9598734759` | `bossman-core/bossman/toolkit/office.py` |
| ops | `module-a71668fad443` | `bossman-core/bossman/video_factory/routes.py` |
| ops | `module-f21597ba1fd6` | `bossman-core/bossman/video_factory/subsystem.py` |
| ops | `mod-agentmap` | `command-center/bcc/features/agentmap.py` |
| ops | `mod-benchlab` | `command-center/bcc/features/benchlab.py` |
| ops | `mod-jeff_master_parser` | `command-center/bcc/features/jeff_master_parser.py` |
| ops | `mod-opencode` | `command-center/bcc/features/opencode.py` |
| ops | `mod-oss_integrations` | `command-center/bcc/features/oss_integrations.py` |
| ops | `mod-owner_input` | `command-center/bcc/features/owner_input.py` |
| ops | `mod-workflow` | `command-center/bcc/features/workflow.py` |
| plugins | `plugins-oss` | `command-center/bcc/features/oss_integrations.py` |
| pv | `pv-eval` | `apps/poker-vision/pokervision/eval/run_eval.py` |
