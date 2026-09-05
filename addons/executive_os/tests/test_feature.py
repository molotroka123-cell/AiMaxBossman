"""Real BCC auth/discovery plus a controlled Runtime boundary, no provider calls."""
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from bossman_os.bcc_app import create


class ControlledRuntime:
    def __init__(self, state_root, artifact_root):
        self.state_root = state_root
        self.artifact_root = artifact_root
        self.calls = []

    def status(self):
        return {"managed_missions": 0}

    def submit(self, payload):
        self.calls.append(("submit", payload))
        return {"id": payload["id"], "status": "planned"}

    def run(self, ident):
        self.calls.append(("run", ident))
        if ident == "denied":
            raise PermissionError("private file path must not leak")
        if ident == "invalid":
            raise ValueError("private file path must not leak")
        if ident == "conflict":
            raise RuntimeError("private file path must not leak")
        if ident == "io-failure":
            raise OSError("private file path must not leak")
        return {"id": ident, "status": "controlled-fixture"}

    def snapshot(self, ident):
        return {"id": ident, "status": "controlled-fixture"}

    def recover(self, ident):
        self.calls.append(("recover", ident))
        return {"id": ident, "status": "unknown"}

    def evaluate(self, payload):
        self.calls.append(("evaluate", payload))
        return {"status": "controlled-fixture"}

    async def propose(self, payload):
        self.calls.append(("propose", payload))
        return {"proposal_only": True}


@pytest_asyncio.fixture
async def sidecar(tmp_path):
    app = create(data_dir=tmp_path / "bcc", runtime_factory=ControlledRuntime)
    svc = app.state.svc
    await svc.start()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            yield app, client, {"X-BCC-Token": svc.auth.token}
    finally:
        await svc.stop()


@pytest.mark.asyncio
async def test_real_bcc_auth_applies_to_extension_and_dispatch(sidecar):
    app, client, auth = sidecar
    assert (await client.get("/api/executive-os/status")).status_code == 401
    response = await client.get("/api/executive-os/status", headers=auth)
    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert response.json()["existing_bcc_tasks"] == "unmanaged"
    assert app.state.svc.start_workers is False
    payload = {"id": "m1", "steps": [{"id": "write", "action": "artifact.write",
                                      "path": "result.txt", "content": "verified later"}]}
    assert (await client.post("/api/executive-os/missions", json=payload)).status_code == 401
    response = await client.post("/api/executive-os/missions", headers=auth, json=payload)
    assert response.status_code == 201
    assert response.json()["id"] == "m1"
    assert (await client.post("/api/executive-os/missions/m1/run", headers=auth)).status_code == 200
    assert (await client.get("/api/executive-os/missions/m1", headers=auth)).status_code == 200
    assert (await client.post("/api/executive-os/recover", headers=auth, json={"id": "m1"})).json()["status"] == "unknown"
    assert (await client.post("/api/executive-os/propose", headers=auth,
                              json={"objective": "prepare report"})).json()["proposal_only"] is True
    assert (await client.post("/api/executive-os/evaluate", headers=auth,
                              json={"suite_id": "s1", "phase": "baseline", "cases": {"one": "m1"}})).status_code == 200


@pytest.mark.asyncio
async def test_http_cannot_grant_capabilities_or_assert_success(sidecar):
    app, client, auth = sidecar
    payload = {"id": "m1", "steps": [{"id": "write", "action": "artifact.write",
                                      "path": "result.txt", "content": "data"}],
               "capabilities": ["terminal.run"], "approved": True}
    assert (await client.post("/api/executive-os/missions", headers=auth, json=payload)).status_code == 422
    assert (await client.post("/api/executive-os/evaluate", headers=auth,
                              json={"suite_id": "s1", "phase": "candidate", "cases": {"one": "m1"},
                                    "success": True})).status_code == 422
    assert app.state.svc.executive_os.calls == []


@pytest.mark.asyncio
async def test_public_console_has_no_token_and_api_still_requires_auth(sidecar):
    app, client, _ = sidecar
    page = await client.get("/executive-os")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert 'type="password"' in page.text
    assert app.state.svc.auth.token not in page.text
    assert "localStorage" not in page.text and "sessionStorage" not in page.text
    assert "innerHTML" not in page.text
    assert "X-BCC-Token" in page.text
    assert (await client.get("/api/executive-os/status")).status_code == 401


@pytest.mark.asyncio
async def test_runtime_errors_are_mapped_without_private_details(sidecar):
    _, client, auth = sidecar
    for ident, expected in (("denied", 403), ("invalid", 400), ("conflict", 409), ("io-failure", 409)):
        response = await client.post(f"/api/executive-os/missions/{ident}/run", headers=auth)
        assert response.status_code == expected
        assert "private file path" not in response.text


@pytest.mark.asyncio
async def test_discovery_is_scoped_and_sidecar_ignores_inherited_database(tmp_path, monkeypatch):
    import bcc.features
    from bcc.api import create_app
    from bcc.config import Settings

    original_paths = list(bcc.features.__path__)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unintended-production-database")
    app = create(data_dir=tmp_path / "isolated", runtime_factory=ControlledRuntime)
    assert list(bcc.features.__path__) == original_paths
    assert app.state.svc.settings.database_url.startswith("sqlite+aiosqlite:///")
    assert str(tmp_path / "isolated") in app.state.svc.settings.database_url
    plain_dir = tmp_path / "plain"
    plain = create_app(Settings(data_dir=plain_dir,
                                database_url=f"sqlite+aiosqlite:///{plain_dir / 'bcc.db'}",
                                ui_dir=tmp_path / "no-ui"),
                       start_workers=False, announce_token=False)
    assert not any(f.name == "executive_os" for f in plain.state.svc.features)
    assert any(f.name == "executive_os" for f in app.state.svc.features)
    await app.state.svc.stop()
    await plain.state.svc.stop()


@pytest_asyncio.fixture
async def real_sidecar(tmp_path):
    app = create(data_dir=tmp_path / "real-bcc")
    svc = app.state.svc
    await svc.start()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            yield app, client, {"X-BCC-Token": svc.auth.token}
    finally:
        await svc.stop()


def _mission(ident, content="Verified UTF-8: Привет\nSecond line\n"):
    return {"id": ident, "project": "api-tests", "steps": [
        {"id": "write", "action": "artifact.write", "path": "report.txt", "content": content},
        {"id": "verify", "depends_on": ["write"], "action": "artifact.verify",
         "path": "report.txt", "content": content}]}


@pytest.mark.asyncio
async def test_actual_runtime_authenticated_api_writes_and_verifies_file(real_sidecar):
    app, client, auth = real_sidecar
    mission = _mission("real-mission")
    assert (await client.post("/api/executive-os/missions", json=mission)).status_code == 401
    assert (await client.post("/api/executive-os/missions", headers=auth, json=mission)).status_code == 201
    run = await client.post("/api/executive-os/missions/real-mission/run", headers=auth)
    assert run.status_code == 200, run.text
    snapshot = await client.get("/api/executive-os/missions/real-mission", headers=auth)
    assert snapshot.status_code == 200 and snapshot.json()["done"] is True
    assert set(snapshot.json()["verified_now"]) == {"write", "verify"}
    target = app.state.svc.executive_os.artifact_root / "real-mission/report.txt"
    assert target.read_bytes() == mission["steps"][0]["content"].encode("utf-8")


@pytest.mark.asyncio
async def test_actual_runtime_http_rejects_escape_and_utf8_byte_overflow(real_sidecar):
    app, client, auth = real_sidecar
    mission = _mission("escape")
    mission["steps"][0]["path"] = "../../outside.txt"
    response = await client.post("/api/executive-os/missions", headers=auth, json=mission)
    assert response.status_code == 400
    assert not (app.state.svc.settings.data_dir / "outside.txt").exists()
    assert not (app.state.svc.executive_os.artifact_root / "escape").exists()
    # Characters fit the HTTP schema; host must independently enforce UTF-8 bytes.
    response = await client.post("/api/executive-os/missions", headers=auth,
                                 json=_mission("oversized", "é" * 40000))
    assert response.status_code == 400
    assert not (app.state.svc.executive_os.artifact_root / "oversized").exists()


@pytest.mark.asyncio
async def test_actual_runtime_reopen_and_fresh_tamper_detection(tmp_path):
    directory = tmp_path / "persistent-bcc"
    first = create(data_dir=directory)
    await first.state.svc.start()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=first),
                                     base_url="http://test", headers={"X-BCC-Token": first.state.svc.auth.token}) as client:
            assert (await client.post("/api/executive-os/missions", json=_mission("durable"))).status_code == 201
            run = await client.post("/api/executive-os/missions/durable/run")
            assert run.status_code == 200 and run.json()["done"] is True, run.text
    finally:
        await first.state.svc.stop()
    second = create(data_dir=directory)
    await second.state.svc.start()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=second),
                                     base_url="http://test", headers={"X-BCC-Token": second.state.svc.auth.token}) as client:
            snapshot = await client.get("/api/executive-os/missions/durable")
            assert snapshot.status_code == 200 and snapshot.json()["done"] is True
            target = second.state.svc.executive_os.artifact_root / "durable/report.txt"
            target.write_bytes(b"changed after verification")
            assert (await client.get("/api/executive-os/missions/durable")).json()["done"] is False
            recovered = await client.post("/api/executive-os/recover", json={"id": "durable"})
            assert recovered.status_code == 200 and recovered.json()["done"] is False
            assert target.read_bytes() == b"changed after verification"
    finally:
        await second.state.svc.stop()
