# PHASE 1 — FULL TEST RUN (быстрый режим)

Дата: 2026-09-06 · Ветка: fix/astra-epoch-residual-20260906 · HEAD: e4a5a2311837df298dca114fd5c3b076f01ff989
Окружение прогона: Windows (win32), Python 3.14.3, pytest 9.0.2 (локальная верификация перед снятием draft PR #25)

## Suite 1 — V5 core suite (231 collected)

```
passed: 229 | failed: 2 | errors: 0 | skipped: 0 | time: 15.76s
FAILED tests/test_v5_observers.py::test_file_observer_refuses_to_treat_a_symlink_as_the_named_file
FAILED tests/test_v5_observers.py::test_directory_observer_refuses_symlink_escape
```

## Suite 2 — Core integration (`-k "fleet or organization or journal or continuity"`)

```
collected 751 / 741 deselected / 10 selected
passed: 10 | failed: 0 | errors: 0 | skipped: 0 | time: 2.36s
```

## Suite 3 — Root regression (`--ignore=tests/test_v5_golden_missions.py`)

```
collected 734
passed: 729 | failed: 5 | errors: 0 | skipped: 0 | time: 138.31s
FAILED tests/test_context_slice.py::test_fingerprint_changes_on_add_delete_rename_untracked_symlink
FAILED tests/test_context_slice.py::test_failing_test_slice_is_depth_bounded_and_hashed
FAILED tests/test_evidence_signing_shared.py::test_key_created_in_env_path_with_0600
FAILED tests/test_v5_observers.py::test_file_observer_refuses_to_treat_a_symlink_as_the_named_file   (dup suite 1)
FAILED tests/test_v5_observers.py::test_directory_observer_refuses_symlink_escape                     (dup suite 1)
```

## SUMMARY

| Suite | passed | failed | errors | skipped |
|---|---|---|---|---|
| V5 core | 229 | 2 | 0 | 0 |
| Core integration | 10 | 0 | 0 | 0 |
| Root regression | 729 | 5 | 0 | 0 |
| **Итого (уникально)** | **729+229+10−дубли** | **5 уникальных** | 0 | 0 |

Уникальных падений: **5** (2 из них воспроизводятся в обоих прогонах).
Checkpoint 1 (SQLite, fb4740d2): все tests/test_v5_connection_lifetime.py + storage тесты — **зелёные**.
Checkpoint 2 (V5 binding, fa6d45df): tests/test_v5_admission_binding_regressions.py — **39 passed**, зелёные (включая e4a5a23 «bind admitted effects and reserve once»).

## Замечания по окружению

- **SKIPPED тестов: 0** — ни один тест не пропущен; реестр пропусков пуст на момент прогона.
- **ModuleNotFoundError: нет** — ни в одном из трёх прогонов (регрессия run 34028670388 не воспроизводится).
- **sleep()/fake timers в V5-тестах: не обнаружено** (grep `sleep\(` по tests/test_v5_* — 0 совпадений; human-speed regression risk отсутствует).
- pytest atexit `cleanup_dead_symlinks` → PermissionError на `pytest-current` — внутренний шум pytest на Windows, не относится к коду репозитория.
- SyntaxWarning: `bossman-core/tests/test_tools.py:118` — невалидная escape-последовательность `"\W"` (P2, cosmetics).

## Логи

- phase1_v5_run.log
- phase1_core_run.log
- phase1_root_run.log
