from pokervision.schema import Field, Money, Seat, TableState

SRC = "test"


def ok(v, c=0.9, t=0):
    return Field.ok(v, c, t, SRC)


def unk(why="x", t=0):
    return Field.unknown(t, SRC, why)


def money(a, step=1.0):
    return Money(float(a), step, None, str(a))


def state(t, hero=("As", "Kd"), board=(), pot=15, hero_stack=1000, seats=None, to_call=None, fid=None, hero_turn=None):
    st = TableState(fid or f"f{t}", t, SRC, "test")
    st.hero_cards = [ok(c, t=t) if c else unk(t=t) for c in hero] if hero else [unk(t=t), unk(t=t)]
    st.board = [ok(c, t=t) if c else unk(t=t) for c in board]
    st.board_count = ok(len(board), t=t)
    st.street = ok({0: "preflop", 3: "flop", 4: "turn", 5: "river"}.get(len(board), "flop"), t=t)
    st.pot = ok(money(pot), t=t) if pot is not None else unk(t=t)
    st.to_call = ok(money(to_call), t=t) if to_call is not None else unk(t=t)
    st.hero_stack = ok(money(hero_stack), t=t)
    st.dealer_slot, st.num_seats, st.actions, st.hero_position = unk(t=t), unk(t=t), unk(t=t), unk(t=t)
    st.hero_turn = ok(hero_turn, t=t) if hero_turn is not None else unk(t=t)
    for slot, (stack, bet) in (seats or {}).items():
        sd = Seat(slot)
        sd.stack = ok(money(stack), t=t)
        if bet is not None:
            sd.bet = ok(money(bet), t=t)
        st.seats.append(sd)
    return st
