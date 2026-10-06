import pytest

from pokervision.parse import parse_card, parse_money
from pokervision.validate import check_transition, validate_state

from .helpers import ok, state, unk


@pytest.mark.parametrize("text,amount,step", [("1,000", 1000, 1), ("$50.0K", 50000, 100), ("1.2M", 1.2e6, 1e5), ("€5,300", 5300, 1), ("995", 995, 1)])
def test_money_legal(text, amount, step):
    m = parse_money(text)
    assert m is not None and m.amount == pytest.approx(amount) and m.step == pytest.approx(step)


@pytest.mark.parametrize("text", ["", "1,00", "12,34,567", "1.5", "$", "abc", "1,0000", "--5", "1..2K", "5.K"])
def test_money_malformed_is_none_not_guessed(text):
    assert parse_money(text) is None


def test_money_rounding_agreement():
    assert parse_money("$50.0K").agrees(parse_money("50,040"))
    assert not parse_money("$50.0K").agrees(parse_money("50,400"))


def test_cards():
    assert parse_card("As") == "As" and parse_card("td") == "Td"
    for bad in ("1s", "Ax", "", "AsK"):
        assert parse_card(bad) is None


def test_duplicate_card_demotes_both_never_repairs():
    st = state(1, hero=("As", "Kd"), board=("As", "7c", "2d"))
    validate_state(st)
    assert any(i["code"] == "DUPLICATE_CARD" for i in st.issues)
    assert not st.hero_cards[0].known and not st.board[0].known     # both copies demoted
    assert st.hero_cards[1].known and st.board[1].known            # innocents untouched


def test_legal_state_passes_unchanged():                             # negative control for the duplicate rule
    st = state(1, hero=("As", "Kd"), board=("Qs", "7c", "2d"))
    validate_state(st)
    assert not st.issues and st.hero_cards[0].known


def test_bad_board_size_and_street_mismatch():
    st = state(1, board=("Qs", "7c"))
    validate_state(st)
    assert any(i["code"] == "BAD_BOARD_COUNT" for i in st.issues) and not st.street.known
    st2 = state(1, board=("Qs", "7c", "2d")); st2.street = ok("river")
    validate_state(st2)
    assert any(i["code"] == "STREET_BOARD_MISMATCH" for i in st2.issues) and not st2.street.known


def test_mixed_currency_demotes_amounts():
    from pokervision.schema import Money
    st = state(1)
    st.pot = ok(Money(10, 1, "USD", "$10")); st.hero_stack = ok(Money(10, 1, "EUR", "€10"))
    validate_state(st)
    assert any(i["code"] == "MIXED_CURRENCY" for i in st.issues) and not st.pot.known


def test_transitions():
    flop = state(1, board=("Qs", "7c", "2d")); turn = state(2, board=("Qs", "7c", "2d", "9h"))
    assert check_transition(flop, turn) == []                         # legal growth
    codes = {i["code"] for i in check_transition(turn, flop)}
    assert "STREET_REGRESSION" in codes
    changed = state(3, board=("Qs", "8c", "2d", "9h"))
    assert "BOARD_CARD_CHANGED" in {i["code"] for i in check_transition(turn, changed)}
    assert "HERO_CARDS_CHANGED" in {i["code"] for i in check_transition(state(1), state(2, hero=("2c", "3d")))}
