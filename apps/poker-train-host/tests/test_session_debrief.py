"""/api/session: the whole session goes to Jeff for a debrief — only for the Telegram-signed sender, bounded load."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pokerhost import analysis, initdata, server  # noqa: E402

JEFF, PULT, OTHER = "111:jeff-token-abc", "222:pult-token-def", "333:foreign"
USER = {"id": 5150, "first_name": "Max"}

SESSION = {"sessionId": "s1", "handSummaries": [
    {"hand": 1, "position": "BTN", "holeCards": ["As", "Kd"], "netProfit": 1200, "mistake": None, "evLost": 0,
     "actions": [{"stage": "preflop", "action": "raise"}]},
    {"hand": 2, "position": "UTG", "holeCards": ["7c", "2d"], "netProfit": -800, "mistake": "bad_call", "evLost": 450,
     "actions": [{"stage": "preflop", "action": "call"}, {"stage": "flop", "action": "call"}]},
    {"hand": 3, "position": "BB", "holeCards": ["Qh", "Qs"], "netProfit": -300, "mistake": "too_passive",
     "evLost": 900, "actions": [{"stage": "preflop", "action": "call"}]}],
    "records": [{"handNumber": 1, "stage": "preflop", "action": "raise"},
                {"handNumber": 2, "stage": "preflop", "action": "call"},
                {"handNumber": 2, "stage": "flop", "action": "call", "mistakeType": "bad_call", "evLost": 450},
                {"handNumber": 3, "stage": "preflop", "action": "call", "mistakeType": "too_passive", "evLost": 900}]}


def signed(token=JEFF, user=USER, age=0):
    return initdata.sign_for_tests({"auth_date": int(time.time() - age), "user": user}, token)


@pytest.fixture
def host(tmp_path):
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "index.html").write_text("x", encoding="utf-8")
    sent, coached = [], []

    async def deliver(bot, chat_id, text):
        sent.append((bot, chat_id, text))

    async def coach(summary):
        coached.append(summary)
        return "1. Совет."
    app = server.create_app(tmp_path / "dist", tmp_path / "data", [JEFF, PULT], bots={"jeff": JEFF, "pult": PULT},
                            deliver=deliver, coach=coach)
    return TestClient(app), sent, coached, tmp_path


def wait(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end and not cond():
        time.sleep(0.02)
    return cond()


def test_signed_session_is_debriefed_and_sent_back_to_that_player_through_jeff(host):
    client, sent, coached, tmp = host
    r = client.post("/api/session", json={"initData": signed(), "session": SESSION})
    assert r.status_code == 200 and r.json()["hands"] == 3
    assert wait(lambda: sent)
    [(bot, chat_id, text)] = sent
    assert bot == "jeff" and chat_id == USER["id"]
    assert "Рук: 3" in text and "Ошибок: 2" in text and "−900 EV" in text and "Рука #3" in text
    assert "Комментарий тренера" in text and coached and coached[0]["hands"] == 3
    saved = list((tmp / "data" / "sessions").glob("*/*.json"))
    assert len(saved) == 1 and str(USER["id"]) not in str(saved[0])      # stored under a hash, not the raw id


def test_owner_from_the_pult_gets_the_reply_through_the_pult_bot(host):
    client, sent, _c, _t = host
    assert client.post("/api/session", json={"initData": signed(PULT), "session": SESSION}).status_code == 200
    assert wait(lambda: sent) and sent[0][0] == "pult"


@pytest.mark.parametrize("init", [lambda: "", lambda: signed(OTHER), lambda: signed(age=3 * 86400),
                                  lambda: signed().replace("Max", "Eve")])
def test_unsigned_foreign_stale_or_tampered_sender_is_refused(host, init):
    client, sent, _c, tmp = host
    assert client.post("/api/session", json={"initData": init(), "session": SESSION}).status_code == 401
    time.sleep(0.1)
    assert sent == [] and not (tmp / "data" / "sessions").exists()


def test_one_debrief_per_player_per_two_minutes(host):
    client, sent, _c, _t = host
    assert client.post("/api/session", json={"initData": signed(), "session": SESSION}).status_code == 200
    r = client.post("/api/session", json={"initData": signed(), "session": SESSION})
    assert r.status_code == 429 and r.json()["error"] == "too_often"


def test_queue_is_bounded(host, monkeypatch):
    client, _s, _c, _t = host
    monkeypatch.setattr(server, "MAX_PENDING", 0)
    r = client.post("/api/session", json={"initData": signed(), "session": SESSION})
    assert r.status_code == 429 and r.json()["error"] == "busy"


def test_session_size_and_shape_limits(host):
    client, _s, _c, _t = host
    big = b'{"initData":"x","session":{"handSummaries":[' + b"0," * 1_100_000 + b"0]}}"
    assert client.post("/api/session", content=big, headers={"content-type": "application/json"}).status_code == 413
    assert client.post("/api/session", json={"initData": signed(), "session": "junk"}).status_code == 400
    assert client.post("/api/session", json=["list"]).status_code == 400


def test_coach_failure_still_sends_the_numbers(tmp_path):
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "index.html").write_text("x", encoding="utf-8")
    sent = []

    async def deliver(bot, chat_id, text):
        sent.append(text)

    async def coach(_summary):
        raise RuntimeError("GPU busy")
    client = TestClient(server.create_app(tmp_path / "dist", tmp_path / "d", [JEFF], bots={"jeff": JEFF},
                                          deliver=deliver, coach=coach))
    assert client.post("/api/session", json={"initData": signed(), "session": SESSION}).status_code == 200
    assert wait(lambda: sent) and "Рук: 3" in sent[0] and "Комментарий тренера" not in sent[0]


def test_analysis_handles_junk_and_empty_sessions():
    s = analysis.summarize({"handSummaries": [{"evLost": "NaN", "netProfit": "x"}, 5, None],
                            "records": [{"mistakeType": "bad_fold", "evLost": float("inf")}]})
    assert s["hands"] == 1 and s["ev_lost"] == 0 and s["mistakes"] == 1
    assert "нечего" in analysis.render(analysis.summarize({}), "Ann")


def test_gto_math_and_street_ev_in_analysis():
    assert analysis.pot_odds(100, 50) == pytest.approx(0.25)
    assert analysis.mdf(100, 50) == pytest.approx(2.0 / 3.0)
    assert analysis.optimal_bluff_alpha(100, 50) == pytest.approx(1.0 / 3.0)
    assert analysis.spr(200, 100) == pytest.approx(2.0)
    f = analysis.geometric_bet_fraction(150, 100, 2)
    assert 100 * ((1 + 2 * f) ** 2) == pytest.approx(100 + 2 * 150)

    assert analysis.classify_board(["As", "7d", "2c"]) == "dry"
    assert analysis.classify_board(["Ks", "9s", "4s"]) == "monotone"
    assert analysis.classify_board(["Jh", "Th", "9c"]) == "wet"

    spot = analysis.evaluate_record_gto(
        {"stage": "river", "action": "call", "pot": 150, "toCall": 50, "stack": 200, "blinds": {"bb": 2}, "equity": 0.10, "board": ["As", "Kd", "7c", "2h", "9s"]}
    )
    assert spot["pot_odds"] == pytest.approx(0.25)
    assert spot["mdf"] == pytest.approx(2.0 / 3.0, abs=1e-4)
    assert spot["ev_call"] < 0.0 and spot["delta_ev_bb"] > 10.0


    s = analysis.summarize(SESSION)
    assert "gto" in s and s["gto"]["avg_ev_loss_bb_per_hand"] > 0


