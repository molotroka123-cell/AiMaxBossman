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


def test_evolution_status_and_report_are_read_off_the_event_loop(tmp_path, monkeypatch):
    # status()/report() read JSON, psutil and lease files synchronously; the dashboard polls them,
    # so on the event loop every poll stalled all other requests. Same payloads, same 404/503.
    def off_loop() -> bool:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return True
        return False

    seen = {}

    def fake_status(work):
        seen["status"] = off_loop()
        return {"status": "RUNNING", "loop_running": True, "campaign": str(work)}

    def fake_report(work):
        seen["report"] = off_loop()
        return {"verdict": "ok", "campaign": str(work)}

    monkeypatch.setattr(evolution, "_loop", lambda: SimpleNamespace(status=fake_status, report=fake_report))
    app = FastAPI()
    app.state.svc = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path / "data"))
    app.include_router(evolution.router, prefix="/api")
    work = (tmp_path / "data").resolve() / "evolution" / "campaign"
    with TestClient(app) as client:
        assert client.get("/api/evolution/report").status_code == 404   # no campaign yet
        status = client.get("/api/evolution/status")
        assert status.status_code == 200, status.text
        assert status.json() == {"status": "RUNNING", "loop_running": True, "campaign": str(work)}
        (work / "loop-state.json").write_text("{}", encoding="utf-8")
        report = client.get("/api/evolution/report")
        assert report.status_code == 200, report.text
        assert report.json() == {"verdict": "ok", "campaign": str(work)}
        assert seen == {"status": True, "report": True}

        def missing():
            raise evolution.HTTPException(503, {"code": "EVOLUTION_RUNTIME_MISSING", "message": "x"})
        monkeypatch.setattr(evolution, "_loop", missing)
        for path in ("/api/evolution/status", "/api/evolution/report"):
            gone = client.get(path)
            assert gone.status_code == 503 and gone.json()["detail"]["code"] == "EVOLUTION_RUNTIME_MISSING"


def _zone_app(tmp_path, monkeypatch, created):
    from bcc.features import coding_tasks
    bossman = _bossman_checkout(tmp_path / "bm")
    (bossman / "command-center" / "tests").mkdir(parents=True)
    (bossman / "command-center" / "tests" / "test_new_feature.py").write_text("def test_x(): pass\n", encoding="utf-8")
    app = _scan_app(tmp_path, monkeypatch, [bossman])
    seed = {"schema_version": "1.0", "nodes": [
        {"id": "bossman", "label": "Bossman", "parent": "", "status": "mixed", "sources": []},
        {"id": "fam", "label": "Семья", "parent": "bossman", "status": "mixed", "sources": []},
        {"id": "leaf", "label": "Новая функция", "parent": "fam", "status": "code", "detail": "d",
         "sources": [{"path": "command-center/bcc/features/new_feature.py"}]},
        {"id": "elsewhere", "label": "Другая ветка", "parent": "fam", "status": "branch",
         "sources": [{"path": "command-center/bcc/features/not_here.py"}]}]}
    (tmp_path / "seed.json").write_text(json.dumps(seed), encoding="utf-8")

    async def fake_create(body, _request):
        created.append(body)
        return {"id": f"task{len(created):08d}", "status": "running"}
    monkeypatch.setattr(coding_tasks, "create_task", fake_create)

    async def emit(*_a, **_kw):
        return None
    app.state.svc.bus = SimpleNamespace(emit=emit)
    return app, bossman


def test_zone_work_starts_one_scoped_coding_task_and_reports(tmp_path, monkeypatch):
    created = []
    app, _ = _zone_app(tmp_path, monkeypatch, created)
    with TestClient(app) as client:
        out = client.post("/api/capability-tree/work", json={"node_id": "leaf", "instruction": "проверь граничный случай"})
        assert out.status_code == 200, out.text
        body = created[0]
        assert body.allowed_paths == ["command-center/bcc/features/new_feature.py", "command-center/tests"]
        assert body.verify_tests == ["command-center/tests/test_new_feature.py"]
        assert "Новая функция" in body.instruction and "проверь граничный случай" in body.instruction
        again = client.post("/api/capability-tree/work", json={"node_id": "leaf"})
        assert again.status_code == 409
        reports = client.get("/api/capability-tree/reports?after=0").json()["items"]
        assert [r["status"] for r in reports] == ["started"]
        assert client.get("/api/capability-tree").json()["work"][0]["task_id"] == out.json()["job"]["task_id"]


def test_zone_work_passes_the_chosen_cloud_worker_and_local_means_default(tmp_path, monkeypatch):
    created = []
    app, _ = _zone_app(tmp_path, monkeypatch, created)
    with TestClient(app) as client:
        assert client.post("/api/capability-tree/work", json={"node_id": "leaf", "worker": "evil"}).status_code == 422
        out = client.post("/api/capability-tree/work", json={"node_id": "leaf", "worker": "openrouter-free"})
        assert out.status_code == 200 and created[-1].worker == "openrouter-free"
        assert out.json()["job"]["worker"] == "openrouter-free" and "free" in out.json()["job"]["worker_label"]
        workers = client.get("/api/capability-tree").json()["workers"]
        assert workers[0]["id"] == "local" and {"openrouter-free", "nvidia-nim", "glm-flash"} <= {w["id"] for w in workers}
    created.clear()
    app2, _ = _zone_app(tmp_path / "second", monkeypatch, created)
    with TestClient(app2) as client:
        assert client.post("/api/capability-tree/work", json={"node_id": "leaf", "worker": "local"}).status_code == 200
    assert created[-1].worker is None


def test_lite_view_omits_the_seed_but_keeps_live_state(tmp_path, monkeypatch):
    created = []
    app, _ = _zone_app(tmp_path, monkeypatch, created)
    with TestClient(app) as client:
        client.post("/api/capability-tree/work", json={"node_id": "leaf"})
        lite = client.get("/api/capability-tree?lite=1").json()
    assert set(lite) == {"activity", "work"} and lite["work"][0]["node_id"] == "leaf"


def test_zone_without_files_in_this_build_is_refused(tmp_path, monkeypatch):
    created = []
    app, _ = _zone_app(tmp_path, monkeypatch, created)
    with TestClient(app) as client:
        out = client.post("/api/capability-tree/work", json={"node_id": "elsewhere"})
        assert out.status_code == 422 and out.json()["detail"]["code"] == "CAPABILITY_ZONE_HAS_NO_SOURCES"
        assert created == []


def test_zone_completion_is_reported_once_with_the_independent_check(tmp_path, monkeypatch):
    from bcc.features import coding_tasks
    created = []
    app, _ = _zone_app(tmp_path, monkeypatch, created)
    svc = app.state.svc
    with TestClient(app) as client:
        task_id = client.post("/api/capability-tree/work", json={"node_id": "leaf"}).json()["job"]["task_id"]
    monkeypatch.setattr(coding_tasks, "_read", lambda _svc, tid: {
        "id": tid, "status": "completed", "changed_files": ["command-center/bcc/features/new_feature.py"],
        "verification": {"ran": True, "passed": True}})
    tree._sync_zone_work(svc)
    tree._sync_zone_work(svc)  # no duplicate report for an unchanged status
    with TestClient(app) as client:
        reports = client.get("/api/capability-tree/reports?after=1").json()["items"]
    assert len(reports) == 1 and reports[0]["status"] == "completed" and reports[0]["task_id"] == task_id
    assert "независимая проверка Bossman: пройдена" in reports[0]["text"]
    assert "после вашего подтверждения" in reports[0]["text"]


def test_leaf_earns_verified_then_applied_only_through_the_owner_apply(tmp_path, monkeypatch):
    from bcc.features import coding_tasks
    created = []
    app, _ = _zone_app(tmp_path, monkeypatch, created)
    svc = app.state.svc
    with TestClient(app) as client:
        task_id = client.post("/api/capability-tree/work", json={"node_id": "leaf"}).json()["job"]["task_id"]
    rec = {"id": task_id, "status": "completed", "changed_files": ["a.py"], "verification": {"ran": True, "passed": True}}
    monkeypatch.setattr(coding_tasks, "_read", lambda _svc, _tid: dict(rec))
    tree._sync_zone_work(svc)
    with TestClient(app) as client:
        job = client.get("/api/capability-tree?lite=1").json()["work"][0]
    assert job["earned"] == "verified"
    rec["applied_at"] = 123.0  # the owner pressed Apply in Coding
    tree._sync_zone_work(svc)
    tree._sync_zone_work(svc)
    with TestClient(app) as client:
        job = client.get("/api/capability-tree?lite=1").json()["work"][0]
        reports = client.get("/api/capability-tree/reports?after=0").json()["items"]
    assert job["earned"] == "applied"
    assert [r["status"] for r in reports].count("applied") == 1


def test_failed_independent_check_earns_nothing():
    assert tree._earned({"status": "completed", "changed_files": ["a.py"], "verification": {"passed": False}}) is None
    assert tree._earned({"status": "completed", "changed_files": [], "verification": {"passed": True}}) is None
    assert tree._earned({"status": "completed", "changed_files": ["a.py"]}) == "unverified"
    assert tree._earned({"status": "failed", "changed_files": ["a.py"]}) is None


def test_full_app_mounts_capability_tree(tmp_path):
    app = create_app(make_settings(tmp_path), start_workers=False, announce_token=False)
    paths = set(app.openapi()["paths"])
    assert "/api/capability-tree" in paths
    assert "/api/capability-tree/note" in paths
    assert "/api/capability-tree/scan" in paths


def _zone_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "zone"
    feats, tests = repo / "command-center" / "bcc" / "features", repo / "command-center" / "tests"
    feats.mkdir(parents=True)
    tests.mkdir(parents=True)
    for stem in ("plugins", "missions", "rave"):
        (feats / f"{stem}.py").write_text("X = 1\n", encoding="utf-8")
    (tests / "test_plugins_adapter.py").write_text("from bcc.features import plugins\n", encoding="utf-8")
    (tests / "test_plugin_security.py").write_text("from bcc.features import (\n    plugins,\n)\n", encoding="utf-8")
    (tests / "test_feat_missions.py").write_text("import bcc.features.missions as m\n", encoding="utf-8")
    (tests / "test_rave_a.py").write_text("", encoding="utf-8")
    (tests / "test_rave_b.py").write_text("", encoding="utf-8")
    (tests / "test_browser_sweep.py").write_text("from playwright.sync_api import sync_playwright\n"
                                                 "from bcc.features import missions\n", encoding="utf-8")
    (tests / "test_unrelated.py").write_text("from bcc.features import pluginsx, rave_cli\n", encoding="utf-8")
    for i in range(tree._ZONE_FILES_MAX + 3):
        skill = repo / ".agents" / "skills" / f"s{i}"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("# s\n", encoding="utf-8")
    return repo


def test_zone_scope_keeps_runtime_code_and_finds_importing_tests(tmp_path):
    # Owner-run prep 2026-10-05: the skills zone held 48 SKILL.md texts, no runtime code and no tests,
    # and the plugins zone missed test_plugin_security (named after plugin_security, imports plugins).
    repo = _zone_repo(tmp_path)
    md = [{"id": f"s{i}", "parent": "z", "sources": [{"path": f".agents/skills/s{i}/SKILL.md"}]}
          for i in range(tree._ZONE_FILES_MAX + 3)]
    code = [{"id": stem, "parent": "z", "sources": [{"path": f"command-center/bcc/features/{stem}.py"}]}
            for stem in ("plugins", "missions", "rave")]
    seed = {"nodes": [{"id": "z", "sources": []}, *md, *code]}
    files, dirs, verify = tree._zone_scope(seed, seed["nodes"][0], repo)
    assert len(files) == tree._ZONE_FILES_MAX
    assert files[:3] == [f"command-center/bcc/features/{s}.py" for s in ("plugins", "missions", "rave")]
    assert dirs == ["command-center/tests"]
    names = [Path(v).name for v in verify]
    # own tests of every module first (round-robin), then tests that merely import a module
    assert names == ["test_plugins_adapter.py", "test_rave_a.py", "test_rave_b.py",
                     "test_plugin_security.py", "test_feat_missions.py"]
    # negative controls: a browser suite and a look-alike module name are never a zone check
    assert "test_browser_sweep.py" not in names and "test_unrelated.py" not in names


def test_zone_scope_caps_verification(tmp_path):
    repo = _zone_repo(tmp_path)
    tests = repo / "command-center" / "tests"
    for i in range(tree._ZONE_VERIFY_MAX + 5):
        (tests / f"test_plugins_{i:02d}.py").write_text("", encoding="utf-8")
    seed = {"nodes": [{"id": "z", "sources": [{"path": "command-center/bcc/features/plugins.py"}]}]}
    _, _, verify = tree._zone_scope(seed, seed["nodes"][0], repo)
    assert len(verify) == tree._ZONE_VERIFY_MAX


def test_working_status_is_served_and_owner_notes_cannot_set_it(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, [
        {"id": "bossman", "label": "Bossman", "parent": "", "status": "mixed", "sources": []},
        {"id": "leaf", "label": "Leaf", "parent": "bossman", "status": "reported", "sources": []},
        {"id": "inst", "label": "Inst", "parent": "bossman", "status": "working", "sources": []},
    ], {"campaign_id": "test", "status": "RUNNING"})
    with TestClient(app) as client:
        for body in ({"node_id": "leaf", "text": "x", "state": "working"},
                     {"node_id": "leaf", "text": "x", "state": "note", "status": "working"}):
            client.post("/api/capability-tree/note", json=body)
        nodes = {n["id"]: n["status"] for n in client.get("/api/capability-tree").json()["tree"]["nodes"]}
        assert nodes == {"bossman": "mixed", "leaf": "reported", "inst": "working"}
        assert client.post("/api/capability-tree/note",
                           json={"node_id": "leaf", "text": "x", "state": "working-installed"}).status_code == 422


def test_ui_declares_working_status_with_distinct_color():
    import re
    ui = Path(tree.__file__).resolve().parents[2] / "ui" / "pages"
    js = (ui / "capability_tree.js").read_text(encoding="utf-8")
    scene = (ui / "capability_tree_scene.js").read_text(encoding="utf-8")
    m = re.search(r"working:\s*\['Работает в установленном Bossman',\s*'ok',\s*'(#[0-9a-fA-F]{6})'\]", js)
    assert m, "working status missing from STATUS"
    color = m.group(1).lower()
    other = {c.lower() for c in re.findall(r"'(#[0-9a-fA-F]{6})'\]", js) if c.lower() != color}
    other |= {c.lower() for c in re.findall(r"#[0-9a-fA-F]{6}", scene)}
    assert color not in other
    assert "const LEGEND = ['working', 'reported'" in js
