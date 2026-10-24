from fastapi.testclient import TestClient

from pc_autopilot_mini.api import build_app


def test_macro_draft_and_dry_run_never_execute_steps(tmp_path, monkeypatch):
    monkeypatch.setenv("BOSSMAN_APPS_DATA", str(tmp_path))
    client = TestClient(build_app())
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="create-macro"' in page.text
    assert 'id="dry-run"' in page.text
    assert '"/run"' not in page.text
    assert "не запускает макрос на компьютере" in page.text

    macro = client.post("/api/macros", json={
        "name": "Synthetic wait draft", "steps": [{"action": "wait", "seconds": 1}],
    }).json()
    preview = client.post(f"/api/macros/{macro['id']}/dry-run", json={"variables": {}}).json()
    assert preview["plan"] == [{"step": {"action": "wait", "seconds": 1}, "authority": "LOCAL"}]
    assert preview["requires_bossman"] is False
    assert client.get("/api/macros").json()["macros"][0]["status"] == "draft"
