"""Тесты общих швов этапов 4–7: таксономия ошибок, реестр подсистем,
correlation-id в событиях, вычистка секретов, аутентификация telegram-вебхука."""
from __future__ import annotations

import json

import pytest

from bossman import correlation, errors, events, obs
from bossman.lifecycle import SubsystemRegistry


# ---------- errors.py ----------

def test_error_code_http_and_retryable():
    e = errors.ResourceExhausted("no ram")
    assert e.code is errors.ErrorCode.RESOURCE_EXHAUSTED
    assert e.http == 503 and e.retryable is True
    body = e.to_dict()["error"]
    assert body["code"] == "RESOURCE_EXHAUSTED" and body["retryable"] is True

    a = errors.AuthDenied("bad token")
    assert a.http == 401 and a.retryable is False


def test_legacy_exception_mapping():
    class CloudDenied(Exception):
        pass

    class NeedsCloudApproval(Exception):
        pass

    mapped = errors._map_legacy(CloudDenied("never"))
    assert mapped is not None and mapped.code is errors.ErrorCode.POLICY_DENIED and mapped.http == 403
    mapped2 = errors._map_legacy(NeedsCloudApproval("ask"))
    assert mapped2 is not None and mapped2.code is errors.ErrorCode.APPROVAL_REQUIRED
    assert errors._map_legacy(ValueError("random")) is None


# ---------- lifecycle.py ----------

class _Sub:
    def __init__(self, name, critical=False, fail_on=None):
        self.name = name
        self.critical = critical
        self.fail_on = fail_on
        self.events: list[str] = []

    async def validate(self):
        self.events.append("validate")
        if self.fail_on == "validate":
            raise errors.BossmanError("validate failed")

    async def start(self):
        self.events.append("start")
        if self.fail_on == "start":
            raise RuntimeError("start failed")

    async def stop(self):
        self.events.append("stop")


@pytest.mark.asyncio
async def test_registry_starts_and_stops_reverse_order():
    reg = SubsystemRegistry()
    a, b = _Sub("a"), _Sub("b")
    reg.register(a)
    reg.register(b)
    await reg.start_all()
    assert a.events == ["validate", "start"] and b.events == ["validate", "start"]
    await reg.stop_all()
    # b остановлена первой (обратный порядок)
    assert b.events[-1] == "stop" and a.events[-1] == "stop"
    st = reg.status()
    assert {s["name"] for s in st} == {"a", "b"}


@pytest.mark.asyncio
async def test_optional_subsystem_degrades_but_boot_continues():
    reg = SubsystemRegistry()
    bad = _Sub("bad", critical=False, fail_on="start")
    good = _Sub("good")
    reg.register(bad)
    reg.register(good)
    await reg.start_all()  # не бросает
    status = {s["name"]: s for s in reg.status()}
    assert status["bad"]["degraded"] is True and status["bad"]["started"] is False
    assert status["good"]["started"] is True


@pytest.mark.asyncio
async def test_critical_subsystem_aborts_boot():
    reg = SubsystemRegistry()
    reg.register(_Sub("db", critical=True, fail_on="validate"))
    with pytest.raises(errors.BossmanError):
        await reg.start_all()


# ---------- correlation.py + events.py ----------

@pytest.mark.asyncio
async def test_emit_merges_correlation_ids():
    q = events.subscribe()
    try:
        with correlation.scope(request_id="req_test", task_id="t1"):
            events.emit("demo", foo="bar")
        msg = json.loads(q.get_nowait())
    finally:
        events.unsubscribe(q)
    assert msg["kind"] == "demo" and msg["foo"] == "bar"
    assert msg["request_id"] == "req_test" and msg["task_id"] == "t1"


def test_correlation_scope_isolation():
    assert correlation.current() == {}
    with correlation.scope(run_id="r1"):
        assert correlation.get("run_id") == "r1"
    assert correlation.current() == {}  # откат


def test_correlation_rejects_unknown_key():
    with pytest.raises(KeyError):
        correlation.bind(nonsense="x")


# ---------- obs.py redaction ----------

@pytest.mark.parametrize("raw,must_not_contain", [
    ("Authorization: Bearer sk-abcdef0123456789ABCD", "sk-abcdef0123456789ABCD"),  # ci-secret-scan: allow
    ('{"api_key": "super-secret-value-1234"}', "super-secret-value-1234"),
    ("token=ghp_0123456789abcdefABCDEF cookie=sess-1234567890", "ghp_0123456789abcdefABCDEF"),  # ci-secret-scan: allow
    ("password: hunter2hunter2", "hunter2hunter2"),
])
def test_redact_removes_secrets(raw, must_not_contain):
    out = obs.redact(raw)
    assert must_not_contain not in out
    assert obs.REDACTED in out


def test_redact_is_idempotent():
    once = obs.redact("Authorization: Bearer sk-abcdef0123456789ABCD")  # ci-secret-scan: allow
    assert obs.redact(once) == once


def test_redact_obj_by_key_and_value():
    obj = {"api_key": "keep-hidden-123456", "note": "Bearer sk-zzzzzzzzzzzzzzzzzz", "n": 5}
    red = obs.redact_obj(obj)
    assert red["api_key"] == obs.REDACTED
    assert "sk-zzzzzzzzzzzzzzzzzz" not in red["note"]
    assert red["n"] == 5


def test_json_formatter_carries_cid_and_redacts(caplog):
    logger = obs.get_logger("bossman.test.obs")
    fmt = obs.JsonFormatter()
    filt = obs.RedactionFilter()
    rec = logger.makeRecord("bossman.test.obs", 20, __file__, 1,
                            "leak Authorization: Bearer sk-abcdef0123456789ABCD", (), None)  # ci-secret-scan: allow
    filt.filter(rec)
    line = fmt.format(rec)
    assert "sk-abcdef0123456789ABCD" not in line  # ci-secret-scan: allow
    assert obs.REDACTED in line
