# Карта ботов Bossman (версия f189904fce9e)

Сгенерировано: 2026-10-18T17:14:35Z · исходный SHA: `67a2a0b54239ff78c89c724d254e9f575d33dfb3` · запущенная сборка: `c25413930eb2` · окно задач: 96

Агентов в БД: **41** (работает 0, простаивает 31, выключено 1, ни разу не запускался 9); компонентов в коде: **8**.

`N/A` - данных нет, не выдумано. `never_run` означает «нет задач в окне снимка». Текст инструкций в карту не копируется, только sha256.

## Компоненты (определены в коде)

| ID | Назначение | Инструменты / данные | Лимиты | Состояние | Основание состояния |
|---|---|---|---|---|---|
| Autonomy loop (Line A) | одна пишущая сессия (Claude/Codex CLI по подписке, либо бесплатный Nemotron-diff) и только читающие ревьюеры; цели, журнал, бюджет, STOP | изолированный клон + ветка, брокер рук (HandBroker), журнал с хеш-цепочкой | аренда Line A (одна пишущая), таймаут задачи, STOP-файлы, бюджет autonomy/budget.py, конституция v2 с пином | **idle** | /api/autonomy/status loop=READY goals_total=0 |
| Qwen Operator L1 Lab | локальный Qwen как оператор лаборатории: план, правка в изолированном worktree, тесты, коммит в lab/* | реестр моделей/провайдеров, coding-task sidecar, политика autonomy (пути/argv), очередь approvals | MAX_ATTEMPTS=3 на ошибку, обязательная проверка, откат при ухудшении, STOP; push/merge/deploy отклоняются (promote refused) | **idle** | /api/qwen-operator/status busy=False |
| Argon PC-Agent | управление Windows-компьютером владельца через единое ядро (задачи, tool_calls, approvals) | экран/окна/процессы/файлы (T0), обратимые локальные действия (T1); T2 только после кнопки; T3 запрещено (секреты, конституция, платежи) | уровни доступа T0-T3, окно доверия /trust, append-only журнал, /stop | **idle** | /api/pc-agent/status stopped=False active_tasks=0 |
| Evolution loop (bossman_v3.self_improvement) | ограниченный цикл самоисправления: лагерь в каталоге данных, кандидат не повышается в stable | исходный checkout только в разрешённых code-roots, бесплатная модель | число циклов 1..200, пауза после 3 подряд неудачных циклов, STOP владельца | **paused** | /api/evolution/status status=AUTO_PAUSED loop_running=False halt_reason=MAX_CONSECUTIVE_FAILURES pause=3 consecutive failed cycles |
| Limits-out orchestra | последовательность циклов tree_self_repair_cycle своими воркерами, когда лимиты ассистента кончились | free worker, скрытый holdout, независимая проверка, отчёт в пульт | --max-cycles/--max-hours, режим --paid none по умолчанию (cost_usd должен быть 0.0), STOP_ORCHESTRA, пауза при direct-gen, BELOW_NORMAL, never merges | **N/A** | не проверялось (нет статусного endpoint или снимок не передан) |
| Tree self-repair cycle | один цикл самоисправления через запущенный Bossman со стадиями DEFECT_REPRODUCED ... TRANSFER_PASS | изолированная копия, скрытый holdout | стадии не повышаются последующими; APPLIED_RUNTIME_PASS не решается скриптом | **N/A** | не проверялось (нет статусного endpoint или снимок не передан) |
| Jeff (PIT, Telegram-бот для друзей владельца) | Telegram-бот-помощник; отделён от бота-пульта владельца (отдельный токен, замок на токен) | N/A (не проверялось; одобрения, STOP, Computer Use и coding живут только на боте-пульте) | refuses to start with the companion token; per-token kernel lock | **N/A** | не проверялось (нет статусного endpoint или снимок не передан) |
| Telegram companion (пульт) | отдельно запускаемый мост управления и отчётов владельца | N/A | import не запускает воркер/сеть/модель; пульт получает одобрения и STOP | **N/A** | не проверялось (нет статусного endpoint или снимок не передан) |

## Агенты в БД (GET /api/agents)

| ID | Имя | Роль | Инструменты | Разрешения | Модель (статус) | Лимиты шаги/токены/USD | Состояние | Задач | Последняя | sha256 инструкции |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Пилот | Основной исполнитель владельца | computer.act, computer.observe, jeff.broadcast | channel.send, computer.act, computer.observe | local-qwen36-35b-a3b-q5-exact (online) | 8/2048/0.0 | **idle** | 19 | #68 failed | `eb27928ea545` |
| 2 | Решатель | Решает сложные задачи программирования | — | — | local-qwen38-27b-q5-exact (online) | 2/8192/0.0 | **idle** | 2 | #13 completed | `c7b05cf05550` |
| 3 | Nemotron worker (free, test 2026-09-24) | worker | — | — | nemotron-ultra-free (error) | 2/8000/0.0 | **idle** | 9 | #71 failed | `188fda992b02` |
| 4 | OWNER-BENCH-NEX-20260925 | read-only browser/vision routing benchmark | — | — | nemotron-ultra-free-canonical (online) | 1/250/0.0 | **idle** | 8 | #62 failed | `554aeae4c718` |
| 5 | YOUTUBE-VERIFY-NEMOTRON-20260925 | independent read-only patch verifier | — | — | nemotron-ultra-free-canonical (online) | 1/600/0.0 | **idle** | 2 | #58 completed | `cbf8f77269e0` |
| 6 | YT-Nemotron-1-20260925 | EXTRACTOR | — | — | nemotron-ultra-free-canonical (online) | 1/1200/0.0 | **idle** | 1 | #28 completed | `ec122e3c1bdb` |
| 7 | YT-Nemotron-2-20260925 | SKEPTIC | — | — | nemotron-ultra-free-canonical (online) | 1/1200/0.0 | **idle** | 1 | #29 completed | `ec122e3c1bdb` |
| 8 | YT-Nemotron-3-20260925 | CURRICULUM | — | — | nemotron-ultra-free-canonical (online) | 1/1200/0.0 | **idle** | 1 | #30 completed | `ec122e3c1bdb` |
| 9 | YT-FIX-LING-PATCH-ONLY | public repo patch proposal | — | — | ling-30-fin-free (unavailable) | 1/1200/0.0 | **idle** | 1 | #31 failed | `8db2e9655bb0` |
| 10 | YT-FIX-NEMOTRON-PATCH-ONLY | public repo patch proposal | — | — | nemotron-ultra-free-canonical (online) | 1/1200/0.0 | **idle** | 1 | #32 completed | `c649d5747b6f` |
| 11 | YT-FIX-INDEPENDENT-VERIFY-2 | read-only verifier, no code writes | — | — | nemotron-ultra-free-canonical (online) | 1/850/0.0 | **idle** | 1 | #33 completed | `ca36e79acb9f` |
| 12 | YT-FIX-NEX-VERIFIER | read-only independent patch verifier | — | — | nex-n25-pro-free (unavailable) | 1/500/0.0 | **idle** | 1 | #34 completed | `efd1a327d8e6` |
| 13 | YT-Nemotron-Extractor-Compact | public transcript extractor | — | — | nemotron-ultra-free-canonical (online) | 1/2600/0.0 | **idle** | 1 | #35 completed | `9467ddf63517` |
| 14 | YT-Nemotron-SKEPTIC-Compact | SKEPTIC | — | — | nemotron-ultra-free-canonical (online) | 1/2600/0.0 | **idle** | 1 | #36 completed | `7784980104e8` |
| 15 | YT-Nemotron-CURRICULUM-Compact | CURRICULUM | — | — | nemotron-ultra-free-canonical (online) | 1/2600/0.0 | **idle** | 1 | #37 completed | `7784980104e8` |
| 16 | OWNER-LOCAL-QWEN38-PROBE | read-only owner CMD acceptance | — | — | local-qwen38-27b-q5-exact (online) | 1/128/0.0 | **idle** | 7 | #50 failed | `7087a54864f3` |
| 17 | FRESH-VIBES-LOCAL-DRAFT | business content dry-run worker | — | — | local-qwen38-27b-q5-exact (online) | 1/1100/0.0 | **idle** | 1 | #39 stopped | `bb598b8dc78a` |
| 18 | FRESH-VIBES-FAST-LOCAL-DRAFT | business dry-run worker | — | — | local-qwen36-35b-a3b-q5-exact (online) | 1/550/0.0 | **idle** | 2 | #52 completed | `1e8157be9d1b` |
| 19 | VAULT-ACL-NEMOTRON-REVIEW | independent security reviewer | — | — | nemotron-ultra-free-canonical (online) | 1/2600/0.0 | **idle** | 1 | #41 failed | `8087596a5f81` |
| 20 | VAULT-ACL-LOCAL-INDEPENDENT | independent security reviewer | — | — | local-qwen36-35b-a3b-q5-exact (online) | 1/2000/0.0 | **idle** | 3 | #57 completed | `56e96771ba0d` |
| 21 | BOSSBLOCKS-LOCAL-OWNER-20260925 | Local game coding worker for owner benchmark | list_dir, read_file, search, edit_file, write_file, run_tests, finish | require_tests_before_finish | local-qwen36-35b-a3b-q5-exact (online) | 40/4096/0.0 | **idle** | 1 | #59 stopped | `509de727db27` |
| 22 | BOSSBLOCKS-NEMOTRON-REVIEW-20260926 | Independent game design reviewer for BOSSBLOCKS-001; no product code writes | — | read_only | nemotron-ultra-free-canonical (online) | 1/900/0.0 | **idle** | 1 | #53 failed | `03a5a5afbac5` |
| 23 | BOSSBLOCKS-LOCAL-SMALL-20260926 | Bounded local Godot game implementation worker | list_dir, read_file, search, edit_file, write_file, run_tests, finish | — | local-qwen36-35b-a3b-q5-exact (online) | 12/3000/0.0 | **idle** | 1 | #56 completed | `7d46cbe7dc64` |
| 24 | OWNER-LOCAL-UNCENSORED-PASSPORT-PROBE | Owner-only readout of supplied consent-backed facts | — | — | owner-local-community-uncensored (offline) | 1/1024/0.0 | **disabled** | 1 | #60 failed | `aea17f47fd83` |
| 25 | Nemotron-Ultra NIM worker (free, 2026-09-30) | worker | — | — | nvidia/nemotron-3-ultra-550b-a55b (online) | 3/4000/0.0 | **idle** | 3 | #74 completed | `ae8a71da3824` |
| 26 | Qwen Оператор (компьютер) | Локальная Qwen управляет ПК владельца: наблюдает экран и выполняет простые действия | computer.observe, computer.act | computer.control, computer.observe | local-qwen36-35b-a3b-q5-exact (online) | 12/2048/0.0 | **idle** | 3 | #86 completed | `51a7f21644ed` |
| 27 | Qwen Браузер (вход как владелец) | Локальная Qwen работает на сайтах под учётками владельца из сейфа | browser.open, browser.read_dom, browser.click, browser.type, browser.submit, browser.login, browser.screenshot | browser.control, browser.read | local-qwen36-35b-a3b-q5-exact (online) | 15/2048/0.0 | **idle** | 1 | #79 completed | `a2a1b29500e8` |
| 28 | Swapme local report 2026-10-02 | Local report execution under owner approval | terminal.run, terminal.status | terminal.read | local-qwen36-vision-exact (online) | 4/2048/0.0 | **idle** | 1 | #81 stopped | `ed8d54fb43de` |
| 29 | LAB · RAW | lab:self-improve | read_file, search, list_dir, edit_file, write_file, run_tests | — | N/A (N/A) | 40/4096/0.0 | **never_run** | 0 | N/A | `0e738040e915` |
| 30 | LAB · TOOL_FIRST | lab:self-improve | read_file, search, list_dir, edit_file, write_file, run_tests | require_tests_before_finish | N/A (N/A) | 40/4096/0.0 | **never_run** | 0 | N/A | `f49f4be2ad6a` |
| 31 | LAB · MEMORY | lab:self-improve | read_file, search, list_dir, edit_file, write_file, run_tests | require_tests_before_finish, use_memory | N/A (N/A) | 40/4096/0.0 | **never_run** | 0 | N/A | `e7678ce4cc43` |
| 32 | LAB · PLAN_EXECUTE_VERIFY | lab:self-improve | read_file, search, list_dir, edit_file, write_file, run_tests | require_tests_before_finish | N/A (N/A) | 40/4096/0.0 | **never_run** | 0 | N/A | `3134d036572c` |
| 33 | LAB · RED_TEAM | lab:self-improve | read_file, search, list_dir, edit_file, write_file, run_tests | require_tests_before_finish | N/A (N/A) | 40/4096/0.0 | **never_run** | 0 | N/A | `c22d64a990ff` |
| 34 | LAB · USER_UX | lab:self-improve | read_file, search, list_dir, edit_file, write_file, run_tests | require_tests_before_finish | N/A (N/A) | 40/4096/0.0 | **never_run** | 0 | N/A | `6c89fdae5a5c` |
| 35 | LAB · CLAUDE_AUDITOR | lab:observer | read_file, search, list_dir | — | N/A (N/A) | 20/4096/0.0 | **never_run** | 0 | N/A | `31d9c9433d13` |
| 36 | LAB · RESULT_VERIFIER | lab:observer | read_file, search, list_dir | — | N/A (N/A) | 20/4096/0.0 | **never_run** | 0 | N/A | `4bdf900a5df4` |
| 37 | LAB · UX_OBSERVER | lab:observer | read_file, search, list_dir | — | N/A (N/A) | 20/4096/0.0 | **never_run** | 0 | N/A | `76daa5b56db7` |
| 38 | Bossman Desktop Operator — Local Qwen Master Prompt Demo | Локальный оператор рабочего стола; инструменты GUI с проверкой | computer.observe, computer.act | computer.control, computer.observe | local-qwen36-35b-a3b-q5-exact (online) | 12/2048/0.0 | **idle** | 1 | #83 stopped | `51a7f21644ed` |
| 39 | Qwen-Operator L1 (uncensored) | operator:l1-lab | read_file, search, list_dir, edit_file, write_file, run_tests | filesystem.read, require_tests_before_finish | owner-local-community-uncensored (offline) | 40/4096/0.0 | **idle** | 5 | #92 completed | `f45857ed47bf` |
| 40 | Argon PC-Agent | pc-agent:argon | computer.observe, computer.act, terminal.run, browser.open, browser.read_dom, browser.screenshot | browser.read, computer.observe, filesystem.read, terminal.read | owner-local-community-uncensored (offline) | 4/2048/0.0 | **idle** | 3 | #88 completed | `174edc3eba75` |
| 41 | Gemma 4 uncensored | chat:local-uncensored | — | — | owner-local-gemma4-uncensored (online) | 4/2048/0.0 | **idle** | 4 | #96 stopped | `845c4a8e8bfb` |

Владелец агента, лимит времени и глубины делегирования в таблице `agents` не хранятся: `N/A`.
