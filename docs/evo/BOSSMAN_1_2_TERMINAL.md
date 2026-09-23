# Bossman 1.2 — MOVE TO TERMINAL

**Цель 1.2:** владелец и Claude Code работают с Bossman из командной строки так же быстро и
удобно, как с Claude Code CLI и Codex CLI. Весь Bossman — модели, агенты, навыки, память,
coding path, управление компьютером, approvals, цикл самоулучшения 1.1 — доступен из
терминала. Веб-интерфейс остаётся и получает тот же язык (референсы ниже).

Терминал — **это новый способ управления, а не второй Bossman.** Учится и работает один и тот же
backend: те же задачи, та же память и LearningStore, те же approvals, бюджеты, LOCAL_ONLY и
журнал доказательств. Терминал и веб — два способа показать один поток событий.

Связанные документы: [North Star 1.1](BOSSMAN_1_1_NORTH_STAR.md) ·
[Цикл самоулучшения](../evolution/EVOLUTION_LOOP.md) · [Терминал — инструкция владельца](../owner/TERMINAL.md) ·
[Лаборатория](../owner/SELF_IMPROVE_LAB.md) · [Навыки](../owner/SKILLS.md).

---

## 1. Зачем

1. **Скорость.** Задача ставится одной строкой, результат виден сразу, без переключения страниц.
2. **Claude Code ведёт Bossman через терминал.** Claude Code запускает `bossman -p … --output-format
   stream-json`, читает события построчно и работает учителем и аудитором 1.1: наблюдает, при
   необходимости вмешивается по уровням 0–5 и записывает вмешательства. Сам Claude за ученика не
   работает: патч уровня 5 не засчитывается ученику.
3. **Меньше нагрузки на UI.** Тяжёлые прогоны (лаборатория, турнир моделей, цикл 1.1, soak) удобно
   гонять и наблюдать из терминала; веб остаётся для обзора и ручного контроля.

## 2. Принципы (обязательны)

* **Один backend.** Терминал не хранит свою память, не запускает свою модель и не обходит API.
  Всё, что сделано из терминала, видно в вебе и в Telegram, и наоборот.
* **Никакой выдуманной «мысли».** Блок «Thinking» показывает только то, что реально пришло от
  модели (reasoning-поток или план, который модель сама выдала). Если модель этого не отдаёт —
  индикатор «думает…», а не сочинённый текст. Скрытую цепочку рассуждений в память не пишем.
* **Approvals не ослабляются.** Терминал никогда не одобряет действие сам. Headless-режим либо
  ждёт решения владельца (веб, Telegram, `bossman approve`), либо завершается с отдельным кодом.
* **STOP — всегда.** Ctrl+C: первое нажатие отменяет текущий запуск (со всем деревом процессов),
  второе — выход. `bossman stop <run>` делает то же из другого окна.
* **Честные статусы.** MOCK_MODEL виден в интерфейсе. PASSED в лестнице доказательств — только по
  отчёту гейта на REAL_MODEL.
* **Windows first.** Windows Terminal, PowerShell и cmd; UTF-8 и кириллица; без ANSI, если вывод
  не в терминал или задан `NO_COLOR`.
* **Никаких секретов в выводе.** Токен, ключи и пароли не печатаются никогда, в том числе в
  stream-json.

## 3. Что владелец получает в терминале

### 3.1 Интерактивный режим (`Bossman-Terminal.cmd` / `bossman`)

```text
 ❯ Bossman 1.2  ·  build 1a2b3c4  ·  model qwen3.8-27b (local)  ·  agent Default  ·  LOCAL_ONLY     ● ONLINE

 › Улучши Bossman.

 ✻ Bossman
   думает… (reasoning от модели, приглушённо, сворачивается)
   ● search("tool selection")          ⎿ 7 совпадений в 3 файлах
   ● read_file(bcc/features/router.py) ⎿ 412 строк
   ● run_tests(tests/test_router.py)   ⎿ 1 failed — воспроизведено
   ● edit_file(bcc/features/router.py) ⎿ +12 −4
   ● run_tests(...)                    ⎿ 24 passed
   ◆ навыки: systematic-debugging, verification-before-completion
   ◆ память: 2 урока найдено, 1 применён
   ⚠ требуется разрешение: запись в стабильную установку → [y] да  [n] нет  [d] подробнее

 ── run 3f2a…7c9d · 2m14s · 12.4K→2.1K tok · $0.003 · verifier: PASS ──────────────────────
 ❯ _
```

Slash-команды:

| Команда | Что делает | Эквивалент в вебе (референсы) |
|---|---|---|
| `/help` | список команд | — |
| `/status` | подключение, модель, агент, режим, бюджеты | Run context |
| `/model` | список/смена модели через API моделей | Models, «Auto (Best model)» |
| `/agent` | выбор сохранённого профиля (в т.ч. RAW…USER_UX) | Agent variants |
| `/skills` | навыки: VERIFIED / CANDIDATE, триггеры, происхождение | Skills Library |
| `/memory <запрос>` | поиск по памяти и урокам | Memory recall |
| `/code <задача>` | coding task через coding path; показывает diff и вердикт проверки | Proposed changes, Verifier & Tests |
| `/computer` | состояние управления компьютером, STOP | Computer Control |
| `/tasks`, `/approvals` | очереди задач и разрешений | Tasks, Approvals |
| `/evolution status\|pause\|resume\|stop\|report` | цикл 1.1 | Self-Improve Lab, Progress ladder |
| `/stop` | отмена текущего запуска | Stop run |
| `/clear`, `/exit` | очистить экран / выйти | — |

### 3.2 Headless для Claude Code и скриптов

```text
bossman -p "Улучши Bossman" --output-format stream-json --agent "LAB · TOOL_FIRST" --max-seconds 2400 --approval-mode wait
bossman status --json
bossman runs show <run_id> --json
bossman approve <id>   |   bossman deny <id>
bossman stop <run_id>
bossman code "Почини падающий тест X" --json
bossman evolution status --json
```

Поток `stream-json` — по одному JSON-объекту на строку: `system/init` → `thinking` (только
реальный) → `assistant` (дельты текста) → `tool_use` / `tool_result` → `skill` / `memory` →
`approval_required` → `result` (статус, run_id, длительность, токены и стоимость, если известны).
Коды выхода документированы в `docs/owner/TERMINAL.md`.

### 3.3 Claude Code как учитель через терминал

```text
Claude Code ──bossman -p … stream-json──▶ Bossman (ученик: локальная модель + инструменты)
     │                                        │
     │◀──────── события: tool_use / result ───┘
     │
     ├─ LEVEL 0: молчит, наблюдает (CLAUDE_AUDITOR: OBSERVE)
     ├─ LEVEL 1–3: подсказка → self_improve_lab.py intervene --level N --hint …
     ├─ LEVEL 4: объяснение подхода
     └─ LEVEL 5: teacher patch → TEACHER_PATCH, не успех ученика
```

## 4. UX-предложения владельца (референсы 2026-09-23)

Референсы лежат в [`references/1_2/`](references/1_2/). Общий язык: тёмно-синий фон,
циановый/бирюзовый акцент, моноширинный шрифт, зелёный/янтарный/красный статусы, верхняя
навигация **CODE / CONTROL / AUTOMATE / EVOLVE**, индикатор **ONLINE**, слева навигация и
последние сессии, справа — живой контекст запуска.

Палитра для темы терминала и веба (снята с референсов, приблизительно):

| Токен | Цвет | Где |
|---|---|---|
| bg | `#050E19` | фон |
| panel | `#0F1C2B` | карточки |
| border | `#1C2E3D` | рамки |
| accent | бирюзовый/циановый | заголовки, активное, прогресс |
| ok / warn / err | зелёный / янтарный / красный | статусы PASS / PENDING / STOP |
| text / muted | светло-серый / серо-голубой | текст / подписи |

### 4.1 Self-Improve Lab — [01_self_improve_lab.jpg](references/1_2/01_self_improve_lab.jpg)

Сценарий «Bossman improves Bossman», карточки вариантов RAW / TOOL_FIRST / MEMORY /
PLAN_EXECUTE_VERIFY / RED_TEAM / USER_UX, Scenario Runner (кольцевой таймер, вариант, прогон
2/3, стадия, живой лог, **Stop run**), таблица Results, справа Claude teacher, Independent
verifier и **Progress ladder**.

Как сделать честно на нашем backend:
* таймер показывает реальный бюджет попытки (40 минут, не 30), по истечении — TIMEOUT и переход к
  следующему варианту;
* в таблицу добавить главную метрику — **проверенная полезная работа в час** — и исходы
  STUDENT_UNASSISTED_PASS / STUDENT_COACHED_PASS / TEACHER_PATCH / FAIL / TIMEOUT / BLOCKED; бейдж
  MOCK_MODEL, если модель тестовая; сравнение недействительно, если у вариантов разные модель,
  квант, runtime, baseline, права, контекст или время (INVALID_COMPARISON);
* карточка учителя показывает уровень вмешательства 0–5 и реакцию ученика, а не общий совет;
* карточка verifier — вердикт PASS / FAIL / PARTIAL / INVALID_TEST / UNSAFE и отрицательный контроль;
* Progress ladder = лестница North Star 1.1 из отчётов гейтов (`bossman_evolve.py gate/soak`);
  облачные MOCK-прогоны не превращают ступень в PASSED.

Терминал: `/evolution status` и `bossman evolution status --json`; лаборатория —
`self_improve_lab.py compare|lesson|transfer|intervene|status`.

### 4.2 Computer Control — [02_computer_control.jpg](references/1_2/02_computer_control.jpg)

Вкладки Desktop / Terminal / Files / Browser / Apps, живой экран, **Pause / Resume / Stop /
Request approval / Take control**, Action log с временем и глаголами (Open app, Inspect window,
Edit file, Run command, Capture screenshot, Verify result), справа — машина, последнее действие,
права, активные lease, ресурсы, восстановление после ошибок.

Как сделать честно:
* права — это текущая политика never / ask / allowed, а не галочки «всё разрешено»;
* Active leases — существующие `/api/approvals/leases` с отзывом;
* «Auto-retry on failure» разрешён только для чтения и идемпотентных шагов: мутации вслепую не
  повторяются (правило ambiguous effect / recovery ladder);
* у владельца Windows 11 на Ryzen AI Max+ 395, а не macOS: данные машины берутся из реального
  отчёта о машине;
* Take control — ручной перехват со STOP агента, а не параллельное управление.

Терминал: `/computer`, поток `tool_use` с действиями компьютера, `/stop`.

### 4.3 Skills Library — [03_skills_library.jpg](references/1_2/03_skills_library.jpg)

Категории (Debugging, TDD, Verification, Code Review, Memory, Model Scout, Web Research,
Computer Control, Telegram, Media, MVČR), Recently used, **Verified skills**, **Candidate
skills**; карточка: версия, статус, автор, тип, «Tools required» с доступностью, триггеры,
описание, связанные навыки, Install / Create skill.

Как сделать честно:
* статус CANDIDATE → VERIFIED только по тестам каталога; навык никогда не выдаёт инструменты и
  права: «Tools required» — какие инструменты навык ожидает и доступны ли они сейчас;
* вместо чужих «Downloads 12.4k» — локальная статистика: сколько раз применён и с каким
  проверенным эффектом;
* происхождение: источник, commit, лицензия (superpowers, anthropics, huggingface).

Терминал: `/skills`, события `skill` в потоке.

### 4.4 Thinking Process — [04_thinking_process.jpg](references/1_2/04_thinking_process.jpg)

Конвейер **Observe → Inspect → Reproduce → Patch → Run tests → Verify → Save lesson → Restart →
Retry**, Live Execution Log с фазами, «Reasoning (current step)», Proposed Changes (diff),
Test Results (live), справа — контекст выполнения, Verifier & Tests, Changes, Memory & Learning,
Cost & Resources.

Как сделать честно:
* фазы берутся из checkpoint цикла 1.1 (OBSERVE…CHECKPOINT) и событий coding path, а не из таймера;
* «Reasoning» — только то, что модель действительно выдала (reasoning-поток или её план);
* урок сохраняется только после вердикта verifier PASS, иначе FAILED_EXPERIMENT;
* «Restart / Retry» — перезапуск по правилам восстановления: неизвестный исход не повторяется
  без `--redo`.

Терминал: это и есть основной вид интерактивного режима (блоки tool_use, diff, тесты, статус-строка).

### 4.5 Workspace — [05_workspace_chat.jpg](references/1_2/05_workspace_chat.jpg)

Диалог You / Bossman, план, прогресс по шагам, вопрос «продолжить?», сохранённый план; поле
ввода принимает и обычный текст, и команды (`bossman run --scenario self-improve`); `@` для
ссылок, вложения, выбор модели. Справа Run context: локальная модель, verifier, Memory recall,
Approvals, Budgets (токены, вызовы инструментов), Computer control, активный сценарий.

Как сделать честно:
* «Shall I proceed?» — это approval существующей политики, а не вопрос в тексте;
* Budgets — реальные лимиты миссии и расход;
* Memory recall — сколько записей найдено и какие применены (hit ≠ learning).

Терминал: статус-строка и `/status` — это Run context; поле ввода понимает slash-команды.

## 5. Этапы 1.2

| Этап | Что | Статус на 2026-09-23 |
|---|---|---|
| 1.2.0 | `bossman`: интерактивный режим + headless `stream-json`, slash-команды, approvals, STOP, лаунчеры в Windows-архиве | в работе (к owner-run) |
| 1.2.1 | тема по терминальным референсам владельца (пришлёт отдельно), полноэкранная раскладка: диалог / Run context / Action log | после референсов |
| 1.2.2 | Claude Code ведёт лабораторию 1.1 через терминал: учитель LEVEL 0–5, аудитор, отчёт за прогон | готовится вместе с 1.1 |
| 1.2.3 | веб-интерфейс по референсам 4.1–4.5 на том же потоке событий | после 1.2.1 |

## 6. Критерии приёмки 1.2 (на машине владельца)

1. Из `Bossman-Terminal.cmd` владелец без веба выполняет: вопрос модели, задачу с инструментом,
   coding task с diff и проверкой, действие на компьютере с approval, поиск по памяти, запуск и
   STOP цикла 1.1.
2. Claude Code через `bossman -p … --output-format stream-json` проводит один вариант лаборатории
   от начала до вердикта и записывает вмешательства учителя.
3. Ctrl+C / `bossman stop` останавливает запуск и всё дерево процессов; сирот не остаётся.
4. Approval, запрошенный из терминала, виден в вебе и Telegram; решение из любого места
   продолжает запуск в терминале.
5. Кириллица и пути с пробелами работают в Windows Terminal, PowerShell и cmd; при выводе в файл
   нет ANSI-кодов.
6. Токены и ключи не появляются ни в выводе, ни в stream-json, ни в логах.
7. «Thinking» не показывается, если модель его не отдала; MOCK_MODEL подписан.

## 7. Чего 1.2 не делает

* не заменяет веб-интерфейс и Telegram;
* не создаёт вторую память, второй gateway, второй Telegram poller или второй coding engine;
* не даёт терминалу прав больше, чем у веба (never / ask / allowed одна на всех);
* не заявляет AUTONOMOUS_SELF_IMPROVEMENT_READY / 24H_SOAK_PASS без прогона на железе владельца.
