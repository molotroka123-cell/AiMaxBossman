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
