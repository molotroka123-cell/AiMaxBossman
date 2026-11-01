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


# --- Long-horizon run 31.10 (rerun on 03b66902), task 876 «findings» (synthetic) ----------------------------------
# Evidence extraction took the INPUT `orders.csv`; calls 1259-1262 `python -c` analyses exited 1 (f-string
# SyntaxError x2, no pandas, KeyError), 1263 printed headers (exit 0, wrote nothing), 1264 printed the findings
# (exit 0), 1265 wrote notes/03_findings.md (exit 0).

LH876_PROMPT = (
    "Все команды выполняй через terminal.run с mode=\"project_host\" (поле cwd НЕ передавай — рабочая папка уже "
    "выбрана в карточке владельца Auto Full, используй относительные пути внутри текущей папки). Используй Python "
    "(`python -c \"...\"`). Данные: `orders.csv` в текущей папке (колонки order_id,date,region,product,qty,"
    "unit_price,unit_cost,customer_id; есть дубли order_id — считай по первой встреченной строке). Выход: только "
    "внутри текущей папки. Не используй сеть. ШАГ 3 из 5. Выполни в ДВА отдельных шага (два отдельных вызова модели "
    "с terminal.run): Сначала ПЕРВЫМ вызовом terminal.run прочитай `out/product_margin.csv`, `out/top_customers.csv` "
    "и `out/monthly_region.csv` и выведи в консоль худший продукт по марже, топ-1 клиента по доле выручки и падение "
    "выручки региона Север (последние 6 мес к первым 12 мес). Затем ВТОРЫМ вызовом terminal.run запиши эти три "
    "проверяемые находки (продукт P-07, клиент C-0042, регион Север) с числами и именами файлов-источников в "
    "`notes/03_findings.md`.")


def test_lh876_the_file_written_into_is_the_evidence_not_the_input():
    exp = _terminal_evidence(LH876_PROMPT)
    assert exp is not None and exp.target == "notes/03_findings.md"


def test_a_file_read_into_is_not_an_output():
    """«прочитай данные в X» — the last verb before X is a read: X is not the written target."""
    exp = _terminal_evidence("Запиши итог в `notes/a.md`, затем прочитай данные в `orders.csv`.")
    assert exp is not None and exp.target == "notes/a.md"
    from bcc.features.action_contract import _written_into
    text = "Запиши итог в `notes/a.md`, затем прочитай данные в `orders.csv`."
    assert not _written_into(text, text.index("orders.csv"))


def test_a_write_verb_in_another_sentence_does_not_make_an_input_an_output():
    from bcc.features.action_contract import _written_into
    text = "Запиши отчёт. Данные лежат в `orders.csv`."
    assert not _written_into(text, text.index("orders.csv"))
    exp = _terminal_evidence("Прочитай `orders.csv` и выведи итог в консоль")
    assert exp is not None and exp.target == "orders.csv"                  # nothing written: old fallback


def _row876(cmd, preview, seconds, cwd, *, finished=1.2):
    row = _row(cmd, preview, seconds)
    row["args"]["cwd"] = str(cwd)
    row["finished_at"] = _at(seconds + finished) if finished is not None else None
    return row


def _lh876(tmp_path, *, write_at=None, write_root=None, success_finished=1.2, cwd=None):
    """Run folder with the pre-existing input orders.csv; rows 1259..1265; 1265 optionally writes the findings."""
    import os
    run = tmp_path / "run"
    (run / "out").mkdir(parents=True)
    orders = run / "orders.csv"
    orders.write_text("order_id,date\n1,2025-01-01\n", encoding="utf-8")
    os.utime(orders, (T0 - 600, T0 - 600))                                   # created by the driver before the run
    cwd = cwd or run
    rows = [_row876(f"python -c 'import pandas  # attempt {i}'", "exit_code=1\nTraceback", 10 * i, cwd)
            for i in range(4)]                                               # 1259-1262
    rows.append(_row876("python -c 'print(open(\"out/x.csv\").readline())'", "exit_code=0\nheaders", 40, cwd))
    rows.append(_row876("python -c 'print(\"P-07 C-0042 32.1\")'", "exit_code=0\nP-07", 50, cwd))
    rows.append(_row876("python -c 'open(\"notes/03_findings.md\",\"w\")'", "exit_code=0\n", 60, cwd,
                        finished=success_finished))
    if write_at is not None:
        notes = (write_root or run) / "notes" / "03_findings.md"
        notes.parent.mkdir(parents=True, exist_ok=True)
        notes.write_text("P-07, C-0042 15.9 %, Север -32.1 %", encoding="utf-8")
        os.utime(notes, (T0 + write_at, T0 + write_at))
    from bcc.v2.verification import parse_expected
    expected = parse_expected([{"kind": "file", "target": "orders.csv", "expect": {"exists": True}}])
    return run, rows, expected


def test_lh876_replay_input_evidence_superseded_by_a_later_success_that_wrote_a_file(tmp_path):
    from bcc.finalize import _effect_problem
    run, rows, expected = _lh876(tmp_path, write_at=60.5)                   # inside 1265's window
    assert _effect_problem(rows, expected, None, [run]) == ""


def test_lh876_failures_only_refused(tmp_path):
    from bcc.finalize import _effect_problem
    run, rows, expected = _lh876(tmp_path)
    assert _effect_problem(rows[:4], expected, None, [run]) == REFUSED       # no success at all
    assert _effect_problem(rows, expected, None, [run]) == REFUSED           # successes, but none wrote a file


def test_input_evidence_success_whose_file_predates_its_window_is_refused(tmp_path):
    """The fresh file was written by a FAILED attempt (t0+35), before the successful call started (t0+60)."""
    from bcc.finalize import _effect_problem
    run, rows, expected = _lh876(tmp_path, write_at=35)
    assert _effect_problem(rows, expected, None, [run]) == REFUSED


def test_input_evidence_success_writing_outside_the_roots_is_refused(tmp_path):
    from bcc.finalize import _effect_problem
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    run, rows, expected = _lh876(tmp_path, write_at=60.5, write_root=outside, cwd=outside)
    assert _effect_problem(rows, expected, None, [run]) == REFUSED


def test_input_evidence_unfinished_success_is_refused(tmp_path):
    from bcc.finalize import _effect_problem
    run, rows, expected = _lh876(tmp_path, write_at=60.5, success_finished=None)
    assert _effect_problem(rows, expected, None, [run]) == REFUSED


def test_input_evidence_without_roots_is_refused(tmp_path):
    from bcc.finalize import _effect_problem
    run, rows, expected = _lh876(tmp_path, write_at=60.5)
    assert _effect_problem(rows, expected, None, None) == REFUSED


def test_a_declared_file_modified_during_the_run_is_an_output_not_an_input(tmp_path):
    """orders.csv rewritten by a crashed attempt (t0+15) is no longer an input: it must be rewritten after a later
    success started, and the findings file the success wrote does not stand in for it."""
    import os
    from bcc.finalize import _effect_problem
    run, rows, expected = _lh876(tmp_path, write_at=60.5)
    os.utime(run / "orders.csv", (T0 + 15, T0 + 15))
    assert _effect_problem(rows, expected, None, [run]) == REFUSED
