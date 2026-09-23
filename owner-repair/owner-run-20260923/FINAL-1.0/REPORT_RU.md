# BOSSMAN 1.0 — FINAL CONVERGENCE: состояние на момент выключения компьютера (23.09.2026)

Владелец попросил закончить и запушить: компьютер выключается. Конвергенция **не доведена до FREEZE**. Ниже только факты.

## Итоговые поля
| Поле | Значение |
|---|---|
| FINAL_CANDIDATE_SHA | **НЕТ** (не выбран: гейты не пройдены) |
| Текущая голова конвергенции | `f72af6a82569371365de7cc5fc983a842c13735b` на `fix/owner-run-20260923-p1` (запушено) = RC0-pre, НЕ сертифицирован |
| P0 | подтверждённых 0; **3 кандидата P0/P1 из чтения кода, НЕ воспроизведены** (см. ниже) — блокируют freeze до проверки |
| P1 | закрыто кодом 3 из 4 (не верифицированы независимо); controlled apply — WIP, не влит |
| P2 | 20+ открытых из owner-run (BUGS.md), не блокируют |
| CI exact-SHA | NOT_RUN для f72af6a8 |
| WINDOWS_100 | NOT_RUN. Существующий «Windows 100 Scenarios» на default-ветке — фальшивка (печатает OK ×100); настоящий харнесс спроектирован (100 сценариев по областям), код не написан |
| SECURITY_REAUDIT | PARTIAL: регрессия S1–S4 + redteam зелёная на SHA4; новый рой атак на RC0 — NOT_RUN |
| TERMINAL | TR-01…22 на SHA4 — из утреннего прогона (см. ../OR0923-bd2fe23d/TR_MATRIX.md); на RC0 — NOT_RUN |
| OWNER_HW | HW-02 (исправлен код, живой прогон NOT_RUN), HW-06/TR-12/TR-13 — NOT_RUN (тестовый конфиг компаньона подготовлен) |
| JEV | CONTRACT_VERIFIED + SHADOW_PASS (утро); на RC0 — NOT_RUN |
| SELF_REPAIR | SELF_REPAIR_SINGLE_CYCLE_PASS (coached, L4) — без изменений; 3 новых цикла NOT_RUN (экзамен-кейсы INCOMPLETE) |
| TRANSFER | NO_MEASURED_GAIN — без изменений |
| ZIP_SHA256 | для RC0 нет. Последний собранный: SHA4 `012ceb838c2c39b5b07d233904da1398c4a648efa92c3d60fb4decc4fb4bb235` |
| BLOCKERS | см. раздел «Блокеры» |

## Что сделано в этом проходе
1. **Состояние зафиксировано** — `PRE_CONVERGENCE_HEADS.md`: все owner-run фиксы и security `c5daa5e3` уже внутри SHA4 `12612c81`; слои 1–2 без merge.
2. **CONVERGENCE_BRANCH** = `fix/owner-run-20260923-p1`: fast-forward `cdb4b09d → 12612c81`, затем 3 фикса → `f72af6a8`. Без force-push, release не тронут.
3. **Регрессия слоёв 1–2 на SHA4:** bossman-core security+owner-run 251 pass / 3 skip; command-center security/approvals/STOP/sandbox/browser/contract/terminal 367 pass.
4. **Слой 3 (P1), каждый: REPRODUCE → failing test → FIX → PASS; авторы — отдельные агенты, верификация независимым агентом ещё НЕ проведена:**
   | Баг | Commit (на ветке) | До фикса | После |
   |---|---|---|---|
   | DOWNLOAD-FALSE-SUCCESS (PDF false PASS) | `c0a7e039` | 14 тестов FAIL (`completed` вместо `failed`) | 27 pass, вкл. реальный Chromium: approval → файл на диске |
   | APP-CONTRACT-OVERRIDES-AGENT-TOOLS (computer.* агенту) | `aa6bee3d` | 6 FAIL (`['apps_start','apps_stop']`) | 15 pass; computer.act по-прежнему `waiting_approval`; S2/STOP сьюты зелёные |
   | CODING-SNAPSHOT-32MB (репо 74 МБ, 3523 файла) | `f72af6a8` | 7 FAIL | 9 pass; лимит НЕ поднят — потоковые sha256 всех файлов, байты только изменённых (бюджет 32 МБ на изменения); реальный репо: 26.7 с, пик 50 МБ (старый код с лимитом 1 ГБ — 331 МБ) |
   | Controlled apply → canonical project | НЕ влит; WIP `3fa3d33f` сохранён как `WIP_controlled_apply_3fa3d33f.patch` | маршрута нет | 17/17 своих тестов; соседние сьюты не прогнаны; меняет клон sandbox (autocrlf проекта) — требует полной регрессии |
   Совместная голова f72af6a8: command-center 233 pass, bossman-core apprentice+security 213 pass / 18 skip.
5. **Полная регрессия SHA4 (базовая линия) — прервана выключением.** Промежуточно: root 2233 pass / 6 F; core 3240 pass / 7 F / 2 E; command-center 992 pass / 0 F (≈20%). Имена упавших не получены. Автор фикса download нашёл 3 уже падающих на SHA4 теста: `test_golden_missions::test_mission_03_terminal_command`, `test_redteam_rc_20260921::test_rt_d5_executables_are_quarantined_not_executed`, `test_file_intelligence_contract::test_argv_is_a_list_so_a_filename_is_never_a_command` — причина (Windows-окружение или продукт) не разобрана.
   **Досчитано перед выключением (SHA4, локально Windows, tvenv py3.12):**
   - root: **6 failed / 2616 passed / 10 skipped** — `owner_scenarios` ×3 (замороженная таблица уровней / пробелы / отрицательный контроль), `test_owner_run_self_improve::test_stop_kills_the_child_and_grandchild…`, `test_skips_registry::test_registry_is_current…`, `test_target_hardware_acceptance::test_the_real_probe_on_this_machine…` (последний ожидаемо зависит от железа владельца);
   - bossman-core: **7 failed / 2 errors / 3254 passed / 132 skipped** — `test_computer_control_authorization` ×2, `test_computer_observation_pipeline::test_capture_survives…`, `test_teacher_isolation::test_teacher_iso_001…`, `test_v3_cross_layer_e2e`, `test_v3_fence_receipts`, `test_windows_host_shell::test_posix_local_shell_unchanged`, ERROR `test_fleet_remote_auth::test_strict_bounded_json` ×2;
   - command-center: не досчитан (≈20%, 0 F на 992).
   Классификация (PRODUCT / ENVIRONMENT-Windows / HARNESS) НЕ сделана; на Linux CI они могут быть зелёными — это проверяет только exact-SHA CI. Логи: `C:\Users\asd\Bossman\build-0923\regress\sha4.*.log` (локально).
   Ошибка моего харнесса (исправлена): первый запуск давал ImportError из-за коллизии пакета `tests` (command-center/tests перекрывал namespace-пакеты) — не продуктовая.

## Новые кандидаты в дефекты (из чтения кода автором Windows-100, НЕ воспроизведены)
| # | Где | Гипотеза | Предв. severity |
|---|---|---|---|
| C1 | `POST /api/snapshots` (`snapshot.py:273,281`) | `kind` идёт в имя папки: `{"kind":"/../../../x"}` → копия БД вне data dir без approval | **P0-кандидат (path traversal)** |
| C2 | `POST /api/opencode/sessions` (`tools_opencode.py:132`) | `git worktree add` до второй проверки containment: `worktree_name="../x"` создаёт папку вне корней до ответа 403 | P1-кандидат |
| C3 | `POST /api/terminal/roots` | любой корень без проверки/approval расширяет terminal/OpenCode/code roots | P1-кандидат (зависит от auth-модели) |
| C4 | file-intelligence `job_id` (`service.py:107`) | путь без проверки; фича выключена по умолчанию (`BCC_FILE_INTELLIGENCE=1`) | P2 |
| C5 | installed build | default code root = `venv\Lib`; `/health` 503 до настройки модели | P2 |
Также харнесс: `verify_clean_install.pristine_export` (git archive) даёт `SOURCE_IDENTITY_UNKNOWN` вместо build SHA.

## Блокеры freeze
1. C1–C3 не воспроизведены/не закрыты (возможный P0).
2. Независимая верификация трёх P1-фиксов не проведена (автор ≠ verifier).
3. Controlled apply не завершён (WIP).
4. Полная регрессия не завершена; 3+ известных падения на SHA4 не разобраны.
5. Настоящий Windows-100 не написан; фальшивый на default-ветке требует решения владельца (retire).
6. CI exact-SHA: 4 обязательных workflow (Windows bundle, Windows owner run, Owner scenarios, V2 Auto-Repair) запускаются только push в `claude/** | night/** | release/**`, и 3 из них нельзя dispatch (нет файла на default-ветке). Нужен CI-триггер-ref `claude/**` на точный SHA кандидата (решение принято, не выполнено).
7. RC0 live: TR, HW-02/06, TR-12/13/15, UI↔CLI, media, Jev, standard-user — NOT_RUN на новой голове.

## Rollback
Ничего не влито в release/main. Откат = ничего не делать: `release/bossman-owner` = `e0bf948d`. Конвергенция откатывается выбором `12612c81` (SHA4, ZIP `012ceb83…b235`).
