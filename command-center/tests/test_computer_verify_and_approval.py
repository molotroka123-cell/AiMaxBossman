"""Owner-audit hardening regressions for Computer Use.

CU-VERIFY: an unknown / mistyped / empty `expect` must never read as
verified=True — verification is only True when a KNOWN condition actually ran.

CU-APPROVAL: the consequence that an effect-hook surfaces for owner approval
comes from the declared semantic OR the model-named target/text label, so a
benign semantic can no longer hide a consequential named target.
"""
import importlib
import pytest

from bossman.computer_operator.policy import ComputerPolicy

tc = importlib.import_module("bcc.features.tools_computer")


def _obs(title="Notepad", texts=()):
    return {"window": {"title": title}, "elements": [{"name": n} for n in texts]}


# ---------------- CU-VERIFY ----------------
def test_verify_empty_expect_is_unverified_not_ok():
    assert tc.verify(_obs(), {})[0] is None


def test_verify_unknown_key_is_not_true():
    # regression: an unknown non-empty expect previously returned verified=True
    # with zero checks performed.
    verified, _ = tc.verify(_obs(texts=["hello"]), {"file_saved": "yes"})
    assert verified is False


def test_verify_mixed_known_and_unknown_is_not_true():
    verified, _ = tc.verify(_obs(texts=["Saved"]), {"contains_text": "Saved", "bogus": "x"})
    assert verified is not True


def test_verify_mistyped_value_is_not_true():
    verified, _ = tc.verify(_obs(texts=["Saved"]), {"contains_text": ["Saved"]})
    assert verified is not True


def test_verify_known_condition_is_actually_checked():
    assert tc.verify(_obs(texts=["Saved to disk"]), {"contains_text": "Saved"})[0] is True
    assert tc.verify(_obs(texts=["nothing here"]), {"contains_text": "Saved"})[0] is False


# ---------------- CU-APPROVAL ----------------
def test_ask_consequence_from_declared_semantic():
    assert ComputerPolicy.ask_consequence({"semantic": "pay"}) == "pay"


def test_benign_semantic_cannot_hide_a_consequential_named_target():
    # regression: semantic="click" used to stamp _approved_consequence=True and
    # skip the owner ASK for an observed-consequence target like "Delete account".
    assert ComputerPolicy.ask_consequence({"semantic": "click", "target": "Delete account"}) is not None


def test_ask_consequence_is_none_for_a_benign_action():
    assert ComputerPolicy.ask_consequence({"semantic": "noop", "target": "OK"}) is None
    assert ComputerPolicy.ask_consequence({"target": "OK"}) is None


def test_ask_consequence_reads_cyrillic_target_label():
    assert ComputerPolicy.ask_consequence({"target": "Перевести деньги"}) == "transfer"
