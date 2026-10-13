"""Model-assist path (``vision.model_assist``): the local vision model may fill ONLY fields the deterministic reader left
UNKNOWN, and only where deterministic evidence positively agrees. Every acceptance rule is pinned together with its
negative control (the bad case that must still be rejected). No real network: a fake Ollama server on 127.0.0.1."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from pokervision.adapters.base import Frame
from pokervision.adapters.ton_poker import TonPokerAdapter
from pokervision.schema import Field, Money, TableState
from pokervision.vision.model_assist import (CONFIDENCE, PROMPT, SOURCE, ModelAssist, OllamaClient, ReplayClient,
                                             is_local_url)

DET = "ton_poker.pixels"


# ----------------------------------------------------------------------------------------------- helpers
def E(red=None, rank=None, suit=None, pip=None, rank_near=None):
    return {"box": [0, 0, 10], "corner_ok": red is not None, "red": red, "red_conf": 1.0, "rank": rank, "suit": suit,
            "rank_near": rank_near, "suit_near": None, "pip_suit": pip, "pip_why": ""}


def F(v, t):
    return Field.ok(v, 0.8, t, DET) if v is not None else Field.unknown(t, DET, "not_in_book")


def state(t, board=(), board_ev=(), count="auto", hero=(None, None), hero_ev=None, fan=False, pot=None, stack=None):
    st = TableState(f"f{t}", t, "test", "ton_poker@test")
    st.board = [F(c, t) for c in board]
    st.hero_cards = [F(c, t) for c in hero] if fan else [Field.unknown(t, DET, "hero_fan_not_found")] * 2
    st.pot = Field.ok(Money(pot, 0.01), 0.8, t, DET) if pot is not None else Field.unknown(t, DET, "pot: x")
    st.hero_stack = Field.ok(Money(stack, 0.01), 0.8, t, DET) if stack is not None else Field.unknown(t, DET, "stack: x")
    st.quality = {"evidence": {"board": list(board_ev), "board_count": len(board) if count == "auto" else count,
                               "fan": fan, "hero": list(hero_ev) if hero_ev is not None else ([E(), E()] if fan else None)}}
    return st


def frame(t):
    return Frame(np.zeros((8, 8, 3), np.uint8), t, "test", f"f{t}")


class Scripted:
    """Client returning the scripted answers in order (None entries = model not run on that frame)."""
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), 0

    def ask(self, fr):
        self.calls += 1
        a = self.answers.pop(0) if self.answers else None
        return (a, "") if a is not None else (None, "busy")


def ans(board=(), hero=(), pot=None, stack=None, opponents=()):
    return {"board": list(board), "hero_cards": list(hero), "pot": pot, "hero_stack": stack,
            "opponents": [{"name": "x", "cards": list(opponents), "award": None}] if opponents else []}


BOARD_EV = [E(False, "4", "c", "c"), E(True, None, "h", "h"), E(False, None, None, "s"), E(False, None, "c", "c")]
BOARD_DET = ["4c", None, None, None]
GOOD = ans(board=["4c", "Ah", "8s", "9c"])


def run(assist, answers_states):
    out = None
    for st in answers_states:
        out = assist.apply(frame(st.t_ms), st)
    return out


# ----------------------------------------------------------------------------------------------- board
def test_board_filled_after_two_agreeing_answers_and_marked_as_model():
    a = ModelAssist(Scripted(GOOD, GOOD))
    st = run(a, [state(1000, BOARD_DET, BOARD_EV), state(2000, BOARD_DET, BOARD_EV)])
    assert [f.value for f in st.board] == ["4c", "Ah", "8s", "9c"]
    assert st.board[0].source == DET
    for f in st.board[1:]:
        assert f.source == SOURCE and f.confidence == CONFIDENCE
    assert "board[1]=Ah" in st.quality["model_assist"]["accepted"]


def test_one_answer_alone_is_never_accepted():
    st = run(ModelAssist(Scripted(GOOD)), [state(1000, BOARD_DET, BOARD_EV)])
    assert [f.known for f in st.board] == [True, False, False, False]
    assert st.quality["model_assist"]["rejected"]["board"] == "needs_two_consecutive_agreeing_answers"


def test_two_answers_that_disagree_are_not_accepted():
    other = ans(board=["4c", "Ah", "8s", "7c"])
    st = run(ModelAssist(Scripted(other, GOOD)), [state(1000, BOARD_DET, BOARD_EV), state(2000, BOARD_DET, BOARD_EV)])
    assert not any(f.known for f in st.board[1:])


def test_answers_under_a_different_deterministic_structure_do_not_chain():
    ev3 = BOARD_EV[:3]
    first = state(1000, BOARD_DET[:3], ev3)                     # flop (3 cards)
    a = ModelAssist(Scripted(ans(board=["4c", "Ah", "8s"]), GOOD))
    st = run(a, [first, state(2000, BOARD_DET, BOARD_EV)])     # turn (4 cards): a new structure, the chain restarts
    assert not any(f.known for f in st.board[1:])


def test_answers_too_far_apart_do_not_chain():
    a = ModelAssist(Scripted(GOOD, GOOD), max_gap_ms=5000)
    st = run(a, [state(1000, BOARD_DET, BOARD_EV), state(9000, BOARD_DET, BOARD_EV)])
    assert not any(f.known for f in st.board[1:])


def test_colour_conflict_rejects_the_whole_board():
    bad = ans(board=["4c", "Ad", "8h", "9c"])                  # slot 2 is black ink, the model says a heart
    ev = [E(False, "4", "c", "c"), E(True, None, None, "d"), E(False), E(False, None, "c", "c")]
    st = run(ModelAssist(Scripted(bad, bad)), [state(1000, BOARD_DET, ev), state(2000, BOARD_DET, ev)])
    assert not any(f.known for f in st.board[1:])
    assert "colour_conflict" in st.quality["model_assist"]["rejected"]["board"]


def test_suit_not_positively_supported_leaves_that_slot_unknown():
    ev = [E(False, "4", "c", "c"), E(True, None, "h", "h"), E(False, None, None, None), E(False, None, "c", "c")]
    st = run(ModelAssist(Scripted(GOOD, GOOD)), [state(1000, BOARD_DET, ev), state(2000, BOARD_DET, ev)])
    assert [f.value if f.known else None for f in st.board] == ["4c", "Ah", None, "9c"]


def test_suit_evidence_that_disagrees_rejects_the_whole_board():
    ev = [E(False, "4", "c", "c"), E(True, None, "h", "h"), E(False, None, None, "c"), E(False, None, "c", "c")]
    st = run(ModelAssist(Scripted(GOOD, GOOD)), [state(1000, BOARD_DET, ev), state(2000, BOARD_DET, ev)])
    assert not any(f.known for f in st.board[1:])
    assert "centre_pip_conflict" in st.quality["model_assist"]["rejected"]["board"]


def test_board_length_mismatch_is_rejected():
    short = ans(board=["4c", "Ah", "8s"])
    st = run(ModelAssist(Scripted(short, short)), [state(1000, BOARD_DET, BOARD_EV), state(2000, BOARD_DET, BOARD_EV)])
    assert not any(f.known for f in st.board[1:])
    assert st.quality["model_assist"]["rejected"]["board"].startswith("board_length_mismatch")


def test_unknown_deterministic_count_means_no_board_from_the_model():
    a = ModelAssist(Scripted(GOOD, GOOD))
    st = run(a, [state(1000, BOARD_DET, BOARD_EV, count=None), state(2000, BOARD_DET, BOARD_EV, count=None)])
    assert not any(f.known for f in st.board[1:])
    assert a.client.calls == 0                                 # nothing it may fill: the model is not even asked


def test_contradiction_with_a_deterministic_card_rejects_the_whole_board():
    bad = ans(board=["4s", "Ah", "8s", "9c"])                  # deterministic slot 0 is 4c
    st = run(ModelAssist(Scripted(bad, bad)), [state(1000, BOARD_DET, BOARD_EV), state(2000, BOARD_DET, BOARD_EV)])
    assert not any(f.known for f in st.board[1:])
    assert "contradicts_deterministic_card" in st.quality["model_assist"]["rejected"]["board"]


def test_cards_must_be_unique_across_hero_board_opponents():
    dup = ans(board=["4c", "Ah", "8s", "9c"], opponents=["Ah", "2d"])
    st = run(ModelAssist(Scripted(dup, dup)), [state(1000, BOARD_DET, BOARD_EV), state(2000, BOARD_DET, BOARD_EV)])
    assert not any(f.known for f in st.board[1:])
    assert st.quality["model_assist"]["rejected"]["cards"].startswith("duplicate_card")


# ----------------------------------------------------------------------------------------------- hero
def test_hero_qc_never_becomes_qs():
    """Measured 12.10: the model read every hero Qc as Qs. Club evidence -> rejected; no suit evidence -> UNKNOWN."""
    qs = ans(hero=["Qs", "7d"])
    ev_club = [E(False, None, "c", rank_near="Q"), E(True, "7", None, rank_near="7")]
    st = run(ModelAssist(Scripted(qs, qs)), [state(1000, fan=True, hero_ev=ev_club), state(2000, fan=True, hero_ev=ev_club)])
    assert [f.known for f in st.hero_cards] == [False, False]
    assert st.quality["model_assist"]["rejected"]["hero"].startswith("hero_contradicts_evidence")
    ev_none = [E(False, None, None, rank_near="Q"), E(True, "7", None, rank_near="7")]
    st = run(ModelAssist(Scripted(qs, qs)), [state(1000, fan=True, hero_ev=ev_none), state(2000, fan=True, hero_ev=ev_none)])
    assert not st.hero_cards[0].known                          # never Qs without positive suit evidence


def test_hero_accepted_when_corner_suit_supports_the_model():
    qc = ans(hero=["7d", "Qc"])                                # model order differs from the fan: fixed by the suits
    ev = [E(False, None, "c", rank_near="9"), E(True, None, "d", rank_near="7")]
    st = run(ModelAssist(Scripted(qc, qc)), [state(1000, fan=True, hero_ev=ev), state(2000, fan=True, hero_ev=ev)])
    assert [f.value for f in st.hero_cards] == ["Qc", "7d"]
    assert all(f.source == SOURCE for f in st.hero_cards)


def test_hero_needs_the_fan():
    a = ModelAssist(Scripted(ans(hero=["Qc", "7d"])) )
    st = run(a, [state(1000, fan=False), state(2000, fan=False)])
    assert not any(f.known for f in st.hero_cards) and a.client.calls == 0


def test_same_suit_hero_order_needs_nearest_rank_agreement():
    pair = ans(hero=["Kc", "9c"])
    ev_bad = [E(False, None, "c", rank_near="5"), E(False, None, "c", rank_near="6")]
    st = run(ModelAssist(Scripted(pair, pair)), [state(1000, fan=True, hero_ev=ev_bad), state(2000, fan=True, hero_ev=ev_bad)])
    assert not any(f.known for f in st.hero_cards)
    assert st.quality["model_assist"]["rejected"]["hero"] == "hero_order_not_fixed_by_evidence"
    ev_ok = [E(False, None, "c", rank_near="9"), E(False, None, "c", rank_near="K")]
    st = run(ModelAssist(Scripted(pair, pair)), [state(1000, fan=True, hero_ev=ev_ok), state(2000, fan=True, hero_ev=ev_ok)])
    assert [f.value for f in st.hero_cards] == ["9c", "Kc"]


# ----------------------------------------------------------------------------------------------- money
def test_stack_never_introduces_a_value():
    """The '0' badge and the '+77.28' award were read as the hero stack: only the last deterministic stack is bridged."""
    a = ModelAssist(Scripted(ans(stack="0"), ans(stack="0")))
    st = run(a, [state(1000, stack=200.0), state(2000), state(3000)])
    assert not st.hero_stack.known
    assert st.quality["model_assist"]["rejected"]["hero_stack"].startswith("stack_differs_from_last_deterministic")
    a = ModelAssist(Scripted(ans(stack="+77.28"), ans(stack="+77.28")))
    st = run(a, [state(1000, stack=200.0), state(2000), state(3000)])
    assert not st.hero_stack.known and st.quality["model_assist"]["rejected"]["hero_stack"] == "stack_not_cash"
    a = ModelAssist(Scripted(ans(stack="200"), ans(stack="200")))
    st = run(a, [state(1000, stack=200.0), state(2000), state(3000)])
    assert st.hero_stack.known and st.hero_stack.value.amount == 200.0 and st.hero_stack.source == SOURCE


def test_stack_without_any_deterministic_reading_is_never_asked():
    a = ModelAssist(Scripted(ans(stack="200"), ans(stack="200")))
    st = run(a, [state(1000), state(2000)])
    assert not st.hero_stack.known and a.client.calls == 0


def test_pot_may_not_decrease_within_the_hand():
    a = ModelAssist(Scripted(ans(pot="50"), ans(pot="50")))
    st = run(a, [state(1000, pot=84.0), state(2000), state(3000)])
    assert not st.pot.known and st.quality["model_assist"]["rejected"]["pot"].startswith("pot_decrease")
    a = ModelAssist(Scripted(ans(pot="126.5"), ans(pot="126.50")))
    st = run(a, [state(1000, pot=84.0), state(2000), state(3000)])
    assert st.pot.known and st.pot.value.amount == 126.5 and st.pot.source == SOURCE


def test_pot_that_is_not_cash_is_rejected():
    a = ModelAssist(Scripted(ans(pot="1,2K"), ans(pot="1,2K")))
    st = run(a, [state(1000, pot=84.0), state(2000), state(3000)])
    assert not st.pot.known and st.quality["model_assist"]["rejected"]["pot"] == "pot_not_cash"


def test_new_hand_drops_the_pot_anchor():
    a = ModelAssist(Scripted(ans(pot="3"), ans(pot="3")))
    river = state(1000, ["4c", "Ah", "8s", "9c", "2d"], [E(False, "4", "c")] * 5, pot=84.0)
    pre = [state(2000, [], [], count=0), state(3000, [], [], count=0)]    # board shrank -> new hand, no pot anchor
    st = run(a, [river] + pre)
    assert not st.pot.known and a.client.calls == 0


# ----------------------------------------------------------------------------------------------- gate / wiring
def test_known_fields_are_never_touched_and_model_not_called():
    a = ModelAssist(Scripted(GOOD, GOOD))
    full = ["4c", "Ah", "8s", "9c"]
    st = run(a, [state(1000, full, BOARD_EV, hero=("Qc", "7d"), fan=True, pot=84.0, stack=200.0)])
    assert a.client.calls == 0 and all(f.source == DET for f in st.board)


def test_closed_gate_never_reaches_the_model():
    client = Scripted(GOOD, GOOD)
    ad = TonPokerAdapter()                                      # no profile: refused before any pixel is read
    ad.model_assist = ModelAssist(client)
    st = ad.read(Frame(np.zeros((100, 60, 3), np.uint8), 1000, "test"))
    assert st.quality.get("refused") and client.calls == 0


def test_refused_state_is_left_alone():
    a = ModelAssist(Scripted(GOOD, GOOD))
    st = state(1000, BOARD_DET, BOARD_EV)
    st.quality["refused"] = "layout_not_verified_on_held_out_frames"
    run(a, [st, st])
    assert a.client.calls == 0


def test_busy_client_skips_the_frame_without_breaking_the_chain():
    a = ModelAssist(Scripted(GOOD, None, GOOD))
    st = run(a, [state(1000, BOARD_DET, BOARD_EV), state(2000, BOARD_DET, BOARD_EV), state(3000, BOARD_DET, BOARD_EV)])
    assert all(f.known for f in st.board)


def test_replay_client_treats_missing_answers_as_not_run():
    rc = ReplayClient(lambda fr: None)
    assert rc.ask(frame(1)) == (None, "busy")


# ----------------------------------------------------------------------------------------------- the local client
class _Fake(BaseHTTPRequestHandler):
    reply = {"message": {"content": json.dumps(GOOD)}}
    seen: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Fake.seen.append(body)
        out = _Fake.reply
        if body.get("think"):                                  # the gotcha: thinking + format=json -> empty answer
            out = {"message": {"content": ""}}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_ollama():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    _Fake.seen = []
    _Fake.reply = {"message": {"content": json.dumps(GOOD)}}
    yield f"http://127.0.0.1:{srv.server_address[1]}/api/chat"
    srv.shutdown()
    srv.server_close()


@pytest.mark.parametrize("url", ["http://10.0.0.5:11434/api/chat", "http://example.com/api/chat",
                                 "https://127.0.0.1:11434/api/chat", "http://127.0.0.1.evil.com/api/chat",
                                 "http://user@ollama.example/api/chat"])
def test_non_local_host_is_refused(url):
    assert not is_local_url(url)
    with pytest.raises(ValueError):
        OllamaClient(url)


def test_local_hosts_are_allowed():
    for url in ("http://127.0.0.1:11434/api/chat", "http://localhost:11434/api/chat", "http://[::1]:11434/api/chat"):
        assert is_local_url(url)


def test_client_request_shape_and_answer(fake_ollama):
    cli = OllamaClient(fake_ollama, timeout_s=5)
    data, why = cli.ask(frame(1))
    assert why == "" and data["board"] == GOOD["board"]
    body = _Fake.seen[-1]
    assert body["think"] is False and body["format"] == "json" and body["stream"] is False
    assert body["options"]["temperature"] == 0
    assert body["messages"][0]["content"] == PROMPT and len(body["messages"][0]["images"]) == 1


def test_empty_answer_with_thinking_is_no_answer(fake_ollama):
    cli = OllamaClient(fake_ollama, timeout_s=5)
    cli.body_orig = cli.body
    cli.body = lambda png: dict(cli.body_orig(png), think=True)    # simulate a caller that turned thinking on
    data, why = cli.ask(frame(1))
    assert data is None and why.startswith("empty_answer")
    a = ModelAssist(cli)
    st = run(a, [state(1000, BOARD_DET, BOARD_EV), state(2000, BOARD_DET, BOARD_EV)])
    assert not any(f.known for f in st.board[1:]) and st.quality["model_assist"]["why"].startswith("empty_answer")


def test_end_to_end_through_the_fake_server(fake_ollama):
    a = ModelAssist(OllamaClient(fake_ollama, timeout_s=5))
    st = run(a, [state(1000, BOARD_DET, BOARD_EV), state(2000, BOARD_DET, BOARD_EV)])
    assert [f.value for f in st.board] == ["4c", "Ah", "8s", "9c"]


def test_unreachable_server_is_no_answer():
    cli = OllamaClient("http://127.0.0.1:9/api/chat", timeout_s=2)
    data, why = cli.ask(frame(1))
    assert data is None and why.startswith("request_failed")


def test_only_one_request_in_flight():
    cli = OllamaClient("http://127.0.0.1:9/api/chat", timeout_s=2)
    cli._lock.acquire()
    try:
        assert cli.ask(frame(1)) == (None, "busy")
    finally:
        cli._lock.release()


# ----------------------------------------------------------------------------------------------- learning material
def test_candidates_go_outside_git_only(tmp_path):
    p = tmp_path / "cand" / "candidates.jsonl"
    a = ModelAssist(Scripted(GOOD, GOOD), candidates_path=p)
    run(a, [state(1000, BOARD_DET, BOARD_EV), state(2000, BOARD_DET, BOARD_EV)])
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]
    assert {r["model_card"] for r in rows} == {"Ah", "8s", "9c"} and all(r["status"] == "unreviewed" for r in rows)
    from pathlib import Path
    inside = Path(__file__).resolve().parent / "candidates.jsonl"
    with pytest.raises(ValueError):
        ModelAssist(Scripted(), candidates_path=inside)


# ----------------------------------------------------------------------------------------------- opt-in only
def test_model_assist_is_off_by_default(monkeypatch):
    monkeypatch.delenv("BOSSMAN_POKER_MODEL_ASSIST", raising=False)
    assert TonPokerAdapter().model_assist is None
    monkeypatch.setenv("BOSSMAN_POKER_MODEL_ASSIST", "yes")          # only the exact "1" opts in
    assert TonPokerAdapter().model_assist is None


def test_opt_in_flag_enables_a_local_client_only(monkeypatch):
    monkeypatch.setenv("BOSSMAN_POKER_MODEL_ASSIST", "1")
    ad = TonPokerAdapter()
    assert isinstance(ad.model_assist, ModelAssist) and is_local_url(ad.model_assist.client.url)
    monkeypatch.setenv("BOSSMAN_POKER_MODEL_ASSIST_URL", "http://10.1.2.3:11434/api/chat")
    with pytest.raises(ValueError):
        TonPokerAdapter()
