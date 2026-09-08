"""Actual file effects, refusal, restart and interrupted-batch evidence.

Existing HTTP tests now explicitly authenticate because unauthenticated host
filesystem access was a product defect. Their original assertions are retained.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import hmac
import hashlib

from fastapi.testclient import TestClient
import pytest

from file_commander_mini.api import build_app
from file_commander_mini.domain import FileCommander
from file_commander_mini.store import SQLiteStore


@pytest.fixture
def files(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setenv("BOSSMAN_APPS_DATA", str(tmp_path / "state"))
    monkeypatch.setenv("FILE_COMMANDER_ROOTS", str(root))
    monkeypatch.setenv("BOSSMAN_APP_TOKEN", "file-commander-test-authorization-0001")
    store = SQLiteStore("file-commander-mini")
    return root, FileCommander(store)


def client():
    return TestClient(build_app(), headers={"X-Bossman-App-Token": os.environ["BOSSMAN_APP_TOKEN"]})


def test_no_roots_fails_closed_and_api_requires_auth(files, monkeypatch):
    root, eng = files
    (root / "report.pdf").write_text("owner file")
    monkeypatch.delenv("FILE_COMMANDER_ROOTS")
    c = client()
    assert c.post("/api/files/scan", json={"root": str(root)}).status_code == 403
    assert TestClient(build_app()).post("/api/files/scan", json={"root": str(root)}).status_code == 401
    assert TestClient(build_app()).get("/api/roots").status_code == 401
    assert (root / "report.pdf").read_text() == "owner file"


@pytest.mark.parametrize("name", [".env", ".env.production", "secrets/key.txt", ".ssh/id_rsa", "token", "private.pem"])
def test_secret_paths_remain_denied_under_an_allowed_root(files, name):
    root, eng = files
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("sensitive")
    with pytest.raises(PermissionError): eng.allowed(path)
    assert str(path) not in {item["path"] for item in eng.scan(str(root))["files"]}


def test_system_state_and_repository_denied_even_with_broad_root(files, monkeypatch):
    root, eng = files
    repo = root / "repository"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: external")
    (repo / "readme.txt").write_text("protected source")
    with pytest.raises(PermissionError): eng.allowed(repo / "readme.txt")
    with pytest.raises(PermissionError): eng.allowed(eng.s.db_path)
    if os.name == "posix":
        monkeypatch.setenv("FILE_COMMANDER_ROOTS", "/")
        with pytest.raises(PermissionError): eng.allowed(Path("/etc/hosts"))


def test_traversal_symlink_and_destination_link_refused(files):
    root, eng = files
    (root / "file.pdf").write_text("real")
    with pytest.raises(PermissionError): eng.allowed(root / "child" / ".." / "file.pdf")
    link = root / "linked"
    try: link.symlink_to(root.parent, target_is_directory=True)
    except OSError: pytest.skip("host does not permit creating symlinks")
    with pytest.raises(PermissionError): eng.allowed(link / "elsewhere.pdf")
    with pytest.raises(PermissionError):
        eng.apply([{"op": "move", "src": str(root / "file.pdf"), "dst": str(link / "moved.pdf")}], False)


def test_real_preview_apply_repeat_undo_restart(files):
    root, eng = files
    source = root / "owner report.pdf"
    source.write_bytes(b"real owner bytes")
    c = client()
    plan = c.post("/api/files/organize-plan", json={"root": str(root)}).json()
    assert source.exists() and plan["status"] == "PREVIEW_ONLY"
    result = c.post("/api/files/apply", json={"operations": plan["operations"], "approve": True})
    assert result.status_code == 200, result.text
    batch = result.json()["batch_id"]
    target = root / "Documents/PDF/owner report.pdf"
    assert target.read_bytes() == b"real owner bytes" and not source.exists()
    repeated = c.post("/api/files/apply", json={"operations": plan["operations"], "approve": True}).json()
    assert repeated["already_applied"] is True and repeated["batch_id"] == batch
    restarted = client()
    assert restarted.get("/api/files/batches").json()["batches"][0]["status"] == "APPLIED"
    restored = restarted.post(f"/api/files/undo/{batch}", json={"approve": True}).json()
    assert restored["status"] == "ROLLED_BACK"
    assert source.read_bytes() == b"real owner bytes" and not target.exists()
    repeated_undo = restarted.post(f"/api/files/undo/{batch}", json={"approve": True}).json()
    assert repeated_undo["already_undone"] is True


def test_stale_source_same_size_restored_mtime_refused(files):
    root, eng = files
    source = root / "report.pdf"
    source.write_text("before")
    plan = eng.organize_plan(str(root))
    stamp = source.stat()
    source.write_text("after!")
    os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    with pytest.raises(ValueError, match="source changed"):
        eng.apply(plan["operations"], True)
    assert source.read_text() == "after!" and not (root / "Documents").exists()


def test_unpreviewed_and_changed_operations_refused(files):
    root, eng = files
    source = root / "report.pdf"
    source.write_text("owner")
    op = {"op": "move", "src": str(source), "dst": str(root / "changed.pdf")}
    with pytest.raises(ValueError, match="refresh preview"):
        eng.apply([op], True)
    plan = eng.apply([op], False)
    plan["operations"][0]["dst"] = str(root / "not-approved.pdf")
    with pytest.raises(ValueError, match="refresh preview"):
        eng.apply(plan["operations"], True)
    assert source.exists()


def test_preflight_destination_conflict_never_partially_moves(files):
    root, eng = files
    for name in ("a.pdf", "b.pdf"): (root / name).write_text(name)
    plan = eng.organize_plan(str(root))
    target = Path(plan["operations"][1]["dst"])
    target.parent.mkdir(parents=True)
    target.write_text("do not overwrite")
    with pytest.raises(FileExistsError): eng.apply(plan["operations"], True)
    assert (root / "a.pdf").exists() and (root / "b.pdf").exists()
    assert target.read_text() == "do not overwrite"


def test_actual_first_move_is_rolled_back_after_late_error(files, monkeypatch):
    from file_commander_mini import domain
    root, eng = files
    for name in ("a.pdf", "b.pdf"): (root / name).write_text(name)
    plan = eng.organize_plan(str(root))
    original = domain.move_no_replace
    calls = 0
    def break_second(*args):
        nonlocal calls
        calls += 1
        if calls == 2: raise PermissionError("injected destination lock")
        return original(*args)
    monkeypatch.setattr(domain, "move_no_replace", break_second)
    with pytest.raises(ValueError, match="ROLLED_BACK"):
        eng.apply(plan["operations"], True)
    assert (root / "a.pdf").read_text() == "a.pdf"
    assert (root / "b.pdf").read_text() == "b.pdf"
    assert not (root / "Documents").exists()
    assert eng.s.kv_list("batches")[0]["value"]["status"] == "ROLLED_BACK"


def test_crash_after_real_effect_has_journal_and_explicit_recovery(files):
    root, eng = files
    source = root / "report.pdf"
    source.write_text("must survive")
    plan = eng.organize_plan(str(root))
    script = '''
import json,os,sys
from file_commander_mini import domain
from file_commander_mini.store import SQLiteStore
original=domain.move_no_replace
def crash(*args):
    original(*args)
    os._exit(71)
domain.move_no_replace=crash
domain.FileCommander(SQLiteStore("file-commander-mini")).apply(json.loads(sys.stdin.read()),True)
'''
    result = subprocess.run([sys.executable, "-c", script], input=json.dumps(plan["operations"]), text=True,
                            env=os.environ.copy(), capture_output=True, timeout=20)
    assert result.returncode == 71, result.stderr
    restarted = FileCommander(SQLiteStore("file-commander-mini"))
    record = restarted.s.kv_list("batches")[0]["value"]
    assert record["status"] == "APPLYING" and not source.exists()
    with pytest.raises(ValueError, match="RECOVERY_REQUIRED"):
        restarted.apply(plan["operations"], True)
    assert restarted.undo(record["batch_id"], True)["status"] == "ROLLED_BACK"
    assert source.read_text() == "must survive"


def test_read_only_parent_refused_before_mutation(files):
    root, eng = files
    source = root / "report.pdf"
    source.write_text("owner")
    plan = eng.organize_plan(str(root))
    mode = root.stat().st_mode
    root.chmod(0o555)
    try:
        with pytest.raises(PermissionError, match="read-only"):
            eng.apply(plan["operations"], True)
    finally: root.chmod(mode)
    assert source.exists()


@pytest.mark.parametrize("endpoint", ["scan", "duplicates", "organize-plan", "cleanup-summary", "rename-plan", "rule-plan", "project-groups"])
def test_all_owner_read_paths_return_explained_denial(files, endpoint):
    root, _ = files
    response = client().post("/api/files/" + endpoint, json={"root": str(root.parent)})
    assert response.status_code == 403 and response.json()["detail"]


def test_rule_cannot_escape_workspace(files):
    _, eng = files
    with pytest.raises(ValueError): eng.save_rule("escape", [".pdf"], "../outside")


def test_replaced_post_effect_evidence_does_not_report_success(files):
    root, eng = files
    source = root / "report.pdf"
    source.write_text("original")
    plan = eng.organize_plan(str(root))
    eng.apply(plan["operations"], True)
    target = Path(plan["operations"][0]["dst"])
    target.write_text("replaced")
    with pytest.raises(ValueError): eng.apply(plan["operations"], True)
    assert target.read_text() == "replaced"


def test_hardlink_cannot_launder_a_protected_source(files):
    root, eng = files
    secret = root.parent / "private.txt"
    secret.write_text("private bytes")
    linked = root / "ordinary.pdf"
    os.link(secret, linked)
    assert eng.scan(str(root))["count"] == 0
    with pytest.raises(PermissionError, match="hard-linked"):
        eng.apply([{"op": "move", "src": str(linked), "dst": str(root / "moved.pdf")}], False)
    assert secret.read_text() == "private bytes"


@pytest.mark.skipif(os.name != "posix", reason="POSIX advisory locks; Windows locks have a separate host gate")
def test_actual_exclusive_file_lock_is_reported_without_mutation(files):
    import fcntl
    root, eng = files
    source = root / "report.pdf"
    source.write_text("locked bytes")
    with source.open("rb") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        response = client().post("/api/files/organize-plan", json={"root": str(root)})
        assert response.status_code == 409, response.text
    assert source.read_text() == "locked bytes" and not (root / "Documents").exists()


def signed_request(method, path, payload, nonce="a"*64, stamp=None):
    from file_commander_mini import auth
    stamp = str(int(time.time())) if stamp is None else stamp
    token = os.environ["BOSSMAN_APP_TOKEN"]
    return {auth.SIGNATURE: auth.request_mac(token, method, path, payload, stamp, nonce),
            auth.STAMP: stamp, auth.NONCE: nonce, "Content-Type": "application/json"}


def test_proxy_request_proof_and_response_are_bound_and_replay_survives_restart(files):
    root, _ = files
    payload = json.dumps({"root": str(root)}).encode()
    path = "/api/files/scan"
    headers = signed_request("POST", path, payload)
    c = TestClient(build_app())
    result = c.post(path, content=payload, headers=headers)
    assert result.status_code == 200
    material = "\n".join(("response", "a"*64, "200", hashlib.sha256(result.content).hexdigest()))
    expected = hmac.new(os.environ["BOSSMAN_APP_TOKEN"].encode(), material.encode(), hashlib.sha256).hexdigest()
    assert hmac.compare_digest(result.headers["X-Bossman-App-Response"], expected)
    assert c.post(path, content=payload, headers=headers).status_code == 401
    assert TestClient(build_app()).post(path, content=payload, headers=headers).status_code == 401


@pytest.mark.parametrize("change", ["method", "path", "query", "body", "timestamp", "nonce"])
def test_mutating_any_request_binding_refuses_before_file_access(files, change):
    root, _ = files
    payload = json.dumps({"root": str(root)}).encode()
    path = "/api/files/scan"
    headers = signed_request("POST", path, payload)
    method = "POST"
    if change == "method": method = "GET"
    if change == "path": path = "/api/files/duplicates"
    if change == "query": path += "?other=1"
    if change == "body": payload += b" "
    if change == "timestamp": headers["X-Bossman-App-Timestamp"] = "1e999"
    if change == "nonce": headers["X-Bossman-App-Nonce"] = "b"*64
    result = TestClient(build_app()).request(method, path, content=payload, headers=headers)
    assert result.status_code == 401
