"""Long-horizon run 30.10 / owner audit 14:10 chain (finalize_refused -> failed -> deadlock): file evidence must point
at the file the agent really wrote, not at a bare name resolved against the first terminal root (Python312\Lib)."""
from __future__ import annotations

import asyncio
from pathlib import Path

from bcc.features.action_contract import _terminal_evidence
from bcc.v2.verification import ExpectedState, _observe_file


def test_full_windows_path_in_the_task_is_kept():
    exp = _terminal_evidence(r"Рабочая папка: C:\work\lh-runs\run-b. Запиши C:\work\lh-runs\run-b\out\profile.json")
    assert exp is not None and exp.target == r"C:\work\lh-runs\run-b\out\profile.json"


def test_bare_name_stays_bare():
    exp = _terminal_evidence("Создай файл hello.txt с текстом ok")
    assert exp is not None and exp.target == "hello.txt"


def test_relative_name_is_found_in_the_root_that_holds_it(tmp_path):
    lib, work = tmp_path / "Lib", tmp_path / "work"
    lib.mkdir(); work.mkdir()
    (work / "hello.txt").write_text("ok", encoding="utf-8")
    obs, _ = asyncio.run(_observe_file(ExpectedState("file", "hello.txt", {"exists": True}), roots=[lib, work]))
    assert obs.observed.get("exists") is True and Path(obs.target).parent == work


def test_same_name_in_two_roots_is_ambiguous_not_verified(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    for d in (a, b):
        (d / "hello.txt").write_text("x", encoding="utf-8")
    obs, _ = asyncio.run(_observe_file(ExpectedState("file", "hello.txt", {"exists": True}), roots=[a, b]))
    assert "ambiguous" in obs.observed.get("error", "") and not obs.observed.get("exists")


def test_missing_file_still_fails(tmp_path):
    obs, _ = asyncio.run(_observe_file(ExpectedState("file", "nope.txt", {"exists": True}), roots=[tmp_path]))
    assert obs.observed.get("exists") is False


def test_the_produced_file_is_the_obligation_not_the_input():
    exp = _terminal_evidence(r"Данные: C:\w\run\orders.csv (есть дубли). Запиши C:\w\run\out\profile.json с ключами rows")
    assert exp is not None and exp.target == r"C:\w\run\out\profile.json"


def test_failed_attempt_is_decided_by_the_declared_post_state():
    from bcc.finalize import _effect_problem
    failed = {"tool": "terminal.run", "args": {"command": "python a.py > out.json"}, "status": "executed",
              "result_preview": "exit_code=1\nTraceback"}
    fixed = {"tool": "terminal.run", "args": {"command": "python b.py > out.json"}, "status": "executed",
             "result_preview": "exit_code=0"}
    # no contract: a failed mutation still fails the task (unchanged)
    assert _effect_problem([failed, fixed], []) == "effectful terminal outcome is failed or still unobserved"


def test_an_arrow_names_the_output():
    exp = _terminal_evidence(r"Данные: C:\w\run\orders.csv. Выручка по месяцам -> C:\w\run\out\monthly_region.csv")
    assert exp is not None and exp.target == r"C:\w\run\out\monthly_region.csv"


def test_without_an_output_marker_the_first_file_stays():
    exp = _terminal_evidence(r"Прочитай C:\w\run\orders.csv и посчитай строки")
    assert exp is not None and exp.target == r"C:\w\run\orders.csv"


# --- Long-horizon run 31.10, task 857 (synthetic): a failed attempt superseded by a LATER successful call ---------
# Rows replay the live chain: 1248 `python3` (exit 49), 1249 `import pandas` (exit 1), 1250 csv rewrite (exit 0) that
# wrote the outputs. The Auto Full prompt uses relative paths, so the declared evidence is `out/monthly_region.csv`.

REFUSED = "effectful terminal outcome is failed or still unobserved"
T0 = 1_900_000_000


def _at(seconds):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(T0 + seconds, tz=timezone.utc).replace(tzinfo=None)   # DB stores naive UTC


def _row(cmd, preview, seconds):
    return {"tool": "terminal.run", "source": "terminal", "status": "executed",
            "args": {"command": cmd, "mode": "project_host"}, "result_preview": preview, "created_at": _at(seconds)}


def _lh_rows(*, first_preview="exit_code=49\nPython was not found", success_cmd=None):
    return [_row("mkdir -p out && python3 -c \"import pandas\"", first_preview, 0),
            _row("mkdir -p out && python -c \"import pandas\"", "exit_code=1\nModuleNotFoundError: pandas", 10),
            _row(success_cmd or "mkdir -p out && python -c \"import csv\"", "exit_code=0\nmonthly_region.csv", 20)]


def _lh_output(root, seconds):
    import os
    out = root / "out" / "monthly_region.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("region,month,revenue\n", encoding="utf-8")
    os.utime(out, (T0 + seconds, T0 + seconds))
    return out


def _lh_expected():
    from bcc.v2.verification import parse_expected
    return parse_expected([{"kind": "file", "target": "out/monthly_region.csv", "expect": {"exists": True}}])


def test_lh857_failed_attempts_superseded_by_later_success_on_relative_output(tmp_path):
    from bcc.finalize import _effect_problem
    _lh_output(tmp_path, 25)                                         # written while the successful call ran
    (tmp_path / "Lib").mkdir()
    assert _effect_problem(_lh_rows(), _lh_expected(), None, [tmp_path / "Lib", tmp_path]) == ""


def test_lh857_relative_output_without_roots_is_not_forgiven(tmp_path):
    """No verifier roots -> a relative target cannot be located -> strict (the pre-fix live behaviour)."""
    from bcc.finalize import _effect_problem
    _lh_output(tmp_path, 25)
    assert _effect_problem(_lh_rows(), _lh_expected(), None, None) == REFUSED


def test_failed_only_with_its_own_partial_output_is_refused(tmp_path):
    from bcc.finalize import _effect_problem
    _lh_output(tmp_path, 15)
    assert _effect_problem(_lh_rows()[:2], _lh_expected(), None, [tmp_path]) == REFUSED   # no success at all


def test_failure_then_success_on_a_different_output_is_refused(tmp_path):
    """The owed file was last written BEFORE the successful call started (by the crashed attempt) and the success
    wrote some other file: the failure is not superseded."""
    from bcc.finalize import _effect_problem
    _lh_output(tmp_path, 15)                                         # partial output of attempt 2 (t0+10)
    rows = _lh_rows(success_cmd="python -c \"open('out/other.csv','w')\" > log.txt")
    assert _effect_problem(rows, _lh_expected(), None, [tmp_path]) == REFUSED


def test_unobserved_attempt_is_never_superseded(tmp_path):
    """No exit code observed (still running / lost): its effect is unknown and may still be writing."""
    from bcc.finalize import _effect_problem
    _lh_output(tmp_path, 25)
    rows = _lh_rows(first_preview="still running")
    assert _effect_problem(rows, _lh_expected(), None, [tmp_path]) == REFUSED


def test_success_must_come_after_the_failure(tmp_path):
    from bcc.finalize import _effect_problem
    _lh_output(tmp_path, 25)
    rows = _lh_rows()
    rows = [rows[2], rows[0], rows[1]]                               # the success is the EARLIEST row
    for r, seconds in zip(rows, (0, 10, 20)):
        r["created_at"] = _at(seconds)
    assert _effect_problem(rows, _lh_expected(), None, [tmp_path]) == REFUSED


def test_a_later_read_only_success_supersedes_nothing(tmp_path):
    from bcc.finalize import _effect_problem
    _lh_output(tmp_path, 25)
    rows = _lh_rows(success_cmd="ls out")                            # a read: not an effectful retry
    assert _effect_problem(rows, _lh_expected(), None, [tmp_path]) == REFUSED


def test_ambiguous_relative_output_is_not_forgiven(tmp_path):
    from bcc.finalize import _effect_problem
    a, b = tmp_path / "a", tmp_path / "b"
    _lh_output(a, 25)
    _lh_output(b, 25)
    assert _effect_problem(_lh_rows(), _lh_expected(), None, [a, b]) == REFUSED


def test_unobserved_success_supersedes_nothing(tmp_path):
    from bcc.finalize import _effect_problem
    _lh_output(tmp_path, 25)
    rows = _lh_rows()
    rows[2]["result_preview"] = "still running"                      # the "success" itself is unobserved
    assert _effect_problem(rows, _lh_expected(), None, [tmp_path]) == REFUSED
