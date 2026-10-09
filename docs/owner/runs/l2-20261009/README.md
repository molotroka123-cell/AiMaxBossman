# L2 на точном SHA 3b07c9ae (Windows-ПК владельца, чистый venv, 09.10.2026)

Команды: `command-center`: `pytest -q -n 6 --timeout=180` (включая `tests/telegram_calls` параллельно, в CI они идут отдельно и последовательно); корень: `pytest -q -n 6 --timeout=300 tests`; ядро: `cd bossman-core && pytest -q -n 6 --timeout=300 tests`. JUnit-файлы лежат в `audit-20261009/l2-3b07c9ae/` (в git не коммитятся).

| Набор | Итог |
|---|---|
| cc | 5 failed, 9398 passed, 55 skipped, 214 warnings, 4 errors in 1069.73s (0:17:49) |
| root | 7 failed, 3099 passed, 12 skipped in 383.33s (0:06:23) |
| core | 2 failed, 3611 passed, 137 skipped, 256 warnings in 346.19s (0:05:46) |

## Красные локально (Windows владельца). В CI на этом SHA корневой и core-runtime (windows-latest) зелёные => это находки уровня L5, не регрессии ветки

Не воспроизведены в тишине/по одному ещё не все; повторная проверка по одному — следующий шаг WS0.

### cc
- `FAILED tests/telegram_calls/test_calls_e2e_offline.py::test_restart_keeps_the_vault_stop_and_uncertainty_and_a_lost_key_is_reported`
- `FAILED tests/telegram_calls/test_calls_e2e_offline.py::test_secrets_never_appear_in_any_response_log_file_or_the_database`
- `FAILED tests/test_browser_reading_20260930.py::test_navigate_waits_for_text_that_arrives_after_domcontentloaded`
- `FAILED tests/test_ops_terminal_browser.py::test_model_click_on_pay_waits_for_the_owner_then_runs`
- `FAILED tests/test_video_cancel_button.py::test_cancel_stops_the_rendering_export_only_and_partial_output_is_not_ready`
- `ERROR tests/test_browser_navigation_ui.py::test_empty_browser_screen_does_not_invent_readiness[unreachable-\u043f\u0440\u043e\u0432\u0435\u0440\u0438\u0442\u044c \u0443\u0441\u0442\u0430\u043d\u043e\u`
- `ERROR tests/test_browser_navigation_ui.py::test_human_navigation_uses_policy_without_self_approval`
- `ERROR tests/test_browser_navigation_ui.py::test_policy_403_keeps_session_but_401_requires_login`
- `ERROR tests/test_browser_navigation_ui.py::test_lost_csrf_token_requires_login_instead_of_dead_403`

### root
- `FAILED tests/test_astra_security_gate.py::test_windows_bundle_audits_the_shipped_lock_not_this_runner`
- `FAILED tests/test_evolution_loop.py::test_stop_during_an_attempt_cancels_it_quickly_and_resume_finishes_the_cycle`
- `FAILED tests/test_rc19_side_by_side_refusals.py::test_stop_rc_counts_zero_and_one_process_under_strict_mode[1-C:\\Users\\asd\\AppData\\Local\\Microsoft\\WindowsApps\\pwsh.EXE]`
- `FAILED tests/test_rc19_side_by_side_refusals.py::test_stop_rc_counts_zero_and_one_process_under_strict_mode[1-C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.EXE]`
- `FAILED tests/owner_scenarios/test_owner_scenarios.py::test_уровни_совпадают_с_замороженной_таблицей`
- `FAILED tests/owner_scenarios/test_owner_scenarios.py::test_объявленные_пробелы_не_пусты_и_совпадают_с_незелёными`
- `FAILED tests/owner_scenarios/test_owner_scenarios.py::test_каждое_исполненное_утверждение_имеет_отрицательный_контроль`

### core
- `FAILED tests/test_computer_control_authorization.py::test_authorized_owner_on_enabled_profile_still_acts`
- `FAILED tests/test_computer_control_authorization.py::test_local_owner_without_profiles_still_acts`

## Автозонд листьев (`tools/tree_proof/leaf_pytest_probe.py`)
58 листьев «код/ветка»: найдены тесты, реально импортирующие модуль, у 12; PASS у всех 12; **принят 1** (`pv-ui`) — новый лист без старых записей.
11 листьев помечены `CONFLICT_PRIOR_EVIDENCE` и **не тронуты**: у них уже лежат записи прошлых проб (4 модуля `trading_learning` — тогда тесты были пропущены целиком, `1 skipped`; 7 плагинов — проверки живого вызова/approval-gate, которые тест-прогон не заменяет). Решение по ним — за владельцем или отдельная процедура архивирования старых записей.
46 листьев без импортирующих тестов: им нужны новые тесты (задача для воркеров Bossman, проверка мутацией).
