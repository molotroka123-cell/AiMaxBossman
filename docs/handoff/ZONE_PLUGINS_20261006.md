# Зона `plugins` («Плагины и коннекторы») — handoff 2026-10-06

Ветка `zone/plugins-20261006` от `4bad4bd2` (integrate/bossman-2.1-one-20261006).
Python 3.12, Windows 11. Статусы в `capability_tree_seed.json` НЕ менялись.

Контракт Terminal Run: это та же сборка Bossman (тот же backend, реестр
инструментов, политика, хранилище). Отдельного продукта нет. Положение на
лестнице North Star не меняется: здесь только исправления дефектов в коде
коннекторов, а не доказательство самообучения.

## 1. Листья зоны (26, все `code`)

Базовый прогон (до правок, на `4bad4bd2`): test_plugins_adapter 54, test_plugin_security 24,
test_v21_mcp 15, test_v22_fixes 9, test_v26_mcp_sdk_path 3, test_secrem_mcp_boundary 9,
test_bl110_mcp_delete_everywhere 2, test_feat_openrouter_owner_path 22,
test_secrem_sibling_sweep 43, test_capability_tree 20, test_feat_skills 7,
test_offline_mode 11, test_golden_missions 13. Всё passed, ни одного failed.

| Лист | Исходник | Существует | Тесты, которые его проверяют | Результат / находка |
|---|---|---|---|---|
| plugin-0 http.get | features/plugins.py | да | test_plugins_adapter (SSRF у хендлера), test_plugin_security | pass; **дефект**: битый URL → голый ValueError → исправлено `632c210f` |
| plugin-1 monitor.feed | features/plugins.py | да | отдельных нет (тот же хендлер, что у http.get) | pass через http.get; RSS не разбирается: отдаётся сырое тело |
| plugin-2 sql.read | features/plugins.py | да | test_plugins_adapter, test_plugin_security (mode=ro) | **дефект**: `#`/`%` в пути → другой файл и без mode=ro → `05bd066a` |
| plugin-3 obsidian.read | features/plugins.py | да | только confine_path-тесты и сценарий 91 | **дефект**: каталог/пустой путь → сырой PermissionError с абсолютным путём → `4166cb6a` |
| plugin-4 obsidian.write | features/plugins.py | да | test_plugins_adapter (реальная запись, побег, нет креда) | pass |
| plugin-5 mcp.tool_list | features/plugins.py | да | только регистрация/политика | заглушка; **дефект** (см. ниже) → `1f40371c` |
| plugin-6 mcp.tool_call | features/plugins.py | да | только политика (ask, destructive) | заглушка → `1f40371c` |
| plugin-7 ollama.chat | features/plugins.py | да | только декларация local-only | заглушка → `1f40371c` |
| plugin-8 openrouter.chat | features/plugins.py | да | test_plugins_adapter, test_feat_openrouter_owner_path (кред/статус) | заглушка → `1f40371c` |
| plugin-9 github.repo_read | features/plugins.py | да | test_plugins_adapter (нет креда → SKIP) | заглушка → `1f40371c` |
| plugin-10 github.issue_create | features/plugins.py | да | только политика | заглушка → `1f40371c` |
| plugin-11 gmail.search | features/plugins.py | да | только политика | заглушка → `1f40371c` |
| plugin-12 gmail.send | features/plugins.py | да | политика ask/destructive/no-replay | заглушка → `1f40371c` |
| plugin-13 calendar.search | features/plugins.py | да | нет | заглушка → `1f40371c` |
| plugin-14 calendar.create | features/plugins.py | да | только политика | заглушка → `1f40371c` |
| plugin-15 drive.search | features/plugins.py | да | нет | заглушка → `1f40371c` |
| plugin-16 drive.write | features/plugins.py | да | только политика | заглушка → `1f40371c` |
| plugin-17 telegram.status | features/plugins.py | да | нет | заглушка → `1f40371c` |
| plugin-18 telegram.send | features/plugins.py | да | только политика | заглушка → `1f40371c` |
| plugin-19 n8n.workflow_list | features/plugins.py | да | нет | заглушка → `1f40371c` |
| plugin-20 n8n.workflow_run | features/plugins.py | да | только политика | заглушка → `1f40371c` |
| plugin-21 browser.open | features/plugins.py | да | нет | заглушка → `1f40371c` |
| plugin-22 browser.form_submit | features/plugins.py | да | нет | заглушка → `1f40371c` |
| plugins-mcp | features/tools_mcp.py, v2/mcp_hub.py, v2/mcp_runtime.py | да | test_v21_mcp (настоящий stdio-сервер), v22, v26, secrem_mcp_boundary, bl110, golden_missions | pass; **2 дефекта** → `b05b69ea`, `5e76832d`; пробел в UI (см. §4) |
| plugins-security | plugin_security.py | да | test_plugin_security 24, плюс 13 зависимых наборов | pass; дефект битого URL → `632c210f` |
| plugins-oss | features/oss_integrations.py | да | **ни одного теста** до этой ветки | дефектов не нашёл; тесты добавлены `6f33b673` |

## 2. Что исправлено (сначала падающий тест, потом минимальная правка)

| Коммит | Что | До → после | Автор |
|---|---|---|---|
| `6f33b673` | тесты plugins-oss: инвентарь честный и без путей; граница загрузки речи | — → 12 passed (только тесты) | Claude |
| `1f40371c` | общий хендлер 18 коннекторов возвращал `error=False`, `ready=True`, хотя ничего не делал (после одобрения gmail.send модель видела «успех»). Теперь `error=True`, `performed=False` | 18 failed → 19 passed | Claude |
| `05bd066a` | sql.read: URI SQLite без percent-encoding; `#` в пути обрезал путь и отрезал `?mode=ro` | 3 failed → 4 passed | Claude |
| `4166cb6a` | obsidian.read по каталогу: сырой PermissionError с абсолютным путём vault | 4 failed → 5 passed | Claude |
| `b05b69ea` | MCP: сервер с именем из цифр; `_emit_failure`/`tick` помечали unhealthy ЧУЖУЮ строку (id=имя) | 2 failed → 3 passed | Claude |
| `5e76832d` | MCP: ручной `/call` давал 500/502 на битое тело и успевал запустить процесс сервера | 7 failed → 7 passed (422, процесс не поднят) | Claude |
| `632c210f` | validate_url: битый URL/порт → голый ValueError вместо PluginSecurityError | 12 failed → 13 passed | Claude |

Итоговые прогоны на ветке: наборы, импортирующие plugin_security/plugins (+ новые
тесты зоны): **466 passed**; MCP + golden missions + offline mode: **72 passed**.
`tools/skips_registry.py --check`: PASS (389, новых пропусков нет). `git diff --check`: чисто.
Существующие тесты не ослаблялись и не правились.

## 3. Задачи Bossman worker glm-flash (z-ai/glm-5.3-flash)

Источник: `evo-tree-src` @ `7b433b92` (файлы зоны совпадают с `4bad4bd2`). В задаче
давались падающий тест (дословно) и описание дефекта; verify_tests = этот тест.

| Задача | Дефект | Статус | sidecar.summary / ошибка | changed_files |
|---|---|---|---|---|
| `2c214f3a5abf` | sql URI | failed — «независимая проверка Bossman не прошла: exit=1» | `"..."`; run_tests сайдкара ×2 = error | только тест |
| `e5d9ad2f31d0` | obsidian каталог | failed — exit=1 | «Blocked: cannot run the required regression test in this runtime… run_tests… `No module named 'pytest'`… set BOSSMAN_VERIFY_PYTHON…»; правильный план правки, но код не применён | только тест |
| `f6a77a94431d` | MCP имя из цифр | failed — exit=1 | `"..."`; 7 вызовов run_tests без pytest | только тест |
| `eec82a42bfa6` | MCP тело /call | failed — «сайдкар сообщил о неудаче» | «stopped: 8 observation calls repeated with the same result and no file change in between» (no_progress_loop) | нет |
| `3e28547562d1` | битый URL | **не собрано**: последний опрос показал `running` | — | — |

**Ни один патч от GLM не принят: в ветке нет кода GLM.** Все исправления написаны Claude.
Дальше GLM не использовался: координатор остановил все вызовы `/api/coding-tasks`
(каждый вызов делает handshake, который грузит локальную модель Ollama на 27 ГБ в GPU
и мешает рендеру видео). Результат `3e28547562d1` поэтому не запрашивался.

Блокер, найденный по пути: инструмент `run_tests` в сайдкаре работает без pytest
(«these tests need pytest, which this runtime lacks… set BOSSMAN_VERIFY_PYTHON»).
Независимая проверка Bossman pytest находит, а сам воркер — нет. Поэтому GLM-воркер
по дисциплине TDD останавливается на красном тесте. Пока `BOSSMAN_VERIFY_PYTHON` не
задан для сайдкара (или pytest не ставится в его рантайм), задачи с pytest-тестами у
GLM будут проваливаться.

## 4. Что остаётся только `code` и почему

* **18 коннекторов-заглушек** (plugin-5…22): живого вызова внешнего сервиса нет вообще.
  Теперь они честно возвращают ошибку «НЕ выполнено». Чтобы стать рабочими, нужна
  реальная реализация каждого (OAuth Google, GitHub API, n8n, браузер, MCP-мост,
  Ollama/OpenRouter-путь) и живая проверка с разрешения владельца. В рамках зоны
  это не делалось: запрещены внешние действия и GPU.
* **monitor.feed** — это http.get под другим именем, ленту (RSS/Atom) не разбирает.
* **plugins-mcp** — протокол реально работает (stdio, официальный SDK 2.2.0, настоящий
  тестовый сервер). Но в UI есть пробел: `ui/pages/skills.js` добавляет сервер через
  `POST /api/mcp/servers` и нигде не вызывает `/api/mcp/runtime/servers/{id}/refresh`.
  Поэтому у сервера, добавленного владельцем, инструменты не обнаруживаются никогда
  (`restore_registry` поднимает только уже сохранённые в `mcp_tools`). Файлы
  skills.py/skills.js относятся к зоне `skills`, их я не трогал. Рекомендация: кнопка
  «Обнаружить инструменты» или вызов refresh сразу после добавления. Транспорт `http`
  проходит валидацию при добавлении, но рантайм его не поддерживает (только stdio).
* **plugins-oss** — инвентарь и граница загрузки проверены. Инференс Whisper в тестах
  намеренно не запускается: `inference_verified=False` остаётся честным.

## 5. Рекомендуемые изменения статусов (сид не правил)

* plugin-0 http.get, plugin-2 sql.read, plugin-3 obsidian.read, plugin-4 obsidian.write,
  plugins-security, plugins-oss: оставить `code`, но добавить в sources ссылки на новые
  тесты. Для повышения нужна проверка на машине владельца по точному SHA.
* plugin-5…22: понизить ниже `code` (например, `stub`/`planned`, если такой статус
  есть в status_semantics) или пометить в detail «адаптер-заглушка: живого вызова нет,
  отвечает NOT_TESTED_LIVE-ошибкой». Сейчас `code` создаёт ложное впечатление, что
  коннектор работает.
* plugin-1 monitor.feed: detail «= http.get, без разбора RSS».
* plugins-mcp: оставить `code`; next_action — дать UI вызов discovery (зона skills).
