import asyncio
import json

import pytest

from tools.owner_journeys import learning_247_readiness as rd
from tools.owner_journeys import lesson_pipeline as lp
from tools.owner_journeys import triage_dataset as ds

LESSON_ITEM = ds.HELD_OUT[0]
HARD = {h["text"] for h in ds.HELD_OUT[1:4]}      # baseline gets these wrong, the lesson fixes them


def fake_ask(*, gain=True, unsafe=False):
    async def ask(variant, system, text):
        item = next(h for h in ds.HELD_OUT if h["text"] == text)
        ans = {k: item[k] for k in ds.FIELDS}
        taught = LESSON_ITEM["text"] in system
        if text in HARD and not (taught and gain):
            ans["business"] = "none"
        if unsafe and taught and item["intent"] == "medical":
            ans["action"] = "answer_from_facts"
        return json.dumps(ans), 0.01
    return ask


class FakeBackend:
    def __init__(self):
        self.rows = {}

    def create(self, kind, preview):
        aid = len(self.rows) + 1
        self.rows[aid] = {"id": aid, "kind": kind, "preview": preview, "status": "pending", "decided_by": None}
        return self.rows[aid]

    def get(self, aid):
        return self.rows.get(aid)


def _seed(tmp_path, kind="triage"):
    exp = {k: LESSON_ITEM[k] for k in ds.FIELDS}
    rows = [{"cycle_id": 3, "kind": kind, "task": {"text": LESSON_ITEM["text"]}, "expected": exp,
             "got": {"business": "none"}, "status": "CANDIDATE_QUARANTINED"}]
    (tmp_path / "lesson_candidates.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    reg = lp.Registry(tmp_path)
    reg.ingest()
    return reg


def _proven(tmp_path):
    reg = _seed(tmp_path)
    les = reg.next_for_ab()
    ab = asyncio.run(lp.run_ab(les, [], fake_ask(), repeats=2, min_gain_items=2))
    assert lp.record_ab(reg, les["id"], ab) == "GAIN_PROVEN"
    return reg, les["id"]


def test_ingest_quarantines_triage_and_counts_kinds_without_harness(tmp_path):
    reg = _seed(tmp_path)
    with (tmp_path / "lesson_candidates.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"cycle_id": 4, "kind": "journey", "task": {"journey": "x"}}) + "\n")
        fh.write(json.dumps({"cycle_id": 5, "kind": "triage", "task": {"text": LESSON_ITEM["text"]},
                             "expected": {k: LESSON_ITEM[k] for k in ds.FIELDS}}) + "\n")
    reg.ingest()
    c = reg.counts()
    assert c["QUARANTINED"] == 1 and c["no_ab_harness"] == 1
    les = reg.next_for_ab()
    assert les["seen_failures"] == 2 and reg.active() == []


def test_ab_excludes_the_lessons_own_item_and_proves_only_repeatable_gain(tmp_path):
    reg = _seed(tmp_path)
    les = reg.next_for_ab()
    assert all(h["text"] != LESSON_ITEM["text"] for h in lp.eval_items(les, []))
    ab = asyncio.run(lp.run_ab(les, [], fake_ask(), repeats=2, min_gain_items=2))
    assert ab["verdict"] == "GAIN_PROVEN" and [r["delta"] for r in ab["repeats"]] == [3, 3]
    assert ab["n"] == len(ds.HELD_OUT) - 1
    flat = asyncio.run(lp.run_ab(les, [], fake_ask(gain=False), repeats=2, min_gain_items=2))
    assert flat["verdict"] == "NOT_PROVEN"
    unsafe = asyncio.run(lp.run_ab(les, [], fake_ask(unsafe=True), repeats=2, min_gain_items=2))
    assert unsafe["verdict"] == "NOT_PROVEN" and "new safety violations" in unsafe["reasons"]
    one = asyncio.run(lp.run_ab(les, [], fake_ask(), repeats=1, min_gain_items=2))
    assert one["verdict"] == "NOT_PROVEN"


def test_no_promotion_without_an_owner_approved_bound_row(tmp_path):
    reg, lid = _proven(tmp_path)
    be = FakeBackend()
    assert lp.request_approval(reg, lid, be) == "APPROVAL_REQUESTED"
    row = be.rows[1]
    assert row["kind"] == lp.LESSON_KIND and lid in row["preview"] and "rollback" in row["preview"]
    lp.poll_approvals(reg, be)
    assert reg.active() == [], "pending approval must not promote"
    row["status"] = "approved"                      # approved but nobody recorded as decider
    lp.poll_approvals(reg, be)
    assert reg.active() == []
    row["decided_by"] = "tg:user:7@chat:7"
    row["preview"] = row["preview"].replace("digest", "digest X")   # a different lesson's approval
    lp.poll_approvals(reg, be)
    assert reg.active() == [] and reg.read()["lessons"][lid]["status"] == "REJECTED_BY_OWNER"


def test_owner_approval_promotes_and_rollback_is_one_command(tmp_path):
    reg, lid = _proven(tmp_path)
    be = FakeBackend()
    lp.request_approval(reg, lid, be)
    be.rows[1].update(status="approved", decided_by="tg:user:7@chat:7")
    changes = lp.poll_approvals(reg, be)
    assert changes == [{"lesson": lid, "status": "PROMOTED", "by": "tg:user:7@chat:7"}]
    assert reg.active() == [{"text": LESSON_ITEM["text"], **{k: LESSON_ITEM[k] for k in ds.FIELDS}}]
    assert lp.main(["rollback", lid, "--state-dir", str(tmp_path)]) == 0
    assert reg.active() == [] and reg.read()["lessons"][lid]["status"] == "ROLLED_BACK"
    hist = [json.loads(x)["status"] for x in (tmp_path / "lessons" / "history.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert hist == ["QUARANTINED", "GAIN_PROVEN", "APPROVAL_REQUESTED", "PROMOTED", "ROLLED_BACK"]


def test_rejection_and_unavailable_backend(tmp_path, monkeypatch):
    reg, lid = _proven(tmp_path)
    assert lp.request_approval(reg, lid, None) == "APPROVAL_BACKEND_UNAVAILABLE"
    be = FakeBackend()
    lp.poll_approvals(reg, be)
    assert be.rows == {}, "retry waits for its back-off"
    data = reg.read()
    data["lessons"][lid]["retry_at"] = 0
    reg.write(data)
    lp.poll_approvals(reg, be)
    assert reg.read()["lessons"][lid]["status"] == "APPROVAL_REQUESTED"
    be.rows[1].update(status="rejected", decided_by="owner")
    lp.poll_approvals(reg, be)
    assert reg.read()["lessons"][lid]["status"] == "REJECTED_BY_OWNER" and reg.active() == []


def test_approval_backend_is_local_core_only():
    with pytest.raises(ValueError):
        lp.ApprovalBackend("https://example.com", "t")
    lp.ApprovalBackend("http://127.0.0.1:8820", "t")
    assert lp.ApprovalBackend.from_companion() is None      # conftest: no companion config


def test_readiness_flags_a_promotion_without_owner_approval(tmp_path):
    reg, lid = _proven(tmp_path)
    data = reg.read()
    data["lessons"][lid]["status"] = "PROMOTED"          # forged: no approval record
    reg.write(data)
    crit = rd.evaluate(tmp_path, ab_report=tmp_path / "none.json")["criteria"]["no_auto_promotion"]
    assert crit["pass"] is False and crit["value"]["promoted_without_owner_approval"] == [lid]
    data["lessons"][lid].update(approval_id=1, approved_by="tg:user:7@chat:7", approval_status="approved")
    reg.write(data)
    assert rd.evaluate(tmp_path, ab_report=tmp_path / "none.json")["criteria"]["no_auto_promotion"]["pass"]
