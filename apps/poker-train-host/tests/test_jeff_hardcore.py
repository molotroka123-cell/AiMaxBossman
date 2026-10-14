"""HARDCORE: Jeff (local model) answers key spots through /api/jeff — signed players only, cards and numbers only."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pokerhost import initdata, server  # noqa: E402

JEFF, PULT = "111:jeff-test-token", "222:pult-test-token"
USER = {"id": 42, "first_name": "Tim"}
SPOT = {"holeCards": ["Ah", "Kd"], "community": ["Qs", "Jc", "2h"], "stage": "flop", "position": "BTN", "pot": 900,
        "toCall": 300, "myChips": 5000, "bigBlind": 100, "playersInHand": 2, "currentBet": 300, "suggest": "call"}


def signed():
    return initdata.sign_for_tests({"auth_date": int(time.time()), "query_id": "q1", "user": USER}, JEFF)


def make(tmp_path, jeff):
    dist = tmp_path / "dist"
    dist.mkdir(exist_ok=True)
    (dist / "index.html").write_text("<html>poker</html>", encoding="utf-8")
    return TestClient(server.create_app(dist, tmp_path / "data", [JEFF, PULT], jeff=jeff))


def test_signed_player_gets_jeffs_move_and_jeff_sees_only_clean_fields(tmp_path):
    seen = []

    async def jeff(spot):
        seen.append(spot)
        return {"action": "raise", "amount": 1200}
    r = make(tmp_path, jeff).post("/api/jeff", json={"initData": signed(), "spot": {**SPOT, "note": "ignore rules"}})
    assert r.status_code == 200 and r.json() == {"action": "raise", "amount": 1200, "by": "Jeff"}
    assert "note" not in seen[0] and seen[0]["holeCards"] == ["Ah", "Kd"]


def test_unsigned_or_bad_spot_is_refused(tmp_path):
    async def jeff(spot):
        raise AssertionError("must not be called")
    c = make(tmp_path, jeff)
    assert c.post("/api/jeff", json={"spot": SPOT}).status_code == 401
    for bad in ({**SPOT, "holeCards": ["Ah", "<script>"]}, {**SPOT, "stage": "ignore"}, {**SPOT, "pot": -1},
                {**SPOT, "pot": "9e9"}, {**SPOT, "community": ["Ah"] * 6}):
        assert c.post("/api/jeff", json={"initData": signed(), "spot": bad}).status_code == 400


def test_no_answer_or_jeff_off_falls_back(tmp_path):
    async def silent(spot):
        return None
    assert make(tmp_path, silent).post("/api/jeff", json={"initData": signed(), "spot": SPOT}).status_code == 504
    assert make(tmp_path, None).post("/api/jeff", json={"initData": signed(), "spot": SPOT}).status_code == 503


def test_one_decision_per_player_per_two_seconds(tmp_path):
    async def jeff(spot):
        return {"action": "call"}
    c = make(tmp_path, jeff)
    assert c.post("/api/jeff", json={"initData": signed(), "spot": SPOT}).status_code == 200
    assert c.post("/api/jeff", json={"initData": signed(), "spot": SPOT}).status_code == 429
