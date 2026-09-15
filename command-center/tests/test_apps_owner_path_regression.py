"""Owner path for Apps, regression-checked (audit section 14).

History: app control needed an environment variable plus a restart, and every
refused start was a 409 indistinguishable from a real conflict. The fix
(policy via API, 403 with a machine-readable code) landed on the night line
before this closure; the owner's 2026-09-08 log still shows 409 on
file-commander-mini because that runtime predates it. This test pins the
current contract so the fix does not disappear in convergence:

    enable → start → start again → stop → stop again → disable → start refused
    with a NAMED policy reason, twice over, with real child processes.

Contributed as a passing negative control by the Astra/Codex breaker run
(R4, `test_apps_repeated_enable_start_stop_disable`).
"""
from __future__ import annotations

from .test_apps_control import apps_root, make_app  # noqa: F401


async def test_repeated_enable_start_stop_disable_stays_coherent(env, apps_root, monkeypatch):  # noqa: F811
    from bcc.features import apps_control as ctl
    monkeypatch.delenv(ctl.FLAG, raising=False)
    monkeypatch.delenv(ctl.LOCK_ENV, raising=False)
    make_app(apps_root, "file-commander-mini")
    for _ in range(2):
        for enabled in (True, True):
            assert (await env.client.put("/api/apps/control/policy", json={"enabled": enabled})).status_code == 200
        first = (await env.client.post("/api/apps/file-commander-mini/start")).json()
        second = (await env.client.post("/api/apps/file-commander-mini/start")).json()
        assert first["started"] and second["already_running"] and not second["started"]
        for _ in range(2):
            assert (await env.client.post("/api/apps/file-commander-mini/stop")).status_code == 200
        for _ in range(2):
            assert (await env.client.put("/api/apps/control/policy", json={"enabled": False})).status_code == 200
        refused = await env.client.post("/api/apps/file-commander-mini/start")
        # Not the historical 409 ritual: a policy refusal with a code and a hint.
        assert refused.status_code == 403, refused.text
        err = refused.json()["error"]
        assert err["code"] == "APPS_CONTROL_DISABLED" and err["hint"]


async def test_the_disabled_reason_is_readable_without_env_magic(env, monkeypatch):
    from bcc.features import apps_control as ctl
    monkeypatch.delenv(ctl.FLAG, raising=False)
    monkeypatch.delenv(ctl.LOCK_ENV, raising=False)
    await env.client.put("/api/apps/control/policy", json={"enabled": False})
    policy = (await env.client.get("/api/apps/control/policy")).json()
    assert policy["enabled"] is False and policy["hint"]
