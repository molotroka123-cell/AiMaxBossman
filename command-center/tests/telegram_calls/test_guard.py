"""Dial guard: each rule has a passing case and a rejected case."""
from __future__ import annotations

import pytest

from bcc.telegram_calls.guard import check_dial
from bcc.telegram_calls.settings import CallsSettings
from bcc.telegram_calls.types import CallError, Outcome, PeerRef

ME = 111
PEER = PeerRef(4242, "second")


def ok(**kw) -> CallsSettings:
    base = dict(enabled=True, allowed_peers=(PEER,), peer_confirmed=True)
    base.update(kw)
    return CallsSettings(**base)


class Flag:
    def __init__(self, v):
        self.v = v

    def is_set(self):
        return self.v


def dial(settings=None, me=ME, stop=False, last=None, in_call=False, confirm=False):
    return check_dial(settings or ok(), me, Flag(stop), last, in_call, confirm)


def code(fn) -> str:
    with pytest.raises(CallError) as ei:
        fn()
    return ei.value.code


def test_legit_dial_returns_the_one_peer():
    assert dial() == PEER


def test_disabled_by_default():
    assert code(lambda: dial(CallsSettings())) == "NOT_ENABLED"
    assert code(lambda: dial(ok(enabled=False))) == "NOT_ENABLED"


def test_no_peer_or_unconfirmed_peer_rejected():
    assert code(lambda: dial(ok(allowed_peers=()))) == "PEER_NOT_SELECTED"
    assert code(lambda: dial(ok(peer_confirmed=False))) == "PEER_NOT_SELECTED"


def test_more_than_max_peers_rejected_even_if_constructed_directly():
    s = ok(allowed_peers=(PEER, PeerRef(5, "x")))
    assert code(lambda: dial(s)) == "PEER_NOT_ALLOWED"


def test_peer_equal_to_own_account_rejected_other_peer_passes():
    assert code(lambda: dial(me=PEER.user_id)) == "PEER_IS_SELF"
    assert dial(me=ME) == PEER


def test_unknown_own_account_cannot_prove_peer_is_not_self():
    assert code(lambda: dial(me=None)) == "NOT_LOGGED_IN"


def test_stop_flag_blocks_and_clear_flag_passes():
    assert code(lambda: dial(stop=True)) == "STOP_ACTIVE"
    assert dial(stop=False) == PEER
    assert code(lambda: check_dial(ok(), ME, True, None, False)) == "STOP_ACTIVE"   # plain bool works too


def test_call_in_progress():
    assert code(lambda: dial(in_call=True)) == "CALL_IN_PROGRESS"
    assert dial(in_call=False) == PEER


@pytest.mark.parametrize("last", [Outcome.UNKNOWN, Outcome.CONNECTION_LOST, "unknown", "connection_lost", "garbage"])
def test_uncertain_previous_call_needs_explicit_confirm(last):
    assert code(lambda: dial(last=last)) == "UNCERTAIN_PREVIOUS_CALL"
    assert dial(last=last, confirm=True) == PEER


@pytest.mark.parametrize("last", [None, Outcome.COMPLETED, "declined", "busy", "no_answer", "stopped", "failed"])
def test_certain_previous_outcomes_do_not_need_confirm(last):
    assert dial(last=last) == PEER


def test_confirm_must_be_literal_true():
    assert code(lambda: check_dial(ok(), ME, Flag(False), "unknown", False, "yes")) == "UNCERTAIN_PREVIOUS_CALL"


def test_confirm_does_not_bypass_stop_or_enabled():
    assert code(lambda: dial(stop=True, last="unknown", confirm=True)) == "STOP_ACTIVE"
    assert code(lambda: dial(CallsSettings(), last="unknown", confirm=True)) == "NOT_ENABLED"
