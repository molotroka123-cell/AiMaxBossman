"""B11 #6 — манифест plugin-адаптеров проверяется до регистрации (fail-closed).

Дефект: `plugins.setup` регистрировал любую запись MANIFEST. Destructive-
капабилити с risk="allow" становилась AUTO (отправка без подтверждения),
risk="deny" (или опечатка) регистрировалась как вызываемая ASK, хотя по
контракту deny-капабилити не регистрируется вовсе, а `required`, которого нет
в input_schema, давал инструмент, который модель не может вызвать корректно.
"""
from __future__ import annotations

import pytest

import bcc.features.plugins as P
from bcc.tools import REGISTRY


def _cap(**over) -> P.Capability:
    base = dict(plugin_id="probe", capability="send", scope="probe.send", risk="ask",
                destructive=True, permission="", credential_ref="", network_targets=("x",),
                description="probe", input_schema={"text": {"type": "string"}},
                required=("text",))
    base.update(over)
    return P.Capability(**base)


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    for name in [n for n in REGISTRY.names() if n.startswith("plugin:probe")]:
        REGISTRY.unregister(name)


def test_real_manifest_is_valid():
    assert P.validate_manifest(P.MANIFEST) == []


@pytest.mark.parametrize("over, needle", [
    ({"risk": "allow"}, "destructive"),
    ({"risk": "deny"}, "risk"),
    ({"risk": "Ask"}, "risk"),
    ({"required": ("text", "missing")}, "required"),
    ({"input_schema": {"text": "string"}}, "схем"),
    ({"plugin_id": "Bad Id"}, "имя"),
    ({"credential_ref": "BAD-REF"}, "кред"),
    ({"network_targets": ()}, "network_targets"),
])
def test_bad_capability_rejected_in_russian(over, needle):
    errs = P.capability_errors(_cap(**over))
    assert errs and any(needle in e for e in errs), errs


async def test_setup_skips_invalid_capability(monkeypatch):
    bad_auto_send = _cap(capability="send", risk="allow")            # destructive + allow
    bad_deny = _cap(capability="wipe", risk="deny", destructive=False)
    good = _cap(capability="read", risk="allow", destructive=False)
    monkeypatch.setattr(P, "MANIFEST", [bad_auto_send, bad_deny, good])
    await P.setup(None)
    assert REGISTRY.get("plugin:probe.send") is None
    assert REGISTRY.get("plugin:probe.wipe") is None
    assert REGISTRY.get("plugin:probe.read") is not None
    assert P.REGISTERED == ["plugin:probe.read"]
    assert {r["tool"] for r in P.REJECTED} == {"plugin:probe.send", "plugin:probe.wipe"}
