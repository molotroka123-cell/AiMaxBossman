from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from bcc.features import capability_tree as tree
from bcc.features import evolution
from bcc.api import create_app
from .conftest import make_settings


def _run(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "init", "-b", "main")
    _run(repo, "config", "user.email", "test@example.invalid")
    _run(repo, "config", "user.name", "Test")
    (repo / "command-center" / "bcc" / "features").mkdir(parents=True)
    (repo / "command-center" / "bcc" / "features" / "new_feature.py").write_text(
        "router.get('/demo')\ndef build(): pass\n", encoding="utf-8")
    (repo / "apps" / "demo").mkdir(parents=True)
    (repo / "apps" / "demo" / "app.manifest.yaml").write_text("id: demo\n", encoding="utf-8")
    _run(repo, "add", ".")
    _run(repo, "commit", "-m", "seed")
    _run(repo, "remote", "add", "origin", str(repo))
    _run(repo, "fetch", "origin", "main:refs/remotes/origin/main")
    return repo


def test_deterministic_scanner_finds_unmapped_files_without_ai(tmp_path):
    repo = _repo(tmp_path)
    seed = {"schema_version": "1.0", "nodes": [{"id": "bossman", "sources": []}]}
    result = tree.scan_repository(repo, seed)
    assert result["ai_used"] is False
    assert result["branch_count"] == 1
    assert result["apps"] == ["demo"]
    assert "command-center/bcc/features/new_feature.py" in result["unmapped_capability_files"]
    again = tree.scan_repository(repo, seed, result)
    assert again["new_since_previous"] == []
    assert again["fingerprint"] == result["fingerprint"]


def _app(tmp_path: Path, monkeypatch, nodes: list[dict], campaign: dict) -> FastAPI:
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps({"schema_version": "1.0", "nodes": nodes}), encoding="utf-8")
    monkeypatch.setattr(tree, "PACKAGE_MAP", seed_path)
    monkeypatch.setattr(evolution, "_view", lambda _svc: campaign)
    app = FastAPI()
    app.state.svc = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path / "data"))
    app.include_router(tree.router, prefix="/api")
    return app


def test_tree_endpoint_preserves_evidence_status_and_saves_owner_note(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, [
        {"id": "bossman", "label": "Bossman", "parent": "", "status": "blocked", "sources": []},
        {"id": "learning", "label": "Learning", "parent": "bossman", "status": "code", "sources": []},
    ], {"campaign_id": "test", "status": "RUNNING", "loop_running": True,
        "cycle": {"index": 1, "task": "learning", "phase": "ATTEMPT"}})
    with TestClient(app) as client:
        payload = client.get("/api/capability-tree").json()
        assert payload["tree"]["nodes"][0]["status"] == "blocked"
        assert payload["activity"]["campaign"]["loop_running"] is True
        saved = client.post("/api/capability-tree/note", json={
            "node_id": "learning", "text": "проверить перенос", "state": "working",
        })
        assert saved.status_code == 200
        after = client.get("/api/capability-tree").json()
        assert after["notes"]["learning"]["text"] == "проверить перенос"
        assert after["tree"]["nodes"][1]["status"] == "code"


def test_unknown_node_cannot_create_orphan_note(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, [
        {"id": "bossman", "label": "Bossman", "parent": "", "status": "blocked", "sources": []},
    ], {"status": "NO_CAMPAIGN", "cycle": {}})
    with TestClient(app) as client:
        response = client.post("/api/capability-tree/note", json={"node_id": "missing", "text": "x"})
        assert response.status_code == 404


def test_full_app_mounts_capability_tree(tmp_path):
    app = create_app(make_settings(tmp_path), start_workers=False, announce_token=False)
    paths = set(app.openapi()["paths"])
    assert "/api/capability-tree" in paths
    assert "/api/capability-tree/note" in paths
    assert "/api/capability-tree/scan" in paths
