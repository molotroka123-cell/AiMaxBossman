from __future__ import annotations

import asyncio
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
    repo.mkdir(parents=True)
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


def test_previous_scan_of_another_repo_is_not_a_baseline(tmp_path):
    # Live owner run: the UI scanned a non-Bossman root; a later Bossman scan must not call every file "new".
    seed = {"schema_version": "1.0", "nodes": [{"id": "bossman", "sources": []}]}
    other = tree.scan_repository(_repo(tmp_path / "other"), seed)
    again = tree.scan_repository(_repo(tmp_path / "bossman"), seed, other)
    assert again["baseline_created"] is True
    assert again["new_since_previous"] == []


def _bossman_checkout(root: Path) -> Path:
    repo = _repo(root)
    (repo / "bossman-core").mkdir()
    return repo


def _scan_app(tmp_path: Path, monkeypatch, roots: list[Path]) -> FastAPI:
    app = _app(tmp_path, monkeypatch, [{"id": "bossman", "label": "Bossman", "parent": "", "status": "code",
                                        "sources": []}], {"status": "NO_CAMPAIGN", "cycle": {}})

    async def fake_roots(_svc):
        return [r.resolve() for r in roots]
    monkeypatch.setattr(tree, "allowed_roots", fake_roots)
    monkeypatch.setattr(evolution, "allowed_roots", fake_roots)
    return app


def test_ui_scan_picks_the_only_bossman_checkout_among_code_roots(tmp_path, monkeypatch):
    game = _repo(tmp_path / "game")  # another Git root, e.g. a project the owner codes on
    bossman = _bossman_checkout(tmp_path / "bm")
    with TestClient(_scan_app(tmp_path, monkeypatch, [game, bossman])) as client:
        out = client.post("/api/capability-tree/scan", json={})
        assert out.status_code == 200, out.text
        assert out.json()["repo"] == str(bossman.resolve())


def test_ui_scan_refuses_a_non_bossman_repo_and_lists_candidates(tmp_path, monkeypatch):
    game = _repo(tmp_path / "game")
    first, second = _bossman_checkout(tmp_path / "a"), _bossman_checkout(tmp_path / "b")
    with TestClient(_scan_app(tmp_path, monkeypatch, [game])) as client:
        none = client.post("/api/capability-tree/scan", json={})
        assert none.status_code == 422 and none.json()["detail"]["code"] == "CAPABILITY_SCAN_SOURCE_REQUIRED"
        explicit = client.post("/api/capability-tree/scan", json={"source_repo": str(game)})
        assert explicit.status_code == 422 and explicit.json()["detail"]["code"] == "CAPABILITY_SCAN_NOT_BOSSMAN"
    with TestClient(_scan_app(tmp_path, monkeypatch, [game, first, second])) as client:
        many = client.post("/api/capability-tree/scan", json={})
        assert many.status_code == 422
        assert many.json()["detail"]["candidates"] == [str(first.resolve()), str(second.resolve())]
        chosen = client.post("/api/capability-tree/scan", json={"source_repo": str(second)})
        assert chosen.status_code == 200 and chosen.json()["repo"] == str(second.resolve())


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


def _state(work: Path, campaign_id: str) -> None:
    work.mkdir(parents=True, exist_ok=True)
    (work / "loop-state.json").write_text(json.dumps({
        "campaign_id": campaign_id, "status": "COMPLETED", "config": {"backend": "bossman_coding", "max_cycles": 2},
        "cycles": [{"id": "c001-x", "index": 1, "task_id": "fix-thing", "phase": "VERIFY", "status": "CLOSED"}],
        "current": "c001-x"}), encoding="utf-8")


def test_loop_status_exposes_campaign_id_for_once_per_campaign_auto_open(tmp_path):
    # The UI keys its one-time auto-open by campaign_id; without it every campaign shared one key.
    loop = evolution._loop()
    _state(tmp_path / "a", "campaign-a")
    _state(tmp_path / "b", "campaign-b")
    assert loop.status(tmp_path / "a")["campaign_id"] == "campaign-a"
    assert loop.status(tmp_path / "b")["campaign_id"] == "campaign-b"


def test_activity_follows_the_v15_owner_run_campaign(tmp_path):
    # «Запустить 1.5» navigates to the tree; the tree must show that campaign, not NO_CAMPAIGN.
    data = tmp_path / "data"
    _state(data / "v1.5" / "owner-run" / "self-improve" / "evolution", "v15-campaign")
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=data))
    activity = tree._activity(svc)
    assert activity["campaign_source"] == "v15_owner_run"
    assert activity["campaign"]["campaign_id"] == "v15-campaign"
    assert activity["campaign"]["cycle"]["task"] == "fix-thing"
    _state(data / "evolution" / "campaign", "evo-campaign")
    activity = tree._activity(svc)
    assert activity["campaign_source"] == "evolution"
    assert activity["campaign"]["campaign_id"] == "evo-campaign"


def test_tick_does_not_rewrite_activity_for_volatile_metrics(tmp_path, monkeypatch):
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps({"schema_version": "1.0", "nodes": [
        {"id": "bossman", "label": "Bossman", "parent": "", "status": "blocked", "sources": []}]}), encoding="utf-8")
    monkeypatch.setattr(tree, "PACKAGE_MAP", seed_path)
    ram = iter(range(1000, 1010))
    monkeypatch.setattr(evolution, "_view", lambda _svc: {
        "status": "RUNNING", "loop_running": True, "campaign_id": "c", "free_ram_mb": next(ram),
        "lease": {"heartbeat_at": str(next(ram))}, "cycle": {"index": 1, "task": "t", "phase": "ATTEMPT",
                                                             "elapsed_seconds": next(ram)}})
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path / "data"))
    path = tmp_path / "data" / "evolution" / "capability-tree" / "activity-latest.json"
    asyncio.run(tree.tick(svc))
    first = path.read_text(encoding="utf-8")
    asyncio.run(tree.tick(svc))
    assert path.read_text(encoding="utf-8") == first


def test_evolution_start_tells_the_worker_which_command_center_started_it(tmp_path, monkeypatch):
    # Owner install: `bcc --port 8801`; the worker defaulted to :8800 and the campaign was BLOCKED at once.
    import bossman.apprentice.proc_tree as proc_tree
    repo = tmp_path / "repo"
    (repo / "config" / "evolution").mkdir(parents=True)
    (repo / "config" / "evolution" / "owner-v1.1.json").write_text("{}", encoding="utf-8")
    seen = {}

    class FakeTree:
        def __init__(self, argv, **_kw):
            seen["argv"] = argv
            self.pid = 4242
            self.proc = SimpleNamespace(wait=lambda: 0)

        def close(self):
            pass

    async def fake_repo(_svc, _raw):
        return repo
    monkeypatch.setattr(proc_tree, "ProcessTree", FakeTree)
    monkeypatch.setattr(evolution, "_repo", fake_repo)
    app = FastAPI()
    app.state.svc = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path / "data"))
    app.include_router(evolution.router, prefix="/api")
    with TestClient(app, base_url="http://127.0.0.1:8801") as client:
        out = client.post("/api/evolution/start", json={"cycles": 1})
        assert out.status_code == 200, out.text
    argv = seen["argv"]
    assert argv[argv.index("--api-url") + 1] == "http://127.0.0.1:8801"


def test_full_app_mounts_capability_tree(tmp_path):
    app = create_app(make_settings(tmp_path), start_workers=False, announce_token=False)
    paths = set(app.openapi()["paths"])
    assert "/api/capability-tree" in paths
    assert "/api/capability-tree/note" in paths
    assert "/api/capability-tree/scan" in paths
