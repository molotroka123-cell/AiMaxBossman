import pytest
from fastapi.testclient import TestClient
from pc_autopilot_mini.api import build_app


@pytest.mark.parametrize("action", ["file.copy", "file.move"])
@pytest.mark.parametrize("into_dir", [False, True])
def test_approved_copy_or_move_never_overwrites_an_existing_file(tmp_path, monkeypatch, action, into_dir):
    """An approved macro must fail, not silently replace a file that already exists at the destination."""
    monkeypatch.setenv("BOSSMAN_APPS_DATA", str(tmp_path / "data"))
    src = tmp_path / "src.txt"; src.write_text("NEW")
    folder = tmp_path / "out"; folder.mkdir()
    existing = folder / "src.txt" if into_dir else folder / "dst.txt"
    existing.write_text("PRECIOUS")
    c = TestClient(build_app())
    m = c.post("/api/macros", json={"name": "m", "steps": [{"action": action, "src": str(src), "dst": str(folder if into_dir else existing)}]}).json()
    r = c.post(f"/api/macros/{m['id']}/run", json={"approve": True}).json()
    assert r["status"] == "FAILED" and "overwrite" in r["error"]
    assert existing.read_text() == "PRECIOUS" and src.read_text() == "NEW"
    fresh = folder / "fresh.txt"                       # a free destination still works
    m2 = c.post("/api/macros", json={"name": "ok", "steps": [{"action": action, "src": str(src), "dst": str(fresh)}]}).json()
    assert c.post(f"/api/macros/{m2['id']}/run", json={"approve": True}).json()["status"] == "COMPLETED"
    assert fresh.read_text() == "NEW"
