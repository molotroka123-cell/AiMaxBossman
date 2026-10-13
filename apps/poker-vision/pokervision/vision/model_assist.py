"""Second source for the TON-style reader: the LOCAL vision model fills only what the deterministic reader left UNKNOWN.

Why: the deterministic card book is per frame width and only knows classes it has seen (the 1320 px recording has no
2 3 7 T J Q K and no diamonds), so many board cards stay UNKNOWN although rank and colour are obvious to a vision model.
The model alone is not trusted: measured 12.10.2026 on the owner's two recordings it read every board and pot right but
read every hero Qc as Qs (14 of 72 hero answers) and the "0" badge / "+77.28" award as the hero stack.

Contract (each rule has a test in ``tests/test_model_assist.py``):

* LOCAL ONLY. The endpoint host must be 127.0.0.1 / localhost / ::1 (anything else raises at construction), system
  proxies are bypassed, one request at a time (a busy client skips the frame), ``think: false`` (with thinking ON and
  ``format: json`` the answer comes back EMPTY), ``format: json``, temperature 0, bounded timeout.
* Called only behind the base profile gate (from ``TonPokerAdapter._read_rois``, table visible) and only when a field
  it may fill is UNKNOWN. ``verified`` is never touched.
* board: only when the deterministic board COUNT is known and the model's board has exactly that length. A model card is
  taken for an UNKNOWN slot only when its suit is POSITIVELY supported by deterministic evidence (centre-pip shape or
  strict book suit) and equal to it; red/black from the corner ink must always agree; any contradiction with a
  deterministic-known card, colour, strict rank or suit rejects the WHOLE model board.
* hero: only when the fan was found; the rank comes from the model, the suit must be supported by the deterministic
  corner (strict book suit) and equal the model's (the centre pip is not used on the fan: measured wrong once). Club evidence or no evidence against a
  model "Qs" -> UNKNOWN / rejected, never Qs. The left/right order must be fixed by the evidence (suits or nearest rank).
* pot: only when the deterministic pot is UNKNOWN, the answer parses as cash, and it is >= the last deterministic pot
  of the same hand. stack: never a new value: accepted only when equal to the last deterministic stack.
* every accepted value needs TWO consecutive model answers that agree, taken with the same deterministic structure
  (board count, fan presence); cards are unique across hero / board / opponents.
* accepted values carry ``source="model_assist"`` and confidence 0.45 so the Reconciler / coach / history can tell them
  apart. Optional "learning": accepted card crops can be appended to a candidates JSONL OUTSIDE any git checkout for a
  human to review; nothing is ever written into the live card book.
"""
from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from ..parse import parse_cash
from ..schema import Field, Money, TableState

SOURCE = "model_assist"
CONFIDENCE = 0.45
DEFAULT_URL = "http://127.0.0.1:11434/api/chat"
DEFAULT_MODEL = "bossman-fast-qwen36-vision:latest"
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# Same prompt as the 12.10.2026 local-teacher measurement (creative-runs/POKER-LOCAL-TEACHER-20261012/teacher_eval.py),
# copied so the measured behaviour is the behaviour shipped.
PROMPT = ('You read ONE screenshot of a mobile Texas Hold\'em table. Answer with ONE JSON object and nothing else.\n'
          'The HERO is the player seated at the bottom-left of the table. Keys:\n'
          'hero_cards: the hero\'s own two face-up cards, e.g. ["7d","7h"], or [] if the hero has no face-up cards.\n'
          'board: community cards in the centre, left to right (all boards if several are shown), or [].\n'
          'pot: the number next to \'Total Pot\' / the pot label, string or null.\n'
          'hero_stack: the number under the hero\'s name, string or null.\n'
          'buttons: labels of the action buttons the hero can press at the bottom, or [].\n'
          'hero_turn: true only if fold/call/check/bet/raise buttons for the hero are shown, else false.\n'
          'opponents: list for every OTHER player that has FACE-UP cards or a \'+amount\' award label: '
          '{"name": seat name, "cards": [face-up cards], "award": amount string or null}.\n'
          'Card format: rank 2-9,T,J,Q,K,A then suit s,h,d,c (spades=s, clubs=c: look at the shape). '
          'If you are not sure use null / [] - never guess. Face-down cards are never listed.')


def is_local_url(url: str) -> bool:
    try:
        p = urllib.parse.urlparse(url)
    except ValueError:
        return False
    return p.scheme == "http" and (p.hostname or "").lower() in LOCAL_HOSTS


# ----------------------------------------------------------------------------------------------- answer parsing
def norm_cards(value) -> list[str]:
    out = []
    for c in value if isinstance(value, list) else []:
        c = str(c).strip().replace("10", "T")
        if re.fullmatch(r"[2-9TJQKA][shdc]", c, re.I):
            out.append(c[0].upper() + c[1].lower())
        else:
            return []                       # one malformed card makes the whole list unusable (order would shift)
    return out


def norm_cash(value) -> Money | None:
    """'84' / '35.18' / '35.1' (a dropped trailing zero) -> Money; anything else (signs, letters, 3 decimals) -> None."""
    if value is None:
        return None
    s = str(value).strip().replace(",", "")
    m = re.fullmatch(r"(\d+)(?:\.(\d{1,2}))?", s)
    if not m:
        return None
    return parse_cash(m.group(1) + ("." + m.group(2).ljust(2, "0") if m.group(2) else ""))


@dataclass
class Answer:
    board: list[str] = field(default_factory=list)
    hero: list[str] = field(default_factory=list)
    pot: Money | None = None
    stack: Money | None = None
    opponents: list[str] = field(default_factory=list)

    @staticmethod
    def parse(data: dict) -> "Answer":
        opp = []
        for o in data.get("opponents") or []:
            if isinstance(o, dict):
                opp += norm_cards(o.get("cards"))
        return Answer(norm_cards(data.get("board")), norm_cards(data.get("hero_cards")), norm_cash(data.get("pot")),
                      norm_cash(data.get("hero_stack")), opp)


# ----------------------------------------------------------------------------------------------- clients
class OllamaClient:
    """One local Ollama vision call per frame. Never more than one request in flight: a busy client returns
    ``(None, "busy")`` and the frame is simply not assisted."""

    def __init__(self, url: str = DEFAULT_URL, model: str = DEFAULT_MODEL, timeout_s: float = 60.0, num_predict: int = 400):
        if not is_local_url(url):
            raise ValueError(f"model_assist: refusing non-local endpoint {url!r} (only 127.0.0.1/localhost)")
        self.url, self.model, self.timeout_s, self.num_predict = url, model, float(timeout_s), int(num_predict)
        self._lock = threading.Lock()
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never via a system proxy

    def body(self, png: bytes) -> dict:
        return {"model": self.model, "stream": False, "format": "json", "think": False,
                "options": {"temperature": 0, "num_predict": self.num_predict},
                "messages": [{"role": "user", "content": PROMPT, "images": [base64.b64encode(png).decode("ascii")]}]}

    def ask(self, frame) -> tuple[dict | None, str]:
        if not self._lock.acquire(blocking=False):
            return None, "busy"
        try:
            ok, png = cv2.imencode(".png", frame.bgr)
            if not ok:
                return None, "encode_failed"
            req = urllib.request.Request(self.url, data=json.dumps(self.body(png.tobytes())).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
            try:
                with self._opener.open(req, timeout=self.timeout_s) as r:
                    raw = json.loads(r.read())
            except Exception as e:                       # timeout, refused, HTTP error: no answer, never a guess
                return None, f"request_failed({type(e).__name__})"
            text = ((raw or {}).get("message") or {}).get("content", "") or ""
            if not text.strip():
                return None, "empty_answer(thinking_on?)"
            try:
                data = json.loads(text)
            except ValueError:
                return None, "answer_not_json"
            return (data, "") if isinstance(data, dict) else (None, "answer_not_object")
        finally:
            self._lock.release()


class ReplayClient:
    """Replays model answers recorded earlier (evaluation / simulation without new model calls). ``lookup(frame)`` returns
    the raw answer dict, or None when the model was not run on that frame (treated like a busy client)."""

    def __init__(self, lookup: Callable[[object], dict | None]):
        self.lookup = lookup

    def ask(self, frame) -> tuple[dict | None, str]:
        d = self.lookup(frame)
        return (d, "") if d is not None else (None, "busy")


# ----------------------------------------------------------------------------------------------- the assist
def _is_red(card: str) -> bool:
    return card[1] in "hd"


def _contradicts(det: Field | None, e: dict, m: str, pip_ok: bool = True) -> str:
    """Why model card ``m`` contradicts what the deterministic reader saw on that card ('' = no contradiction)."""
    if det is not None and det.known:
        return "" if det.value == m else f"contradicts_deterministic_card({det.value}!={m})"
    if e.get("red") is not None and e["red"] != _is_red(m):
        return f"colour_conflict({m})"
    if e.get("rank") and e["rank"] != m[0]:
        return f"rank_conflict({e['rank']}!={m[0]})"
    if e.get("suit") and e["suit"] != m[1]:
        return f"suit_conflict({e['suit']}!={m[1]})"
    if pip_ok and e.get("pip_suit") and e["pip_suit"] != m[1]:
        return f"centre_pip_conflict({e['pip_suit']}!={m[1]})"
    return ""


def _supported(e: dict, m: str, pip_ok: bool = True) -> bool:
    """Suit POSITIVELY confirmed by deterministic evidence, colour known and equal."""
    if e.get("red") is None or e["red"] != _is_red(m):
        return False
    return e.get("suit") == m[1] or (pip_ok and e.get("pip_suit") == m[1])


@dataclass
class _Prev:
    t_ms: int
    structure: tuple
    ans: Answer


class ModelAssist:
    def __init__(self, client, max_gap_ms: int = 20000, candidates_path: str | Path | None = None):
        self.client = client
        self.max_gap_ms = int(max_gap_ms)
        self.candidates_path = Path(candidates_path) if candidates_path else None
        if self.candidates_path is not None:
            _refuse_inside_git(self.candidates_path)
        self.reset()

    def reset(self) -> None:
        self._prev: _Prev | None = None
        self._det_pot: Money | None = None
        self._det_stack: Money | None = None
        self._det_count: int | None = None
        self._det_hero: tuple | None = None

    # ---- deterministic anchors (pot within the hand, last stack)
    def _update_anchors(self, st: TableState, ev: dict) -> None:
        n = ev.get("board_count")
        hero = tuple(f.value for f in st.hero_cards) if len(st.hero_cards) == 2 and all(f.known for f in st.hero_cards) else None
        new_hand = (n is not None and self._det_count is not None and n < self._det_count) or \
                   (hero is not None and self._det_hero is not None and set(hero) != set(self._det_hero))
        if new_hand:
            self._det_pot = None
        if n is not None:
            self._det_count = n
        if hero is not None:
            self._det_hero = hero
        if st.pot.known and st.pot.source != SOURCE:
            self._det_pot = st.pot.value
        if st.hero_stack.known and st.hero_stack.source != SOURCE:
            self._det_stack = st.hero_stack.value

    def apply(self, frame, st: TableState) -> TableState:
        ev = st.quality.get("evidence")
        if ev is None or st.quality.get("refused"):
            return st
        self._update_anchors(st, ev)
        n = ev.get("board_count")
        need_board = n is not None and len(st.board) == n and any(not f.known for f in st.board)
        need_hero = bool(ev.get("fan")) and ev.get("hero") is not None and any(not f.known for f in st.hero_cards)
        need_pot = not st.pot.known and self._det_pot is not None
        need_stack = not st.hero_stack.known and self._det_stack is not None
        info: dict = {"called": False, "accepted": [], "rejected": {}}
        st.quality["model_assist"] = info
        if not (need_board or need_hero or need_pot or need_stack):
            return st
        data, why = self.client.ask(frame)
        info["called"] = why != "busy"
        if data is None:
            info["why"] = why
            if why != "busy":
                self._prev = None                       # a failed / empty answer breaks the two-answer chain
            return st
        ans = Answer.parse(data)
        structure = (n, bool(ev.get("fan")))
        prev = self._prev
        chain = prev is not None and prev.structure == structure and 0 < st.t_ms - prev.t_ms <= self.max_gap_ms
        self._prev = _Prev(st.t_ms, structure, ans)
        t = st.t_ms
        ok = lambda v: Field.ok(v, CONFIDENCE, t, SOURCE)
        filled_cards: list[tuple[str, int, str, dict]] = []
        # ---- board
        if need_board:
            fill, r = self._board(st, ev, ans)
            if r:
                info["rejected"]["board"] = r
            elif not chain or prev.ans.board != ans.board:
                info["rejected"]["board"] = "needs_two_consecutive_agreeing_answers"
            elif not fill:
                info["rejected"]["board"] = "no_slot_with_supported_suit"
            else:
                for i, m in fill.items():
                    st.board[i] = ok(m)
                    filled_cards.append(("board", i, m, ev["board"][i]))
        # ---- hero
        if need_hero:
            fill, r = self._hero(st, ev, ans)
            if r:
                info["rejected"]["hero"] = r
            elif not chain or sorted(prev.ans.hero) != sorted(ans.hero):
                info["rejected"]["hero"] = "needs_two_consecutive_agreeing_answers"
            elif not fill:
                info["rejected"]["hero"] = "suit_not_supported_by_deterministic_evidence"
            else:
                for i, m in fill.items():
                    st.hero_cards[i] = ok(m)
                    filled_cards.append(("hero", i, m, ev["hero"][i]))
        # ---- uniqueness across hero / board / opponents (deterministic showdown + the model's own opponents)
        if filled_cards:
            seen = [f.value for f in st.hero_cards + st.board if f.known]
            for sd in st.showdown or []:
                seen += [c for c in sd.get("cards") or [] if c]
            seen += ans.opponents
            if len(seen) != len(set(seen)) or len(ans.board + ans.hero) != len(set(ans.board + ans.hero)):
                for kind, i, _, _ in filled_cards:
                    tgt = st.board if kind == "board" else st.hero_cards
                    tgt[i] = Field.unknown(t, SOURCE, "model_card_duplicates_another_card")
                info["rejected"]["cards"] = "duplicate_card_across_hero_board_opponents"
                filled_cards = []
        for kind, i, m, _ in filled_cards:
            info["accepted"].append(f"{kind}[{i}]={m}")
        # ---- pot
        if need_pot:
            m = ans.pot
            if m is None:
                info["rejected"]["pot"] = "pot_not_cash"
            elif m.amount < self._det_pot.amount - 0.005:
                info["rejected"]["pot"] = f"pot_decrease({m.amount:g}<{self._det_pot.amount:g})"
            elif not chain or prev.ans.pot is None or abs(prev.ans.pot.amount - m.amount) > 0.005:
                info["rejected"]["pot"] = "needs_two_consecutive_agreeing_answers"
            else:
                st.pot = ok(m)
                info["accepted"].append(f"pot={m.amount:g}")
        # ---- stack: bridges a gap, never introduces a value
        if need_stack:
            m = ans.stack
            if m is None:
                info["rejected"]["hero_stack"] = "stack_not_cash"
            elif abs(m.amount - self._det_stack.amount) > 0.005:
                info["rejected"]["hero_stack"] = f"stack_differs_from_last_deterministic({m.amount:g}!={self._det_stack.amount:g})"
            elif not chain or prev.ans.stack is None or abs(prev.ans.stack.amount - m.amount) > 0.005:
                info["rejected"]["hero_stack"] = "needs_two_consecutive_agreeing_answers"
            else:
                st.hero_stack = ok(self._det_stack)
                info["accepted"].append(f"hero_stack={m.amount:g}")
        if filled_cards and self.candidates_path is not None:
            self._write_candidates(frame, st, filled_cards)
        return st

    # ---- rules
    @staticmethod
    def _board(st: TableState, ev: dict, ans: Answer) -> tuple[dict[int, str], str]:
        n = ev["board_count"]
        if len(ans.board) != n:
            return {}, f"board_length_mismatch(model {len(ans.board)} != deterministic {n})"
        fill = {}
        for i, (det, e, m) in enumerate(zip(st.board, ev["board"], ans.board)):
            why = _contradicts(det, e, m)
            if why:
                return {}, f"slot{i}:{why}"
            if not det.known and _supported(e, m):
                fill[i] = m
        return fill, ""

    @staticmethod
    def _hero(st: TableState, ev: dict, ans: Answer) -> tuple[dict[int, str], str]:
        if len(ans.hero) != 2 or ans.hero[0] == ans.hero[1]:
            return {}, f"hero_answer_not_two_cards({ans.hero})"
        evs = ev["hero"]
        pip_ok = (False, False)                     # fan: suit only from the strict corner book (centre pip not used)

        def consistent(order):
            return all(not _contradicts(st.hero_cards[i], evs[i], order[i], pip_ok[i]) for i in range(2))

        orders = [o for o in (tuple(ans.hero), tuple(reversed(ans.hero))) if consistent(o)]
        if not orders:
            reasons = [_contradicts(st.hero_cards[i], evs[i], ans.hero[i], pip_ok[i]) for i in range(2)]
            return {}, "hero_contradicts_evidence(" + ";".join(r for r in reasons if r) + ")"
        if len(orders) == 2:
            # same-suit / unsupported pair: the order must come from the nearest rank of each corner
            near = [o for o in orders if all(evs[i].get("rank_near") == o[i][0] for i in range(2))]
            if len(near) != 1:
                return {}, "hero_order_not_fixed_by_evidence"
            orders = near
        order = orders[0]
        fill = {i: order[i] for i in range(2) if not st.hero_cards[i].known and _supported(evs[i], order[i], pip_ok[i])}
        return fill, ""

    # ---- optional learning material (human review only)
    def _write_candidates(self, frame, st: TableState, filled) -> None:
        try:
            p = self.candidates_path
            crops = p.parent / (p.stem + "_crops")
            crops.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as fh:
                for kind, i, m, e in filled:
                    x, y, w = e["box"]
                    h = int(w / 0.70)
                    crop = frame.bgr[max(0, y):y + h, max(0, x):x + w]
                    name = f"{frame.frame_id or st.t_ms}_{kind}{i}_{m}.png".replace("/", "_").replace("\\", "_")
                    if crop.size:
                        cv2.imwrite(str(crops / name), crop)
                    fh.write(json.dumps({"t_ms": st.t_ms, "frame_id": frame.frame_id, "field": kind, "slot": i,
                                         "model_card": m, "evidence": {k: e.get(k) for k in ("red", "rank", "suit",
                                         "rank_near", "suit_near", "pip_suit")}, "crop": name, "status": "unreviewed",
                                         "written": time.strftime("%Y-%m-%dT%H:%M:%S")}, ensure_ascii=False) + "\n")
        except OSError:
            pass                                     # learning material is optional; reading never fails because of it


ENV_FLAG = "BOSSMAN_POKER_MODEL_ASSIST"


def assist_from_env() -> "ModelAssist | None":
    """OFF unless the owner opts in with ``BOSSMAN_POKER_MODEL_ASSIST=1``. Optional ``BOSSMAN_POKER_MODEL_ASSIST_URL`` /
    ``..._MODEL`` (the URL must still be local) and ``..._CANDIDATES`` (a JSONL path outside git)."""
    if os.environ.get(ENV_FLAG, "").strip() != "1":
        return None
    cli = OllamaClient(os.environ.get(ENV_FLAG + "_URL", DEFAULT_URL), os.environ.get(ENV_FLAG + "_MODEL", DEFAULT_MODEL))
    return ModelAssist(cli, candidates_path=os.environ.get(ENV_FLAG + "_CANDIDATES") or None)


def _refuse_inside_git(path: Path) -> None:
    """Candidates may contain personal frames: they must live outside any git checkout."""
    p = path.resolve()
    for d in [p.parent, *p.parent.parents]:
        if (d / ".git").exists():
            raise ValueError(f"model_assist: candidates file {path} is inside a git checkout ({d}); keep it outside git")
