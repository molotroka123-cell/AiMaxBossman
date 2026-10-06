"""Headless No-Limit Texas Hold'em hand engine (2-6 seats, deterministic).

``play_hand`` deals and plays exactly one hand and returns a ``HandResult``.
Rules implemented:

* blinds (heads-up: the button posts the small blind and acts first preflop,
  last postflop); a short stack posts what it has and is all-in;
* betting rounds with "raise to" amounts; minimum bet = big blind, minimum
  raise = the size of the last full bet/raise; an all-in for less than a full
  raise does NOT re-open raising for players who already acted;
* side pots by contribution levels, uncalled bets returned, showdown with split
  pots (odd chips go to the first winner left of the button);
* the deck is a pure function of ``deck_seed``; seat ``i`` always receives
  ``deck[2i], deck[2i+1]`` and the board is the next five cards, so the same
  seed deals the same cards to the same seat regardless of the actions taken
  (this enables paired / duplicate comparisons in the arena).

Engine is strict: an illegal action raises ``IllegalAction``.
Known simplification: several consecutive short all-in raises that together
exceed a full raise do not re-open the action.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from .cards import Card, cards_str, new_deck
from .evaluator import category_name, evaluate

MAX_SEATS = 6
STREETS = ("preflop", "flop", "turn", "river")
_BOARD_AFTER = {"preflop": 0, "flop": 3, "turn": 4, "river": 5}


class IllegalAction(ValueError):
    pass


@dataclass(frozen=True)
class Action:
    """``kind`` in fold/check/call/bet/raise. For bet/raise ``amount`` is the
    player's total street bet after the action ("raise to")."""

    kind: str
    amount: int = 0

    def __str__(self) -> str:
        return f"{self.kind} {self.amount}" if self.kind in ("bet", "raise") else self.kind


@dataclass(frozen=True)
class LegalActions:
    to_call: int  # chips needed to call (already capped at the stack)
    can_check: bool
    can_call: bool
    can_fold: bool
    can_bet: bool
    can_raise: bool
    min_to: int  # min legal total street bet for bet/raise (== max_to when only all-in is allowed)
    max_to: int  # all-in total

    def validate(self, action: Action) -> None:
        k = action.kind
        ok = (
            (k == "fold" and self.can_fold)
            or (k == "check" and self.can_check)
            or (k == "call" and self.can_call)
            or (k == "bet" and self.can_bet and self.min_to <= action.amount <= self.max_to)
            or (k == "raise" and self.can_raise and self.min_to <= action.amount <= self.max_to)
        )
        if not ok:
            raise IllegalAction(f"illegal {action} given {self}")


@dataclass(frozen=True)
class Observation:
    hand_id: int
    seat: int
    street: str
    hole: tuple[Card, ...]
    board: tuple[Card, ...]
    pot: int  # all chips committed this hand, including current street bets
    to_call: int
    stack: int  # chips behind (not yet committed)
    my_street_bet: int
    current_bet: int
    big_blind: int
    n_players: int  # players dealt in
    n_active_opponents: int  # opponents who have not folded
    raises_this_street: int
    position: int  # 0 = button, 1 = SB, ... (seats after the button among dealt players)
    legal: LegalActions


class Agent(Protocol):
    name: str

    def act(self, obs: Observation, rng: random.Random) -> Action: ...


@dataclass
class ActionRecord:
    seat: int
    street: str
    kind: str
    added: int  # chips moved from stack to pot by this action
    to: int  # street bet after the action


@dataclass
class SeatStats:
    dealt: bool = False
    vpip: bool = False
    pfr: bool = False
    saw_flop: bool = False
    post_aggr: int = 0  # postflop bets + raises
    post_calls: int = 0
    showdown: bool = False
    won_showdown: bool = False


@dataclass
class HandResult:
    hand_id: int
    button: int
    holes: dict[int, tuple[Card, ...]]
    board: tuple[Card, ...]
    start_stacks: list[int]
    end_stacks: list[int]
    deltas: list[int]
    actions: list[ActionRecord]
    stats: list[SeatStats]
    pots: list[tuple[int, list[int], list[int]]] = field(default_factory=list)  # (amount, eligible, winners)
    showdown_hands: dict[int, str] = field(default_factory=dict)

    def summary(self) -> str:
        acts = " | ".join(f"s{a.seat}:{a.street[0]}:{a.kind}{'' if a.kind in ('fold', 'check') else ' ' + str(a.to)}" for a in self.actions)
        holes = ", ".join(f"s{s}={cards_str(h)}" for s, h in sorted(self.holes.items()))
        return f"#{self.hand_id} btn={self.button} {holes} board={cards_str(self.board)} deltas={self.deltas} :: {acts}"


def decision_seed(deck_seed: int, seat: int, k: int) -> int:
    """Per-decision RNG seed that does not depend on other players' decisions."""
    return (deck_seed * 1_000_003 + seat * 10_007 + k * 101 + 17) & 0x7FFF_FFFF_FFFF


def _next_seat(seat: int, dealt: Sequence[bool]) -> int:
    n = len(dealt)
    for step in range(1, n + 1):
        s = (seat + step) % n
        if dealt[s]:
            return s
    raise RuntimeError("no dealt seat")


def play_hand(
    agents: Sequence[Agent],
    stacks: Sequence[int],
    button: int,
    small_blind: int,
    big_blind: int,
    deck_seed: int,
    hand_id: int = 0,
    deck: Sequence[Card] | None = None,
) -> HandResult:
    """Play one hand. ``deck`` (52 distinct cards) overrides the seeded shuffle; tests use it."""
    n = len(agents)
    if not 2 <= n <= MAX_SEATS or len(stacks) != n:
        raise ValueError(f"need 2..{MAX_SEATS} seats with matching stacks")
    if not 0 < small_blind <= big_blind:
        raise ValueError("bad blinds")
    if any(s < 0 for s in stacks):
        raise ValueError("negative stack")
    dealt = [s > 0 for s in stacks]
    if sum(dealt) < 2:
        raise ValueError("need at least two players with chips")

    if deck is None:
        deck = new_deck(random.Random(deck_seed))
    elif len(deck) != 52 or len(set(deck)) != 52:
        raise ValueError("deck must contain 52 distinct cards")
    holes = {s: (deck[2 * s], deck[2 * s + 1]) for s in range(n) if dealt[s]}
    full_board = tuple(deck[2 * MAX_SEATS : 2 * MAX_SEATS + 5])

    stack = list(stacks)
    committed = [0] * n
    street_bet = [0] * n
    folded = [not d for d in dealt]
    stats = [SeatStats(dealt=d) for d in dealt]
    actions: list[ActionRecord] = []
    decisions = [0] * n
    n_dealt = sum(dealt)

    if not dealt[button]:
        button = _next_seat(button, dealt)
    # position index: 0 = button, then clockwise among dealt seats
    position: dict[int, int] = {}
    s = button
    for i in range(n_dealt):
        position[s] = i
        s = _next_seat(s, dealt)

    if n_dealt == 2:
        sb_seat = button
        bb_seat = _next_seat(button, dealt)
    else:
        sb_seat = _next_seat(button, dealt)
        bb_seat = _next_seat(sb_seat, dealt)

    def put(seat: int, amount: int) -> int:
        amount = min(amount, stack[seat])
        stack[seat] -= amount
        committed[seat] += amount
        street_bet[seat] += amount
        return amount

    def can_act(seat: int) -> bool:
        return not folded[seat] and stack[seat] > 0

    def active_count() -> int:
        return sum(1 for x in range(n) if not folded[x])

    for street in STREETS:
        board = full_board[: _BOARD_AFTER[street]]
        for x in range(n):
            street_bet[x] = 0
        min_raise_inc = big_blind
        raises = 0
        if street == "preflop":
            put(sb_seat, small_blind)
            put(bb_seat, big_blind)
            actions.append(ActionRecord(sb_seat, street, "post_sb", street_bet[sb_seat], street_bet[sb_seat]))
            actions.append(ActionRecord(bb_seat, street, "post_bb", street_bet[bb_seat], street_bet[bb_seat]))
            current_bet = big_blind
            first = _next_seat(bb_seat, dealt)
        else:
            current_bet = 0
            first = _next_seat(button, dealt)
            if street == "flop":
                for x in range(n):
                    if not folded[x]:
                        stats[x].saw_flop = True

        if active_count() <= 1:
            break
        actors = [x for x in range(n) if can_act(x)]
        needs_action = {x: True for x in actors}
        may_raise = {x: True for x in actors}
        acted_since_full_raise: set[int] = set()
        # Nobody to play against: a lone player able to act with nothing to call has no decision.
        if len(actors) <= 1 and all(street_bet[x] >= current_bet for x in actors):
            needs_action = {}

        seat = first
        guard = 0
        while any(needs_action.values()) and active_count() > 1:
            guard += 1
            if guard > 1000:
                raise RuntimeError("betting loop did not terminate")
            if not needs_action.get(seat):
                seat = (seat + 1) % n
                continue
            to_call_full = current_bet - street_bet[seat]
            to_call = min(to_call_full, stack[seat])
            others_can_act = any(can_act(x) for x in range(n) if x != seat)
            max_to = street_bet[seat] + stack[seat]
            raise_ok = others_can_act and may_raise[seat] and max_to > current_bet
            min_to = min(current_bet + min_raise_inc, max_to)
            legal = LegalActions(
                to_call=to_call,
                can_check=to_call_full <= 0,
                can_call=to_call_full > 0,
                can_fold=to_call_full > 0,
                can_bet=raise_ok and current_bet == 0,
                can_raise=raise_ok and current_bet > 0,
                min_to=min_to,
                max_to=max_to,
            )
            obs = Observation(
                hand_id=hand_id,
                seat=seat,
                street=street,
                hole=holes[seat],
                board=board,
                pot=sum(committed),
                to_call=to_call,
                stack=stack[seat],
                my_street_bet=street_bet[seat],
                current_bet=current_bet,
                big_blind=big_blind,
                n_players=n_dealt,
                n_active_opponents=active_count() - 1,
                raises_this_street=raises,
                position=position[seat],
                legal=legal,
            )
            rng = random.Random(decision_seed(deck_seed, seat, decisions[seat]))
            decisions[seat] += 1
            action = agents[seat].act(obs, rng)
            legal.validate(action)

            kind = action.kind
            added = 0
            if kind == "fold":
                folded[seat] = True
            elif kind == "call":
                added = put(seat, to_call)
            elif kind in ("bet", "raise"):
                added = put(seat, action.amount - street_bet[seat])
                increment = street_bet[seat] - current_bet
                current_bet = street_bet[seat]
                raises += 1
                if increment >= min_raise_inc:
                    min_raise_inc = increment
                    acted_since_full_raise = set()
                    for x in actors:
                        if x != seat and can_act(x):
                            needs_action[x] = True
                            may_raise[x] = True
                else:  # short all-in raise: others must respond but only fresh players may re-raise
                    for x in actors:
                        if x != seat and can_act(x):
                            needs_action[x] = True
                            may_raise[x] = x not in acted_since_full_raise
            acted_since_full_raise.add(seat)
            needs_action[seat] = False
            actions.append(ActionRecord(seat, street, kind, added, street_bet[seat]))

            st = stats[seat]
            if street == "preflop":
                if kind in ("call", "raise", "bet"):
                    st.vpip = True
                if kind in ("raise", "bet"):
                    st.pfr = True
            else:
                if kind in ("bet", "raise"):
                    st.post_aggr += 1
                elif kind == "call":
                    st.post_calls += 1
            seat = (seat + 1) % n

        if active_count() <= 1:
            break

    # ---- settle pots ----
    contenders = [x for x in range(n) if not folded[x]]
    showdown = len(contenders) > 1
    scores: dict[int, int] = {}
    showdown_hands: dict[int, str] = {}
    if showdown:
        for x in contenders:
            scores[x] = evaluate(list(holes[x]) + list(full_board))
            showdown_hands[x] = category_name(scores[x])
            stats[x].showdown = True

    levels = sorted({c for c in committed if c > 0})
    pots: list[tuple[int, list[int], list[int]]] = []
    prev = 0
    carry = 0
    award_order = []
    s = _next_seat(button, dealt)
    for _ in range(n_dealt):
        award_order.append(s)
        s = _next_seat(s, dealt)
    for lvl in levels:
        amount = sum(min(c, lvl) - min(c, prev) for c in committed) + carry
        prev = lvl
        eligible = [x for x in contenders if committed[x] >= lvl]
        if not eligible:
            carry = amount
            continue
        carry = 0
        if len(eligible) == 1:
            winners = eligible
        else:
            best = max(scores[x] for x in eligible)
            winners = [x for x in eligible if scores[x] == best]
        share, rem = divmod(amount, len(winners))
        for w in winners:
            stack[w] += share
        for w in [x for x in award_order if x in winners][:rem]:
            stack[w] += 1
        pots.append((amount, eligible, winners))
        if len(eligible) > 1:
            for w in winners:
                stats[w].won_showdown = True
    if carry:  # cannot happen with legal play; keep chips conserved anyway
        stack[contenders[0]] += carry

    deltas = [stack[x] - stacks[x] for x in range(n)]
    if sum(deltas) != 0:
        raise RuntimeError(f"chip conservation violated: {deltas}")
    return HandResult(
        hand_id=hand_id,
        button=button,
        holes=holes,
        board=full_board if showdown else full_board[: _board_seen(actions)],
        start_stacks=list(stacks),
        end_stacks=stack,
        deltas=deltas,
        actions=actions,
        stats=stats,
        pots=pots,
        showdown_hands=showdown_hands,
    )


def _board_seen(actions: Sequence[ActionRecord]) -> int:
    last = actions[-1].street if actions else "preflop"
    return _BOARD_AFTER[last]
