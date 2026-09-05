"""TZ-01 §2.1 (EH-01): улика verified=True доверяется только с валидной HMAC-подписью
доверенного signer'а. Приёмка: test_evidence_unsigned_verified_is_rejected,
test_evidence_signature_tamper (+ цепочка журнал → улика)."""
from __future__ import annotations

import os
import stat

import pytest

import bossman._shared  # noqa: F401
from bossman_shared import evidence as ev
from bossman_v3.memory.journal import TaskJournal
from bossman_v3.organization import DelegationContract, Evidence, WorkResult
from bossman_v3.organization.contracts import EvidenceRequirement


def _contract():
    from bossman_v3.organization import Resources
    return DelegationContract(work_id="w1", mission_id="m1", department_id="engineering", goal="записать файл",
                              required_capability="fs.write", success_criteria=["файл существует"],
                              evidence_required=[EvidenceRequirement("file", "/tmp/x")],
                              budget=Resources(usd=1.0, tokens=1000, compute_seconds=60))


def test_evidence_unsigned_verified_is_rejected():
    ok, errors = _contract().validate(WorkResult("w1", executed=True, evidence=[
        Evidence("file", "/tmp/x", True, source="journal:m1__w1/s1")]))
    assert not ok and any("unsigned verified evidence" in e for e in errors)
    # префикс source ничего не доказывает и для «верификатора»
    ok, errors = _contract().validate(WorkResult("w1", executed=True, evidence=[
        Evidence("file", "/tmp/x", True, source="bcc.v2.verification")]))
    assert not ok


def test_signed_evidence_is_accepted_and_roundtrips():
    e = Evidence.signed("file", "/tmp/x", source="journal:m1__w1/s1", observed_at="t")
    assert e.signature_valid() and e.signer == "bossman_v3.memory.journal" and len(e.sig) == 64
    ok, errors = _contract().validate(WorkResult("w1", executed=True, evidence=[e]))
    assert ok and errors == []
    again = Evidence.from_dict(e.to_dict())
    assert again.signature_valid()
    assert WorkResult.from_dict(WorkResult("w1", True, [e]).to_dict()).evidence[0].signature_valid()


def test_evidence_signature_tamper():
    e = Evidence.signed("file", "/tmp/x", source="journal:m1__w1/s1")
    body = {k: v for k, v in e.to_dict().items() if k != "sig"}
    assert ev.verify(body, e.sig)
    tampered = dict(body); tampered["ref"] = "/tmp/y"
    assert ev.verify(tampered, e.sig) is False
    # один байт подписи
    flipped = ("0" if e.sig[0] != "0" else "1") + e.sig[1:]
    assert ev.verify(body, flipped) is False
    forged = Evidence.from_dict({**e.to_dict(), "ref": "/tmp/y"})
    ok, errors = _contract().validate(WorkResult("w1", executed=True, evidence=[forged]))
    assert not ok and any("signature invalid" in err for err in errors)


def test_untrusted_signer_and_foreign_key_are_rejected():
    with pytest.raises(ValueError):
        ev.sign_fields({"kind": "file"}, signer="model:self-report")
    body = {"kind": "file", "ref": "/tmp/x", "verified": True, "source": "journal:x", "observed_at": "", "detail": ""}
    fields = ev.sign_fields(body, signer="bossman_v3.verifier", key=b"k" * 32)   # чужой ключ
    e = Evidence.from_dict({**body, **fields})
    assert e.signature_valid() is False
    fake = Evidence.from_dict({**body, **fields, "signer": "model:self-report"})
    assert fake.signature_valid() is False


def test_key_file_created_private_and_stable(tmp_path):
    p = ev.key_path()
    k1 = ev.load_or_create_key()
    assert p.exists() and len(k1) == 32
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    ev.reset_cache()
    assert ev.load_or_create_key() == k1
    # без ключа — проверить нельзя (fail-closed), подписать нельзя
    sig = ev.sign({"a": 1})
    p.write_bytes(b"short"); ev.reset_cache()
    assert ev.verify({"a": 1}, sig) is False
    with pytest.raises(ev.EvidenceKeyUnavailable):
        ev.sign({"a": 1})


def test_journal_step_is_signed_only_when_closed(tmp_path):
    j = TaskJournal.start(task_id="t1", plan=[("s1", "write"), ("s2", "check")], root=tmp_path / "j")
    j.record("s1", receipt={"path": "/tmp/x"}, verified=False, by="worker")
    assert j.steps[0].sig == "" and not j.steps[0].finished
    j.record("s1", receipt={"path": "/tmp/x"}, verified=True, by="worker")
    assert j.steps[0].finished and j.steps[0].signature_valid("t1")
    loaded = TaskJournal.load(task_id="t1", root=tmp_path / "j")
    assert loaded.steps[0].signature_valid("t1")
    # подмена receipt в файле журнала → подпись невалидна → шаг не даёт улики
    import json
    path = tmp_path / "j" / "t1.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["steps"][0]["receipt"] = {"path": "/tmp/evil"}
    path.write_text(json.dumps(raw), encoding="utf-8")
    forged = TaskJournal.load(task_id="t1", root=tmp_path / "j")
    assert forged.steps[0].finished and not forged.steps[0].signature_valid("t1")
    assert not forged.steps[1].signature_valid("t1")
