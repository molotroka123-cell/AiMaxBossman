"""Owner-audit hardening regression for Computer Use CU-VERIFY.

An unknown / mistyped / empty `expect` must never read as verified=True —
verification is only True when a KNOWN condition actually ran. verify() lives
in bcc.features.tools_computer and needs no bossman-core import (the policy-side
ask_consequence regression lives in bossman-core/tests).
"""
import importlib

tc = importlib.import_module("bcc.features.tools_computer")


def _obs(title="Notepad", texts=()):
    return {"window": {"title": title}, "elements": [{"name": n} for n in texts]}


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
