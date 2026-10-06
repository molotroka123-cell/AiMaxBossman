"""authored_by_lane (opsplug): bossman.toolkit.office - declared gmail/crm/docs tools are honest until the connector exists.

The module is a declaration layer: rights, confirm-by-default for irreversible actions and a handler that
FAILS (error=True) instead of pretending an email was sent.
"""
import pytest

from bossman.toolkit import REGISTRY, ToolContext, clip
from bossman.toolkit import office  # noqa: F401  (registers on import)

NAMES = ["gmail.read", "gmail.draft", "gmail.send", "crm.read", "crm.write", "docs.read"]


def test_all_six_tools_are_registered_with_the_right_rights():
    assert {n: REGISTRY[n].rights for n in NAMES} == {
        "gmail.read": "read", "gmail.draft": "write", "gmail.send": "send",
        "crm.read": "read", "crm.write": "write", "docs.read": "read"}


def test_irreversible_actions_are_confirm_by_default_and_reads_are_not():
    confirm = {n for n in NAMES if REGISTRY[n].confirm_default}
    assert confirm == {"gmail.send", "crm.write"}


def test_reads_have_token_limits_and_drafting_requires_recipient_and_body():
    assert REGISTRY["gmail.read"].token_limit == 1000 and REGISTRY["crm.read"].token_limit == 2000
    assert REGISTRY["gmail.draft"].required == ["to", "body"]
    schema = REGISTRY["gmail.send"].schema()["function"]
    assert schema["name"] == "gmail_send" and "draft_id" in schema["parameters"]["properties"]


@pytest.mark.parametrize("name", NAMES)
async def test_unconfigured_connector_is_an_error_never_a_fake_success(name):
    res = await REGISTRY[name].handler({}, ToolContext(agent="a"))
    assert res.error is True
    assert name in res.content and "не настроен" in res.content
    assert res.one_line == f"{name}: не настроен"
    assert res.render() == res.content                    # not truncated, nothing hidden


def test_clip_helper_used_by_the_token_limits_truncates_long_text():
    text = "слово " * 5000
    clipped, was_cut = clip(text, REGISTRY["gmail.read"].token_limit)
    assert was_cut is True and len(clipped) < len(text)
