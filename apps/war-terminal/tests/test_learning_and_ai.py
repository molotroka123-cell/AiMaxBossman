import json

from warterminal import ai, learning
from warterminal.store import Store
from conftest import Env


def _prepare(env):
    env.ingest_ted()
    env.req("POST", "/ingest", "analyst", json={"source": "demo"})


def test_demo_outcomes_are_refused_for_learning(env):
    _prepare(env)
    r = env.req("POST", "/learning/outcomes", "owner", json={"tender_key": "EU:DEMO-001", "result": "won"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "DEMO_NOT_LEARNABLE"


def test_decisions_outcomes_and_calibration_say_when_data_is_insufficient(env):
    env.ingest_ted()
    d = env.req("POST", "/learning/decisions", "owner", json={"tender_key": "EU:100001-2026", "decision": "go", "reasons": "simple fuel supply, supplier known"})
    assert d.status_code == 200
    assert env.req("POST", "/learning/outcomes", "owner", json={"tender_key": "EU:100001-2026", "result": "won", "actual_margin_pct": 12.5}).status_code == 200
    cal = env.req("GET", "/learning/calibration", "viewer").json()
    assert cal["total_closed_outcomes"] == 1 and "недостаточно" in cal["note"]
    tier = next(iter(cal["by_predicted_tier"].values()))
    assert tier["enough_data"] is False and tier["win_rate_smoothed"] == 0.667             # (1+1)/(1+2): no pretending at n=1
    prop = env.req("GET", "/learning/proposal", "viewer").json()
    assert prop["status"] == "INSUFFICIENT_DATA" and prop["n"] == 1
    assert env.req("POST", "/learning/proposal/apply", "owner").status_code == 409


def test_weight_proposal_needs_enough_data_is_bounded_and_needs_approval(tmp_path):
    store = Store(tmp_path / "w.db")
    for i in range(40):
        won = i % 2 == 0
        comps = {k: {"weight": w, "value": (0.9 if won else 0.2) if k == "suppliers" else 0.5} for k, w in
                 {"qualification": 25, "suppliers": 20, "capital": 20, "time": 15, "experience": 10, "risk": 10}.items()}
        key = f"EU:T{i}"
        learning.record_decision(store, key, "go", "test", {"tier_if_cleared": "EASY", "components": comps})
        learning.record_outcome(store, key, "won" if won else "lost", None, ["09"], "")
    prop = learning.propose_weights(store)
    assert prop["status"] == "PROPOSAL" and abs(sum(prop["weights"].values()) - 100) < 0.5
    assert prop["weights"]["suppliers"] > 20 and prop["weights"]["suppliers"] <= 26                # moved, but by at most ~5 points
    assert "owner approval" in prop["requires"]


def test_dataset_export_is_owner_only_redacted_and_marks_demo(env):
    env.ingest_ted()
    env.req("POST", "/learning/decisions", "owner", json={"tender_key": "EU:100001-2026", "decision": "watch", "reasons": "call me at +420 777 123 456 or a@b.cz"})
    text = env.req("GET", "/learning/dataset", "owner").text
    row = json.loads(text.splitlines()[0])
    assert "[phone]" in row["owner_reasons"] and "[email]" in row["owner_reasons"] and "777" not in text and "a@b.cz" not in text
    assert row["owner_decision"] == "watch" and row["demo"] is False


def test_ai_adapter_is_advisory_budgeted_and_survives_bad_replies(tmp_path):
    store = Store(tmp_path / "ai.db")
    t = {"title": "Fuel mail me@x.org", "category": "fuel_energy", "country": "CZE", "estimated_value": 1, "currency": "EUR", "cpv": ["09"], "status": "open"}
    assert ai.classify(t, None, store)["status"] == "NOT_CONNECTED"
    sent = []

    def good(msgs):
        sent.append(msgs)
        return 'noise {"tier":"MEDIUM","rationale":"needs local carriers","risks":["margin"]} tail'
    r = ai.classify(t, good, store)
    assert r["status"] == "OK" and r["tier"] == "MEDIUM" and "me@x.org" not in json.dumps(sent) and "[email]" in json.dumps(sent)
    assert ai.classify(t, lambda m: "not json", store)["status"] == "ERROR"
    assert ai.classify(t, lambda m: '{"tier":"EXCELLENT"}', store)["status"] == "ERROR"
    assert ai.classify(t, good, store, daily_limit=3)["status"] == "BUDGET_EXCEEDED"
    assert ai.disagreement("EASY", "HARD") is True and ai.disagreement("EASY", "EASY") is False and ai.disagreement("REVIEW_REQUIRED", "EASY") is False


def test_assessment_with_ai_never_changes_gates(tmp_path):
    e = Env(tmp_path)
    e.app.state.store  # same store the app uses
    e.ingest_ted()
    r = e.req("POST", "/assessments", "analyst", json={"tender_key": "EU:100001-2026", "use_ai": True}).json()
    assert r["ai"]["status"] == "NOT_CONNECTED" and r["complexity"] == "REVIEW_REQUIRED"
