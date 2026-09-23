# Bossman в терминале (1.2) — инструкция владельца

Терминал — **ещё один пульт того же Bossman**, а не второй Bossman. Задачи, память, навыки,
модели, разрешения (approvals), STOP и журнал — общие с вебом и Telegram. Терминал ничего не
учит сам, не хранит свою память и не одобряет действия за вас.

Статус: код и тесты на Linux (CONTRACT_PASS с тестовой моделью MOCK_MODEL). Проверка на вашем
Windows — отдельный шаг (см. «Что ещё не сделано»).

## Как запустить

| Что | Как |
|---|---|
| Разговор (интерактивно) | `Bossman-CLI.cmd` (или `Bossman-Terminal.cmd`) в папке архива; из cmd — `bossman.cmd chat` или просто `bossman.cmd` |
| Для Claude Code и скриптов | `bossman.cmd -p "…" --output-format stream-json` или `bossman.cmd exec --input-file задание.json` |
| Из исходников | `python -m bcc.terminal_cli …` или `bossman …` (точка входа bossman-core) |

Bossman должен быть запущен (`Start-Bossman.cmd`). Если он не отвечает, `bossman chat` спросит
«Запустить Bossman сейчас?» и поднимет штатный backend (`python -m bcc.app`) в фоне; можно и явно:
`bossman start`. Терминал сам находит тот же экземпляр: адрес из `desktop.lock`, `BCC_PORT`
(8800) или `--url`; каталог данных — `BCC_DATA_DIR` или `--data-dir`. Токен читается из файла
`token` каталога данных и **никогда** не печатается и не передаётся в командной строке.

Старые команды `bossman serve | task | project | models` (Bossman Core) работают как раньше.

## Разговор: `bossman chat`

Одна прокручиваемая колонка: заголовок «Bossman … — CLI Operator» со строкой статуса
(Mode, Model (local/cloud, MOCK_MODEL если тестовая), Approvals, Memory — заполненность контекста,
только если backend её сообщил, Computer Control), дальше беседа `You ›` / `Bossman ›`.

* `● tool(аргументы)` и под ним `⎿ ✓ итог (время)` — реальные вызовы инструментов.
* `✻ …` приглушённо — рассуждение модели, **только если модель его прислала**. Нет — строка
  «думает… / ожидаю модель» с настоящим временем, без выдуманного текста. Длинное — свёрнуто,
  `/expand N` разворачивает.
* `◆ память: N записей`, `◆ навыки: …` — только то, что сообщил backend.
* `⚠ требуется разрешение #id` — `[y]` да, `[n]` нет, `[d]` подробнее, `[Enter]` — решить позже в
  вебе/Telegram. Решение в вебе/Telegram закрывает вопрос само. Терминал никогда не одобряет сам.
* После задачи — строка итога: `── PASS ─ задача 12 · run 34 · 2м14с · 12.4K→2.1K tok · $— …`.
  Неизвестная стоимость — «—», не $0.

Enter — отправить; Tab — дополнение команд и путей; многострочная вставка — одно сообщение
(с prompt_toolkit; без него — простой ввод). История ввода — `<данные>\terminal\history.txt`,
секреты вырезаются; выключить: `/history off`, `--no-history` или `BOSSMAN_TERMINAL_HISTORY=0`.

Команды: `/help /status /tasks /models [use X] /agent [X] /skills [запрос] /tools /memory <запрос>
/diff [id] /code --allow <путь> [--verify <тест>] <задача> /approve [id] /deny [id] /approvals
/pause /stop [id|all] /resume /computer [status|stop|resume] /evolve [status|pause|resume|stop|report]
/keys /panel /expand [N] /history [on|off] /clear /exit`.

* `/models use X` меняет модель **текущего агента** — это постоянная настройка агента, видна в вебе.
* `/panel` — панель контекста по запросу: Workspace, Active Task, Tools Enabled, Memory (summary),
  Budget (session), Model.
* Беседа: каждый ход — обычная задача Bossman; в следующий ход терминал добавляет последние 3 хода
  (их ответы берутся из Bossman). Продолжить позже: `bossman resume <id сессии>`.

### Ctrl+C и STOP

* В строке ввода: очищает ввод; дважды на пустой строке — выход.
* Во время задачи: **просит Bossman отменить задачу** и ждёт его подтверждения
  («остановлена (подтверждено Bossman)»). Второй Ctrl+C — отсоединиться (задача в backend).
* Выход из чата **не останавливает** задачи — терминал говорит об этом перед выходом.
* Глобальный STOP: `/stop all` или `bossman stop --all` — все активные задачи, STOP управления
  компьютером, отмена coding-задач.

## Headless: Claude Code и скрипты

```text
bossman -p "Что ты знаешь о проекте?" --output-format stream-json --agent "LAB · TOOL_FIRST" --max-seconds 2400 --approval-mode wait
bossman exec --input-file task.json            (JSONL в stdout; схема входа ниже)
bossman exec --input-file task.json --detach   (сразу вернуть task_id)
bossman status --json
bossman events <task_id> [--after <cursor>] [--follow]
bossman result <task_id> --json
bossman approve <id>   |   bossman deny <id>
bossman stop <task_id> |   bossman stop --all
bossman pause <task_id> | bossman continue <task_id>
bossman list models|agents|skills|tools|tasks|approvals --json
bossman code "почини сложение" --allow calc.py --verify test_calc.py --json
bossman evolution status --json
```

Вход `exec` (UTF-8, до 256 КБ; другой формат — отказ с кодом 2; простой текст — `--text`):

```json
{"schema": "bossman.exec.v1", "prompt": "…", "title": "…", "agent": "имя или id",
 "model": "alias", "request_id": "cli-…", "wait": true, "max_seconds": 2400,
 "approval_mode": "fail", "on_timeout": "stop"}
```

`request_id` — ключ идемпотентности: повтор после обрыва связи возвращает **ту же** задачу и не
запускает второй прогон. По умолчанию `approval_mode=fail` (headless не зависает), `max_seconds=2400`.

Вывод `stream-json` — по одному JSON на строку, у всех `"v": 1`: `system/init` (подключение, сборка,
агент, модель + `model_kind` MOCK_MODEL/REAL_MODEL) → `task` → `step` → `thinking` (только настоящий)
→ `assistant` / `assistant_message` → `tool_use` / `tool_result` / `tool_denied` → `memory` / `skill`
→ `approval_required` / `approval_decided` → `usage` → … → **`result`** (всегда последний: `ok`,
`task_state`, `status`, `task_id`, `run_id`, `duration_ms`, `result`, `usage`, `model_kind`,
`cursor`, `exit_code`). В stdout нет ANSI, баннеров и секретов; диагностика — в stderr.

Обрыв потока: клиент переподключается с курсором (`seq`) и ничего не теряет и не дублирует;
если backend недоступен — `DISCONNECTED` (код 3) и `cursor`, дальше: `bossman events <id> --after <cursor> --follow`.

### Коды выхода

| Код | task_state / смысл |
|---|---|
| 0 | PASS (или операция удалась, например `--detach`) |
| 1 | FAIL — задача провалилась |
| 2 | ошибка аргументов/схемы входа |
| 3 | DISCONNECTED — Bossman недоступен, вход не выполнен, поток потерян |
| 4 | WAIT_APPROVAL — нужно решение владельца (`--approval-mode fail`); ничего не одобрено |
| 5 | BLOCKED — задача не принята (нет исполнителя/способности) |
| 6 | STOPPED — остановлена (STOP владельца, `bossman stop`, Ctrl+C) |
| 7 | TIMEOUT — истёк `--max-seconds` (задача остановлена или `--on-timeout detach`) |
| 8 | PARTIAL — задача на паузе/ждёт владельца вне approval |
| 9 | не найдено (задача/разрешение/провайдер) |
| 10 | недоступно в этой сборке (например, `/api/evolution`) |
| 11 | конфликт состояния (уже решено/уже завершено) |
| 130 | прервано Ctrl+C |

## Approvals

Терминал ничего не одобряет автоматически. В чате вопрос задаётся вам; в headless
`--approval-mode fail` завершает с кодом 4 (задача ждёт, разрешение остаётся открытым), `wait` —
ждёт решения в вебе, Telegram или `bossman approve <id>` из другого окна. `bossman approve` без
терминала требует `--yes`; в окружении Claude Code задайте `BOSSMAN_TERMINAL_NO_APPROVE=1` —
тогда одобрение из этого окна запрещено совсем. Решение записывается как `owner:terminal`.

## Ключи облачных моделей

Ключ хранит **Bossman** — зашифрованным (vault, как в вебе); он переживает перезапуск. Наружу
показывается только маска `…последние4`.

```text
set ANTHROPIC_API_KEY=sk-ant-…      (только в этом окне cmd)
bossman
  → «Найден ANTHROPIC_API_KEY в этом окне — сохранить в Bossman (зашифровано, сохранится после перезагрузки)? [Y/n]»
  → Y: провайдер заведён/обновлён, модели Claude зарегистрированы; /models use <модель>
```

* Отказ запоминается (только солёный хэш, не ключ) — повторно не спросит.
* Явно: `bossman keys set anthropic` (скрытый ввод), `--stdin` для скриптов;
  `bossman keys` — список (маски); `bossman keys remove anthropic`; `bossman keys import-env --yes`.
  Провайдеры: anthropic (CLAUDE_API_KEY тоже), openrouter, openai, gemini (GOOGLE_API_KEY тоже).
* **Не используйте `setx`**: он пишет ключ открытым текстом в реестр для всех программ.
* Ключ в командной строке (`bossman keys set anthropic sk-…`) не принимается: он остался бы в
  истории оболочки и списке процессов.
* Смена ключа — снова `keys set`; удаление — `keys remove`.

## Как Claude Code ведёт Bossman через терминал

1. `bossman status --json` — тот ли экземпляр (build_sha, data_dir), какие агенты/модели.
2. Задание: `bossman exec --input-file task.json` (или `-p "…" --output-format stream-json`),
   `request_id` — свой для каждой попытки; повтор после обрыва — тот же `request_id`.
3. Читать JSONL построчно; решать по `result.task_state` и `exit_code`, а не по тексту модели.
4. При `WAIT_APPROVAL` — **не одобрять самому**: сообщить владельцу id разрешения.
5. Наблюдение/повтор: `bossman events <task_id> --after <cursor> --follow`; итог: `bossman result`.
6. Остановить: `bossman stop <task_id>`; всё сразу — `bossman stop --all`.
7. MOCK_MODEL в `model_kind` — это проверка связки, не качество модели.

## Что ещё не сделано (честно)

* Windows-запуск не проверен на вашей машине (ConHost/Windows Terminal, кириллица в cmd,
  вставка, Ctrl+C) — это приёмка TR-01/02/04 из docs/terminal.
* `prompt_toolkit` пока не в Windows-архиве (lock надо перезаписать); без него чат работает с
  простым вводом, и многострочная вставка в cmd может уйти построчно.
* Посимвольного стриминга ответа нет: текст и рассуждение приходят целиком после каждого шага модели.
* `bossman repair --self` и `/evolve` зовут `/api/evolution/*`; если в сборке его нет — «недоступно
  в этой сборке» (код 10). `bossman run automation` и `bossman evolve --lab` — минимальные, без
  отдельного вида «Action queue» / двух колонок вариантов.
* Нет instance ID в /api/identity: тот же экземпляр подтверждается токеном каталога данных и build SHA.
* `--cwd` не меняет рабочую папку обычной задачи (её задают агент и корни); влияет на `/code`
  и заголовок.
