"""`exchange_root` from the request body must not let a caller write task files into any folder."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pc_autopilot_mini.api import build_app


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("BOSSMAN_APPS_DATA", str(tmp_path / "data"))
    (tmp_path / "cwd").mkdir()
    monkeypatch.chdir(tmp_path / "cwd")          # a relative root must never land next to the server
    base = tmp_path / "exchange"
    monkeypatch.setenv("BOSSMAN_APP_EXCHANGE_ROOT", str(base))
    c = TestClient(build_app())
    m = c.post("/api/macros", json={"name": "m", "steps": [{"action": "wait", "seconds": 0}]}).json()
    return c, f"/api/macros/{m['id']}/repair", base, tmp_path


@pytest.mark.parametrize("make", [lambda base, tmp: str(tmp / "outside"),
                                  lambda base, tmp: str(base / ".." / "outside"),
                                  lambda base, tmp: "../outside"])
def test_exchange_root_outside_the_exchange_is_refused(env, make):
    c, url, base, tmp = env
    r = c.post(url, json={"failed_step": 0, "proposal": {"action": "wait", "seconds": 0}, "exchange_root": make(base, tmp)})
    assert r.status_code == 400, r.text
    assert not (tmp / "outside").exists()
    assert not list(tmp.rglob("*.json"))


def test_exchange_root_inside_the_exchange_still_emits(env):
    c, url, base, _tmp = env
    for root in (str(base), "sub"):
        r = c.post(url, json={"failed_step": 0, "proposal": {"action": "wait", "seconds": 0}, "exchange_root": root})
        assert r.status_code == 200, r.text
        p = Path(r.json()["task_file"]).resolve()
        assert base.resolve() in p.parents and p.parent.name == "inbox"
