"""Откат валидной истории журнала (V4 release boundary A).

Подпись снапшота доказывает, что файл НЕ ПЕРЕПИСАН. Она ничего не говорит про
ПОДМЕНУ файла более старым, тоже валидно подписанным, снапшотом того же журнала:
вчерашняя копия проходит `verify_signed` не хуже сегодняшней. Ровно на этом
держится атака «восстановим бэкап и переиграем необратимый шаг».

Здесь проверяется монотонный якорь: отдельная подписанная запись
{task_id, seq, snapshot_sha256}, которая только растёт и живёт вне журнала.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from bossman_v3.memory.journal import (JournalIntegrityError, JournalRollbackError,
                                       TaskJournal, anchor_path, journal_path,
                                       read_anchor, write_anchor)

PLAN = [("s1", "первый шаг"), ("s2", "второй шаг")]


def _started(root: Path, task_id: str = "rollback-demo") -> TaskJournal:
    return TaskJournal.start(task_id=task_id, plan=PLAN, root=root)


def test_a_fresh_journal_creates_a_signed_monotonic_anchor(tmp_path):
    j = _started(tmp_path)
    anchor = read_anchor(tmp_path, "rollback-demo")
    assert anchor is not None and anchor["seq"] == 1
    raw = json.loads(journal_path(tmp_path, "rollback-demo").read_text(encoding="utf-8"))
    assert raw["anchor_seq"] == 1
    j.begin("s1", by="test")
    assert read_anchor(tmp_path, "rollback-demo")["seq"] == 2


def test_an_older_but_validly_signed_snapshot_is_refused(tmp_path):
    """Ядро находки: старый снапшот проходит проверку подписи и всё равно отвергнут."""
    from bossman_v3 import evidence as signing

    j = _started(tmp_path)
    path = journal_path(tmp_path, "rollback-demo")
    stale = path.read_bytes()                      # валидно подписанный snapshot seq=1
    j.begin("s1", by="test")                       # seq=2
    j.record("s1", receipt={"ok": True}, verified=True, by="test")   # seq=3

    assert signing.verify_signed(json.loads(stale)) is True, (
        "предпосылка теста: старый снапшот подписан валидно")

    path.write_bytes(stale)                        # откат к валидной истории
    with pytest.raises(JournalRollbackError) as err:
        TaskJournal.load(task_id="rollback-demo", root=tmp_path)
    assert "older than durable anchor" in str(err.value)


def test_the_anchor_survives_a_real_process_restart(tmp_path):
    """Якорь durable: он проверяется свежим интерпретатором, а не памятью процесса."""
    j = _started(tmp_path)
    path = journal_path(tmp_path, "rollback-demo")
    stale = path.read_bytes()
    j.begin("s1", by="test")
    j.record("s1", receipt={"ok": True}, verified=True, by="test")
    path.write_bytes(stale)

    code = (
        "import sys, json\n"
        "sys.path.insert(0, %r)\n"
        "from bossman_v3.memory.journal import TaskJournal, JournalRollbackError\n"
        "try:\n"
        "    TaskJournal.load(task_id='rollback-demo', root=%r)\n"
        "except JournalRollbackError as exc:\n"
        "    print('REFUSED'); sys.exit(0)\n"
        "print('ACCEPTED'); sys.exit(1)\n"
    ) % (str(Path(__file__).resolve().parents[1]), str(tmp_path))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])})
    assert out.returncode == 0, f"stdout={out.stdout} stderr={out.stderr}"
    assert "REFUSED" in out.stdout


def test_deleting_the_journal_does_not_grant_a_fresh_replay(tmp_path):
    """Удалить журнал — не то же самое, что не начинать задачу: якорь помнит."""
    j = _started(tmp_path)
    j.begin("s1", by="test")
    journal_path(tmp_path, "rollback-demo").unlink()
    with pytest.raises(JournalRollbackError):
        _started(tmp_path)


def test_removing_the_anchor_does_not_re_enable_the_stale_snapshot(tmp_path):
    """Снести якорь тоже нельзя: продвинутый снапшот без якоря — не «наследие»."""
    j = _started(tmp_path)
    j.begin("s1", by="test")
    anchor_path(tmp_path, "rollback-demo").unlink()
    with pytest.raises(JournalRollbackError) as err:
        TaskJournal.load(task_id="rollback-demo", root=tmp_path)
    assert "anchor missing" in str(err.value)


def test_a_forged_anchor_is_not_authority(tmp_path):
    """Якорь — улика: без валидной подписи он не понижает планку, а закрывает дверь."""
    j = _started(tmp_path)
    j.begin("s1", by="test")
    ap = anchor_path(tmp_path, "rollback-demo")
    forged = json.loads(ap.read_text(encoding="utf-8"))
    forged["seq"] = 1                                # «разрешим» старый снапшот
    ap.write_text(json.dumps(forged), encoding="utf-8")
    with pytest.raises(JournalIntegrityError) as err:
        TaskJournal.load(task_id="rollback-demo", root=tmp_path)
    assert "forged" in str(err.value) or "unsigned" in str(err.value)


def test_a_same_sequence_replacement_is_refused(tmp_path):
    """Подмена снапшота другим валидным снапшотом того же seq тоже ловится дайджестом."""
    other = tmp_path / "other"
    other.mkdir()
    j = _started(tmp_path)
    twin = TaskJournal.start(task_id="rollback-demo", plan=[("s1", "иной план")], root=other)
    shutil.copyfile(journal_path(other, "rollback-demo"), journal_path(tmp_path, "rollback-demo"))
    with pytest.raises(JournalRollbackError) as err:
        TaskJournal.load(task_id="rollback-demo", root=tmp_path)
    assert "does not match the durable anchor" in str(err.value)


def test_a_crash_between_snapshot_and_anchor_is_recoverable_not_a_rollback(tmp_path):
    """Обрыв ПОСЛЕ снапшота и ДО якоря оставляет snapshot=anchor+1. Это чинится."""
    j = _started(tmp_path)
    j.begin("s1", by="test")                       # snapshot seq=2, anchor seq=2
    a = read_anchor(tmp_path, "rollback-demo")
    write_anchor(tmp_path, "rollback-demo", seq=a["seq"] - 1,
                 snapshot_sha256="0" * 64)         # якорь «отстал», как после обрыва
    resumed = TaskJournal.load(task_id="rollback-demo", root=tmp_path)
    assert resumed.next_step().step_id == "s1"
    resumed.record("s1", receipt={"ok": True}, verified=True, by="test")
    assert read_anchor(tmp_path, "rollback-demo")["seq"] == 3


def test_the_anchor_store_can_live_outside_the_journal_directory(tmp_path, monkeypatch):
    """Право писать в каталог журналов не обязано давать право переписать якорь."""
    vault = tmp_path / "vault"
    monkeypatch.setenv("BOSSMAN_JOURNAL_ANCHOR_ROOT", str(vault))
    j = _started(tmp_path / "journals")
    assert not (tmp_path / "journals" / ".anchors").exists()
    assert any(vault.rglob("rollback-demo.anchor.json"))


def test_a_normal_crash_and_resume_still_works(tmp_path):
    """Якорь не должен ломать обычное возобновление — регрессия на happy path."""
    j = _started(tmp_path)
    j.begin("s1", by="test")
    j.record("s1", receipt={"ok": True}, verified=True, by="test")
    resumed = TaskJournal.load(task_id="rollback-demo", root=tmp_path)
    assert [s.step_id for s in resumed.finished_signed()] == ["s1"]
    assert resumed.next_step().step_id == "s2"
