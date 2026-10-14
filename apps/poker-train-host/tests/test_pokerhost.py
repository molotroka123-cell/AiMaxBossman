"""Poker host: every protection is tested with the attack input, not only the happy path."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pokerhost import initdata, server  # noqa: E402

JEFF, PULT, OTHER = "111:jeff-token-abc", "222:pult-token-def", "333:someone-elses-bot"
USER = {"id": 424242, "first_name": "Ann", "username": "ann"}


def signed(user=USER, token=JEFF, auth_date=None):
    return initdata.sign_for_tests({"auth_date": int(auth_date or time.time()), "query_id": "q1", "user": user}, token)


@pytest.fixture
def client(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>poker</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("TOP SECRET", encoding="utf-8")
    data = tmp_path / "data"
    return TestClient(server.create_app(dist, data, [JEFF, PULT]))


# ------------------------------------------------------------------ initData
def test_initdata_accepts_both_own_bots_and_returns_the_signed_user():
    assert initdata.verify(signed(), [JEFF, PULT])["id"] == USER["id"]
    assert initdata.verify(signed(token=PULT), [JEFF, PULT])["id"] == USER["id"]


@pytest.mark.parametrize("bad", [
    lambda: signed(token=OTHER),                                        # signed by a foreign bot
    lambda: signed().replace("Ann", "Bob"),                             # user field tampered after signing
    lambda: signed(auth_date=time.time() - 3 * 86400),                  # stale (replayed) initData
    lambda: signed().split("&hash=")[0],                                # hash stripped
    lambda: "",                                                         # nothing
    lambda: "user=%7B%22id%22%3A1%7D&hash=" + "0" * 64,                 # forged hash
    lambda: "a" * 5000,                                                 # oversized
])
def test_initdata_refuses_forged_stale_or_foreign(bad):
    assert initdata.verify(bad(), [JEFF, PULT]) is None


def test_display_name_strips_control_characters_and_caps_length():
    name = initdata.display_name({"id": 7, "first_name": "A‮B\x00" + "x" * 80})
    assert "\x00" not in name and "‮" not in name and len(name) <= 32


# ------------------------------------------------------------------ server
def test_game_is_served_and_files_outside_the_build_are_not(client):
    assert client.get("/").text == "<html>poker</html>"
    assert client.get("/assets/app.js").status_code == 200
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt", "/data/leaderboard.sqlite3",
                 "/leaderboard.sqlite3"):
        r = client.get(path)
        assert "TOP SECRET" not in r.text and r.status_code in (400, 404), path


def test_leaderboard_post_needs_a_telegram_signature(client):
    r = client.post("/api/leaderboard", json={"playerId": "x", "name": "Hacker", "gtoScore": 100})
    assert r.status_code == 401
    r = client.post("/api/leaderboard", json={"name": "Hacker", "gtoScore": 100, "initData": signed(token=OTHER)})
    assert r.status_code == 401
    assert client.get("/api/leaderboard").json()["total"] == 0


def test_signed_player_is_stored_under_the_telegram_name_not_the_claimed_one(client):
    r = client.post("/api/leaderboard", json={"playerId": "victim", "name": "Someone Else", "gtoScore": 77,
                                              "hands": 120, "initData": signed()})
    assert r.status_code == 200
    [row] = client.get("/api/leaderboard").json()["players"]
    assert row["name"] == "Ann" and row["gtoScore"] == 77 and row["hands"] == 120
    assert row["playerId"] != "victim" and str(USER["id"]) not in row["playerId"]   # raw TG id is not published


def test_one_update_per_player_per_ten_seconds(client):
    assert client.post("/api/leaderboard", json={"gtoScore": 50, "initData": signed()}).status_code == 200
    assert client.post("/api/leaderboard", json={"gtoScore": 99, "initData": signed()}).status_code == 429
    assert client.get("/api/leaderboard").json()["players"][0]["gtoScore"] == 50


def test_stats_are_clamped_and_junk_is_dropped(client):
    body = json.dumps({"gtoScore": 1e9, "hands": -5, "vpip": "NaN", "roi": True, "pfr": 12.345,
                       "initData": signed()}).replace('"NaN"', "NaN")
    r = client.post("/api/leaderboard", content=body, headers={"content-type": "application/json"})
    assert r.status_code == 200
    row = client.get("/api/leaderboard").json()["players"][0]
    assert row["gtoScore"] == 100 and row["hands"] == 0 and row["pfr"] == 12.3
    assert "vpip" not in row and "roi" not in row


def test_paid_model_route_is_off(client):
    r = client.post("/api/claude", json={"prompt": "x"})
    assert r.status_code == 503 and r.json()["error"] == "disabled_on_host"


def test_oversized_body_and_bad_json_are_refused(client):
    assert client.post("/api/leaderboard", content=b"{" + b" " * 9000 + b"}",
                       headers={"content-type": "application/json"}).status_code == 413
    assert client.post("/api/leaderboard", content=b"{oops", headers={"content-type": "application/json"}
                       ).status_code == 400


def test_security_headers_on_every_response(client):
    for path in ("/", "/api/leaderboard", "/nope"):
        h = client.get(path).headers
        assert "frame-ancestors https://web.telegram.org" in h["content-security-policy"]
        assert "connect-src 'self'" in h["content-security-policy"]
        assert h["x-content-type-options"] == "nosniff"


def test_per_ip_rate_limit(client):
    codes = [client.get("/api/health", headers={"cf-connecting-ip": "9.9.9.9"}).status_code for _ in range(70)]
    assert codes.count(429) >= 1 and codes[0] == 200
    assert client.get("/api/health", headers={"cf-connecting-ip": "8.8.8.8"}).status_code == 200


def test_menu_button_payloads(monkeypatch):
    from pokerhost import __main__ as host
    calls = []
    monkeypatch.setattr(host, "tg", lambda token, method, payload: calls.append((token, method, payload)) or True)
    host.set_buttons({"jeff": JEFF, "pult": PULT, "owner_chat": 99}, "https://abc.trycloudflare.com")
    jeff, pult = calls
    assert jeff[0] == JEFF and "chat_id" not in jeff[2]                 # Jeff: for every user
    assert jeff[2]["menu_button"]["web_app"]["url"] == "https://abc.trycloudflare.com"
    assert pult[0] == PULT and pult[2]["chat_id"] == 99                 # pult: owner chat only
    calls.clear()
    host.set_buttons({"jeff": JEFF, "pult": PULT, "owner_chat": 99}, None)
    assert all(c[2]["menu_button"] == {"type": "default"} for c in calls)
