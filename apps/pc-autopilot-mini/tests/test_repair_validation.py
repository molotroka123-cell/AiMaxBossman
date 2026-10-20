from fastapi.testclient import TestClient
from pc_autopilot_mini.api import build_app


def _macro(c, tmp_path):
    return c.post("/api/macros", json={"name": "m", "steps": [{"action": "file.mkdir", "path": str(tmp_path / "d")}]}).json()


def test_repair_cannot_smuggle_denied_or_malformed_step(tmp_path, monkeypatch):
    """The repair path must pass the same step validation as create/version: no DENY action, no step without action."""
    monkeypatch.setenv("BOSSMAN_APPS_DATA", str(tmp_path / "data"))
    c = TestClient(build_app())
    m = _macro(c, tmp_path)
    for bad in ({"action": "payment", "amount": 999}, {"foo": 1}):
        r = c.post(f"/api/macros/{m['id']}/repair", json={"failed_step": 0, "proposal": bad}).json()
        resp = c.post(f"/api/repairs/{r['id']}/apply", json={"approve": True})
        assert resp.status_code == 409, resp.text
        cur = c.get(f"/api/macros/{m['id']}").json()
        assert cur["version"] == 1 and cur["steps"][0]["action"] == "file.mkdir"
        assert c.post(f"/api/macros/{m['id']}/dry-run", json={}).status_code == 200
    ok = c.post(f"/api/macros/{m['id']}/repair", json={"failed_step": 0, "proposal": {"action": "wait", "seconds": 0}}).json()
    assert c.post(f"/api/repairs/{ok['id']}/apply", json={"approve": True}).json()["status"] == "APPLIED"
    assert c.get(f"/api/macros/{m['id']}").json()["version"] == 2
