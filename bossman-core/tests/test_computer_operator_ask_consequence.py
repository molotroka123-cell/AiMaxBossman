"""CU-APPROVAL regression: the consequence an effect-hook can surface for owner
approval comes from the declared semantic OR the named target/text label, so a
benign semantic can no longer hide a consequential named target."""
from bossman.computer_operator.policy import ComputerPolicy


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
