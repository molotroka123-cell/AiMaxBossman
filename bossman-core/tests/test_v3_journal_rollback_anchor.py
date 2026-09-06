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


# ------------------------------------------------- сквозная истина исполнения
#
# Отказ загрузки — половина утверждения. Вторая половина: из-за этого отказа
# необратимый эффект НЕ повторяется. Ниже — настоящий внешний побочный эффект в
# дедуплицирующем НИЧЕГО журнале-леджере: повтор виден как лишняя строка.

_ROLLBACK_WORKER = '''
import json, os, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, {core!r})
sys.path.insert(0, {repo!r})
from bossman_v3.computer_agent.agent import UniversalComputerAgent
from bossman_v3.contracts import (ApprovalDecision, ExecutionReceipt, Observation,
                                  PolicyDecision, SideEffectClass, TypedAction,
                                  VerificationResult)
from bossman_v3.execution import CompoundRunner, PlanStep
from bossman_v3.memory.journal import TaskJournal, JournalRollbackError

ROOT = Path({root!r})
LEDGER = ROOT / "effects.jsonl"


class Service:
    """Леджер СПЕЦИАЛЬНО без дедупликации: повтор эффекта виден как вторая строка."""

    def authorize(self, action, context): return PolicyDecision(True)
    def request(self, action, policy, context): return ApprovalDecision(False, reason="fixture")
    def supports(self, action_type): return action_type == "ledger.append"

    def execute(self, action):
        started = datetime.now(timezone.utc)
        with LEDGER.open("ab") as stream:
            stream.write(json.dumps({{"effect": action.args["effect"]}}).encode() + b"\\n")
            stream.flush(); os.fsync(stream.fileno())
        return ExecutionReceipt(action.action_type, started, datetime.now(timezone.utc),
                                effect_id=action.args["effect"])

    def observe_fresh(self, action, receipt):
        rows = [json.loads(x) for x in LEDGER.read_bytes().splitlines()]
        return Observation(datetime.now(timezone.utc), "fs",
                           {{"count": sum(r["effect"] == action.args["effect"] for r in rows)}})

    def verify(self, action, receipt, observation):
        return VerificationResult(observation.state["count"] == 1,
                                  reason="external append count must equal one")


PLAN = [PlanStep("s1", "append s1", TypedAction(
    "ledger.append", {{"target": str(LEDGER), "effect": "run/s1"}},
    side_effect=SideEffectClass.IRREVERSIBLE))]
JOURNALS = ROOT / "journals"
mode = sys.argv[1]
try:
    journal = (TaskJournal.load(task_id="irreversible", root=JOURNALS) if mode == "resume"
               else TaskJournal.start(task_id="irreversible", root=JOURNALS,
                                      plan=[(s.step_id, s.intent) for s in PLAN]))
except JournalRollbackError as exc:
    print("REFUSED:" + str(exc)); raise SystemExit(0)
svc = Service()
result = CompoundRunner(UniversalComputerAgent(svc, svc, svc, svc, svc), journal,
                        model="fixture").run(PLAN)
print("COMPLETED" if result.completed else "NOT_COMPLETED")
'''


def _worker(tmp_path: Path, mode: str) -> subprocess.CompletedProcess:
    core = str(Path(__file__).resolve().parents[1])
    code = _ROLLBACK_WORKER.format(core=core, repo=str(Path(core).parent), root=str(tmp_path))
    return subprocess.run([sys.executable, "-c", code, mode], capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": core,
                               "BOSSMAN_REAL_WORKLOAD_ROOT": str(tmp_path / "bench")})


def test_a_rolled_back_journal_cannot_replay_an_irreversible_effect(tmp_path):
    """Сквозное утверждение: откат журнала не приводит к повторному эффекту.

    Не «загрузка отказала», а «внешний леджер, который НИЧЕГО не дедуплицирует,
    остался с одной строкой». Это и есть та атака, ради которой якорь существует:
    восстановить вчерашний валидный журнал и переиграть необратимый шаг.
    """
    ledger = tmp_path / "effects.jsonl"
    ledger.write_bytes(b"")
    journals = tmp_path / "journals"

    first = _worker(tmp_path, "start")
    assert "COMPLETED" in first.stdout, f"предпосылка не выполнена: {first.stdout} {first.stderr}"
    assert ledger.read_bytes().count(b"\n") == 1

    # Снимок ПОСЛЕ начала, но ДО закрытия шага: валидно подписан, но устарел.
    stale = json.loads(_captured(journals))
    _restore(journals, stale)

    second = _worker(tmp_path, "resume")
    assert "REFUSED" in second.stdout, f"откат принят: {second.stdout} {second.stderr}"
    assert ledger.read_bytes().count(b"\n") == 1, (
        "необратимый эффект повторён после отката журнала")


_CAPTURED: dict[str, str] = {}


def _captured(journals: Path) -> str:
    """Валидный снапшот ранней стадии добывается из самого прогона: журнал
    пишется несколько раз, и первая запись — законная история."""
    path = journal_path(journals, "irreversible")
    if "snapshot" not in _CAPTURED:
        # Собственный ранний снапшот того же журнала: seq=1, подпись валидна.
        raw = json.loads(path.read_text(encoding="utf-8"))
        early = dict(raw)
        early["anchor_seq"] = 1
        for step in early["steps"]:
            step.update(status="PENDING", receipt=None, verified=False, in_flight=False,
                        sig="", signer="", nonce="", issued_at="")
        from bossman_v3 import evidence as signing
        for field in ("sig", "signer", "nonce", "issued_at"):
            early.pop(field, None)
        early.update(signing.sign_fields(early, signer=signing.JOURNAL_SIGNER))
        _CAPTURED["snapshot"] = json.dumps(early, ensure_ascii=False, indent=2, sort_keys=True)
    return _CAPTURED["snapshot"]


def _restore(journals: Path, snapshot: dict) -> None:
    journal_path(journals, "irreversible").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
