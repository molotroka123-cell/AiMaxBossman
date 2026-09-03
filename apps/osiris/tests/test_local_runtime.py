from osiris.local_runtime import LocalRuntime, limits_for, CLOUD_SESSION_TOKEN_CAP
from osiris.prompt_cache import cache_key, wrap_cloud


def test_local_has_no_session_cap():
    lim = limits_for("local")
    assert lim.session_token_cap is None
    assert lim.cloud_safety_overlay is False
    assert lim.policy_in_code is True


def test_cloud_keeps_cap():
    lim = limits_for("cloud")
    assert lim.session_token_cap == CLOUD_SESSION_TOKEN_CAP
    assert lim.prompt_cache is True


def test_local_complete():
    out = LocalRuntime().complete("hello")
    assert out["session_token_cap"] is None
    assert "hello" in out["text"]


def test_prompt_cache_stable():
    a = wrap_cloud("q1")
    b = wrap_cloud("q2")
    assert a["cache_key"] == b["cache_key"] == cache_key()
    assert a["applied"] is True
