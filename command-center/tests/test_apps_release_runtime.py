"""Installed catalogue, child scope and recovered process ownership."""
import json
import asyncio
from pathlib import Path

import httpx
import pytest

from bcc.features import apps, apps_control as ctl
from .test_apps_control import apps_root, make_app  # noqa: F401


def test_wheel_catalogue_and_manual_command_do_not_need_checkout(tmp_path, monkeypatch):
    package = tmp_path / "site-packages/bcc"
    metadata = package / "_apps/file-commander-mini"
    metadata.mkdir(parents=True)
    (metadata / "app.manifest.yaml").write_text("id: file-commander-mini\nname: File Commander\ndefault_port: 8911\n")
    (metadata / "pyproject.toml").write_text('[project.scripts]\nfile-commander-mini = "file_commander_mini.cli:main"\n')
    monkeypatch.delenv("BCC_APPS_DIR", raising=False)
    monkeypatch.setattr(apps, "ROOT", package.parent)
    monkeypatch.setattr(apps, "PKG_DIR", package)
    assert apps.apps_directory() == package / "_apps"
    monkeypatch.setattr(apps, "APPS_DIR", apps.apps_directory())
    assert apps._describe(metadata / "app.manifest.yaml")["manifest_path"] == str(Path("apps/file-commander-mini/app.manifest.yaml"))
    assert ctl.command_for("file-commander-mini")["manual"] == "python -m file_commander_mini.cli serve"


def test_only_file_commander_gets_a_scoped_file_root_and_private_token(tmp_path, monkeypatch):
    monkeypatch.delenv("FILE_COMMANDER_ROOTS", raising=False)
    monkeypatch.setenv("BCC_TOKEN", "private-parent-secret")
    data = tmp_path / "state"
    env = ctl._child_env(tmp_path / "file-commander-mini", 8911, data)
    assert env["FILE_COMMANDER_ROOTS"] == str(data / "workspace")
    assert env["BOSSMAN_APPS_DATA"] == str(data / "app-data")
    assert len(env["BOSSMAN_APP_TOKEN"]) >= 32
    assert "BCC_TOKEN" not in env and "private-parent-secret" not in env.values()
    other = ctl._child_env(tmp_path / "other", 8912, data)
    assert "BOSSMAN_APP_TOKEN" not in other and "FILE_COMMANDER_ROOTS" not in other
    assert ctl._child_env(tmp_path / "file-commander-mini", 8911, data)["BOSSMAN_APP_TOKEN"] == env["BOSSMAN_APP_TOKEN"]


@pytest.mark.parametrize("status,body", [(404, {"status": "ok"}), (200, {}), (200, {"status": "unhealthy"}), (200, {"status": "NOT_CONFIGURED"})])
async def test_health_http_response_does_not_imply_readiness(status, body):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status, json=body))) as client:
        result = await apps._probe({"port": 8999, "health_path": "/health"}, client)
    assert result["reachable"] is True and result["status"] != "LIVE"


async def test_reused_or_tampered_process_record_never_grants_stop(env, apps_root):  # noqa: F811
    assert ctl._process_namespace_verified(), "OWNER_REQUIRED: /proc and subprocess PID namespaces differ; run this gate on a standard Linux/Windows host"
    app_dir, port = make_app(apps_root, "fake-app")
    await env.client.put("/api/apps/control/policy", json={"enabled": True})
    result = (await env.client.post("/api/apps/fake-app/start")).json()
    rec = ctl._processes.pop("fake-app")
    path = ctl._record_path("fake-app", env.settings.data_dir)
    record = json.loads(path.read_text())
    record["process_created"] -= 100
    path.write_text(json.dumps(record))
    try:
        response = (await env.client.post("/api/apps/fake-app/stop")).json()
        assert response["owned"] is False and response["stopped"] is False
        assert rec.proc.poll() is None and ctl.port_busy(port)
    finally:
        rec.proc.terminate()
        rec.proc.wait(timeout=5)
        rec.log_file.close()


async def test_proxy_is_not_an_arbitrary_route_or_app_proxy(env):
    for path in ("etc/passwd", "api/jobs", "http://evil.test/", "api/files/undo/../../etc"):
        response = await env.client.get("/api/apps/file-commander-mini/view/" + path)
        assert response.status_code == 404


async def test_corrupt_saved_deny_never_falls_back_to_enabled_environment(env, apps_root, monkeypatch):  # noqa: F811
    import sqlalchemy as sa
    from bcc.db import settings_kv
    make_app(apps_root, "fake-app")
    monkeypatch.setenv(ctl.FLAG, "1")
    await env.client.put("/api/apps/control/policy", json={"enabled": False})
    async with env.svc.db.session() as session:
        await session.execute(sa.update(settings_kv).where(settings_kv.c.key == ctl.SETTING_KEY).values(value_enc="corrupt-ciphertext"))
        await session.commit()
    try:
        response = await env.client.post("/api/apps/fake-app/start")
        assert response.status_code == 403, response.text
        policy = (await env.client.get("/api/apps/control/policy")).json()
        assert policy["source"] == "owner_setting_unreadable" and policy["enabled"] is False
    finally:
        for rec in list(ctl._processes.values()):
            rec.proc.terminate()
            rec.proc.wait(timeout=5)
            ctl._forget(rec)


async def test_deny_while_start_waits_on_process_lock_is_rechecked_at_effect(env, apps_root):  # noqa: F811
    make_app(apps_root, "fake-app")
    await env.client.put("/api/apps/control/policy", json={"enabled": True})
    async with ctl._lock:
        pending = asyncio.create_task(env.client.post("/api/apps/fake-app/start"))
        await asyncio.sleep(0.05)
        assert not pending.done()
        await env.client.put("/api/apps/control/policy", json={"enabled": False})
    try:
        response = await pending
        assert response.status_code == 403, response.text
        assert "fake-app" not in ctl._processes
    finally:
        for rec in list(ctl._processes.values()):
            rec.proc.terminate()
            rec.proc.wait(timeout=5)
            ctl._forget(rec)
