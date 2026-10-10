"""Temporal reconciliation: noisy per-frame readings -> committed values, hand boundaries, stale/frozen detection.

Rules (all explicit, none learned):
  * a value is committed only after ``min_votes`` agreeing readings among the last ``window`` usable frames;
  * the board only grows inside a hand; a smaller/less-known board reading is treated as noise, not as an event;
  * a hand ends on strong evidence (new hero cards, board shrink) — on weak evidence the old hand is closed as
    INCOMPLETE and a new unlinked hand starts; hands are never merged or spliced;
  * a stale or frozen frame sets ``blocked`` so no automatic action may use it.
"""
from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .schema import Field, Money, TableState, money_value
from .validate import check_transition, validate_state


@dataclass
class Config:
    window: int = 6
    min_votes: int = 2
    min_conf: float = 0.35
    stale_ms: int = 1500          # wall-clock age of the newest frame above which it is stale
    freeze_ms: int = 8000         # byte-identical FULL frames for this long => source frozen (only used for sources without a liveness probe)
    max_gap_ms: int = 4000        # a time hole larger than this is recorded as a gap in the history


@dataclass
class Committed:
    """Latest committed view. ``None`` = not committed (UNKNOWN)."""
    hand_id: int | None = None
    t_ms: int = 0
    hero_cards: list = field(default_factory=lambda: [None, None])
    board: list = field(default_factory=list)
    pot: Money | None = None
    to_call: Money | None = None
    hero_stack: Money | None = None
    street: str | None = None
    dealer_slot: int | None = None
    hero_position: str | None = None              # header badge (UTG..BB); used by the preflop chart only
    seats: dict = field(default_factory=dict)     # slot -> {"stack": Money|None, "bet": Money|None}
    actions: list | None = None
    hero_turn: bool | None = None
    blocked: list = field(default_factory=list)   # reasons automatic action is forbidden
    pending: list = field(default_factory=list)   # fields that have a competing reading not yet committed

    def to_dict(self) -> dict:
        def m(x): return None if x is None else {"amount": x.amount, "step": x.step, "raw": x.raw}
        return {"hand_id": self.hand_id, "t_ms": self.t_ms, "hero_cards": self.hero_cards, "board": self.board,
                "pot": m(self.pot), "to_call": m(self.to_call), "hero_stack": m(self.hero_stack), "street": self.street,
                "dealer_slot": self.dealer_slot, "hero_position": self.hero_position,
                "seats": {k: {"stack": m(v.get("stack")), "bet": m(v.get("bet"))} for k, v in self.seats.items()},
                "actions": self.actions, "hero_turn": self.hero_turn, "blocked": self.blocked, "pending": self.pending}


def _same(a, b) -> bool:
    if isinstance(a, Money) and isinstance(b, Money):
        return a.agrees(b)
    return a == b


class _Vote:
    """Commit rule (recency, not majority, so a real change is adopted quickly): the last ``min_votes`` usable readings
    must agree. If the newest usable readings disagree with each other, the committed value is kept and flagged pending."""
    def __init__(self, cfg: Config, expire: bool = False):
        self.cfg = cfg
        self.buf: deque = deque(maxlen=cfg.window)
        self.value: Any = None
        self.pending = False
        self.expire = expire        # a whole window of UNKNOWN readings drops the committed value instead of keeping a stale one

    def push(self, f: Field | None) -> None:
        if f is None:
            return
        if f.known and f.confidence >= self.cfg.min_conf:
            self.buf.append(f.value)
        else:
            self.buf.append(None)          # unknown reading: a frame that votes for nothing
        usable = [v for v in self.buf if v is not None]
        if self.expire and not usable and len(self.buf) == self.buf.maxlen:
            self.value = None; self.pending = False
            return
        k = self.cfg.min_votes
        if len(usable) < k:
            self.pending = False
            return
        tail = usable[-k:]
        if all(_same(tail[0], v) for v in tail[1:]):
            self.value = tail[-1]
            self.pending = False
        else:
            self.pending = self.value is not None

    def reset(self) -> None:
        self.buf.clear(); self.value = None; self.pending = False


class Reconciler:
    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or Config()
        self.reset_all()

    def reset_all(self) -> None:
        self.hand_id = 0
        self._new_hand_votes()
        self.prev_state: TableState | None = None
        self.last_t: int | None = None
        self.frozen_since: int | None = None
        self.last_hash: str | None = None
        self.hands: list[dict] = []
        self.frame_hand: dict[str, int | None] = {}      # frame_id -> hand id; None = ambiguous (never guessed)
        self._cur_frames: list[tuple[str, tuple | None]] = []   # (frame_id, hero cards read on that frame)
        self.cur = self._fresh_hand(None)
        self.committed = Committed()

    def _new_hand_votes(self) -> None:
        c = self.cfg
        self.v = {k: _Vote(c) for k in ("pot", "to_call", "hero_stack", "street", "dealer", "hero_turn", "actions", "board_count", "hero_position")}
        self.v["actions"] = _Vote(c, expire=True)       # buttons that cannot be read any more must not stay "committed" (seen live: a stale
                                                        # CALL from an earlier frame kept the policy deciding on buttons no longer on screen)
        self.v_hero = [_Vote(c), _Vote(c)]
        self.v_board = [_Vote(c) for _ in range(5)]
        self.v_seat: dict[int, dict[str, _Vote]] = {}
        self.max_board = 0
        self.max_pot: float | None = None

    def _fresh_hand(self, t) -> dict:
        self.hand_id += 1
        return {"id": self.hand_id, "t_start": t, "t_end": t, "status": "open", "linked": True, "frames": 0, "snapshots": [],
                "gaps": [], "issues": [], "end_reason": None, "boundary_evidence": []}

    # --------------------------------------------------------------- hand boundary
    def _boundary(self, st: TableState) -> tuple[str, str] | None:
        """Return ('strong'|'weak', reason) if this frame (after voting) indicates a different hand than the open one."""
        cm = self.committed
        # strong 1: both hero cards committed differ from the open hand's cards
        new_hero = [self._peek_known(self.v_hero[i], st.hero_cards[i] if i < len(st.hero_cards) else None) for i in range(2)]
        if all(cm.hero_cards) and all(new_hero) and new_hero != cm.hero_cards:
            return "strong", f"hero cards {cm.hero_cards}->{new_hero}"
        # strong 2: board shrank while readable
        n = st.board_count.value if st.board_count.known and st.board_count.confidence >= self.cfg.min_conf else None
        if n is not None and cm.board and n < len([c for c in cm.board if c is not None]) and n in (0, 3, 4) and self._board_shrink_votes(n) >= self.cfg.min_votes:
            return "strong", f"board {len(cm.board)}->{n}"
        # weak: pot collapsed
        pp, cp = cm.pot.amount if cm.pot else None, money_value(st.pot)
        if pp and cp is not None and cp < 0.5 * pp and self._pot_collapse_votes(pp) >= self.cfg.min_votes:
            return "weak", f"pot {pp}->{cp}"
        return None

    def _peek_known(self, vote: _Vote, f: Field | None):
        """Value this slot would commit if f were pushed now (same recency rule as _Vote, without mutating)."""
        vals = [v for v in vote.buf if v is not None]
        if f is not None and f.known and f.confidence >= self.cfg.min_conf:
            vals = vals + [f.value]
        k = self.cfg.min_votes
        if len(vals) >= k and all(_same(vals[-k], v) for v in vals[-k:]):
            return vals[-1]
        return None

    def _board_shrink_votes(self, n) -> int:
        return getattr(self, "_bs", {}).get(n, 0)

    def _pot_collapse_votes(self, pp) -> int:
        return getattr(self, "_pc", 0)

    # --------------------------------------------------------------- main step
    def push(self, st: TableState, now_ms: int | None = None, frame_bytes: bytes | None = None) -> Committed:
        cfg = self.cfg
        t = st.t_ms
        blocked: list[str] = []
        # --- stale / frozen
        if now_ms is not None and now_ms - t > cfg.stale_ms:
            blocked.append(f"STALE_FRAME age={now_ms - t}ms")
        if frame_bytes is not None:
            h = hashlib.blake2b(frame_bytes, digest_size=8).hexdigest()
            if h == self.last_hash:
                self.frozen_since = self.frozen_since if self.frozen_since is not None else t
                if t - self.frozen_since >= cfg.freeze_ms:
                    blocked.append(f"FROZEN_SOURCE identical for {t - self.frozen_since}ms")
            else:
                self.frozen_since = None
            self.last_hash = h
        # --- time gap
        if self.last_t is not None and t - self.last_t > cfg.max_gap_ms:
            self.cur["gaps"].append({"from": self.last_t, "to": t, "reason": "frame_gap"})
        self.last_t = t
        validate_state(st)
        # --- vote counters for boundary evidence
        self._update_boundary_counters(st)
        hero_read = tuple(f.value for f in st.hero_cards) if len(st.hero_cards) == 2 and all(f.known and f.confidence >= cfg.min_conf for f in st.hero_cards) else None
        b = self._boundary(st) if self.cur["frames"] > 0 else None
        if b is not None:
            old_hero = tuple(self.committed.hero_cards) if all(self.committed.hero_cards) else None
            new_hero = hero_read if hero_read and hero_read != old_hero else None
            self._close_hand(*b, t)
            # frames after the last frame that still showed the old hero cards and before the first frame that shows the new
            # ones cannot be assigned to either hand: they stay ambiguous (None) instead of being guessed
            idx_old = max((k for k, (_, h) in enumerate(self._cur_frames) if old_hero and h == old_hero), default=-1)
            moved = self._cur_frames[idx_old + 1:]
            first_new = next((k for k, (_, h) in enumerate(moved) if new_hero and h == new_hero), None)
            self._new_hand_votes(); self.committed = Committed(); self.prev_state = None
            self.cur = self._fresh_hand(t)
            self._cur_frames = []
            for k, (fid, h) in enumerate(moved):
                if first_new is not None and k >= first_new:
                    self.frame_hand[fid] = self.cur["id"]; self._cur_frames.append((fid, h))
                else:
                    self.frame_hand[fid] = None
            if b[0] == "weak":
                self.cur["linked"] = False
        self.frame_hand[st.frame_id] = self.cur["id"]
        self._cur_frames.append((st.frame_id, hero_read))
        self.cur["frames"] += 1
        self.cur["t_end"] = t
        # --- transition validation against the previous *usable* state
        tr = check_transition(self.prev_state, st)
        for i in tr:
            self.cur["issues"].append({**i, "t": t})
            for fname in i["fields"]:
                if i["severity"] == "error":
                    blocked.append(f"TRANSITION {i['code']}")
        # --- vote
        for i in range(2):
            self.v_hero[i].push(st.hero_cards[i] if i < len(st.hero_cards) else None)
        for i in range(5):
            self.v_board[i].push(st.board[i] if i < len(st.board) else None)
        self.v["board_count"].push(st.board_count)
        for k, f in (("pot", st.pot), ("to_call", st.to_call), ("hero_stack", st.hero_stack), ("street", st.street),
                     ("dealer", st.dealer_slot), ("hero_turn", st.hero_turn), ("hero_position", st.hero_position)):
            self.v[k].push(f)
        self.v["actions"].push(Field(tuple(map(tuple, st.actions.value)), st.actions.confidence, st.actions.t_ms, st.actions.source, st.actions.status) if st.actions.known else st.actions)
        seen_slots = set()
        for sd in st.seats:
            seen_slots.add(sd.slot)
            d = self.v_seat.setdefault(sd.slot, {"stack": _Vote(cfg), "bet": _Vote(cfg), "present": _Vote(cfg)})
            d["stack"].push(sd.stack); d["bet"].push(sd.bet)
            d["present"].push(Field.ok(True, 1.0, t, "seen"))
        for slot, d in self.v_seat.items():
            if slot not in seen_slots:
                d["present"].push(Field.ok(False, 1.0, t, "unseen"))
        # --- commit
        cm = Committed(hand_id=self.cur["id"], t_ms=t)
        cm.hero_cards = [v.value for v in self.v_hero]
        # board grows only
        bvals = [v.value for v in self.v_board]
        bc = self.v["board_count"].value
        prev_board = self.committed.board
        board = list(prev_board)
        for i, v in enumerate(bvals):
            if i < len(board):
                if board[i] is None and v is not None:
                    board[i] = v
            elif v is not None or (bc is not None and i < bc):
                board.append(v)
        if bc is not None:
            board = board[:max(bc, len(prev_board))] if bc >= len(prev_board) else board
        cm.board = board
        cm.pot, cm.to_call, cm.hero_stack = self.v["pot"].value, self.v["to_call"].value, self.v["hero_stack"].value
        cm.street = self.v["street"].value
        cm.dealer_slot = self.v["dealer"].value
        # a position with a competing reading is withheld (None: the chart then does not answer) instead of blocking every action
        cm.hero_position = None if self.v["hero_position"].pending else self.v["hero_position"].value
        cm.hero_turn = self.v["hero_turn"].value
        cm.actions = [list(a) for a in self.v["actions"].value] if self.v["actions"].value is not None else None
        for slot, d in self.v_seat.items():
            if d["present"].value:
                cm.seats[slot] = {"stack": d["stack"].value, "bet": d["bet"].value}
        cm.pending = [k for k, v in self.v.items() if v.pending and k != "hero_position"] +[f"hero[{i}]" for i, v in enumerate(self.v_hero) if v.pending]
        cm.blocked = sorted(set(blocked))
        if cm.pending:
            cm.blocked.append("PENDING_CHANGE " + ",".join(cm.pending))
        if cm.hero_turn and any(x is None for x in cm.hero_cards):
            cm.blocked.append("HERO_CARDS_UNCOMMITTED")
        if cm.hero_turn and cm.pot is None:
            cm.blocked.append("POT_UNCOMMITTED")
        if cm.hero_turn and cm.actions is None:
            cm.blocked.append("ACTIONS_UNCOMMITTED")
        self.committed = cm
        self.prev_state = st if not any(i["severity"] == "error" for i in tr) else self.prev_state
        snap = cm.to_dict()
        if not self.cur["snapshots"] or {k: v for k, v in snap.items() if k not in ("t_ms", "blocked", "pending")} != {k: v for k, v in self.cur["snapshots"][-1].items() if k not in ("t_ms", "blocked", "pending")}:
            self.cur["snapshots"].append(snap)
        return cm

    def _update_boundary_counters(self, st: TableState) -> None:
        # consecutive-frame counters for board shrink / pot collapse evidence
        self._bs = getattr(self, "_bs", {})
        n = st.board_count.value if st.board_count.known else None
        cur_board = len([c for c in self.committed.board if c is not None]) if self.committed.board else 0
        for k in (0, 3, 4):
            self._bs[k] = self._bs.get(k, 0) + 1 if (n == k and k < cur_board) else 0
        pp = self.committed.pot.amount if self.committed.pot else None
        cp = money_value(st.pot)
        self._pc = (getattr(self, "_pc", 0) + 1) if (pp and cp is not None and cp < 0.5 * pp) else 0

    def _close_hand(self, strength: str, reason: str, t: int) -> None:
        h = self.cur
        h["status"] = "closed"
        h["end_reason"] = reason
        h["boundary_evidence"].append({"strength": strength, "reason": reason, "t": t})
        h["complete"] = False if (strength == "weak" or h["gaps"]) else None
        self.hands.append(h)
        self._bs = {}; self._pc = 0

    def finish(self) -> list[dict]:
        if self.cur["frames"] > 0:
            h = self.cur
            h["status"] = "open_at_end"
            h["complete"] = None
            self.hands.append(h)
            self.cur = self._fresh_hand(None)
        return self.hands
