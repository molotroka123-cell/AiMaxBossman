import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import tree_apply_evidence as t  # noqa: E402


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True).stdout.strip()


def _setup(tmp_path):
    repo = tmp_path
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-q", "-m", "x")
    sha = _git(repo, "rev-parse", "HEAD")
    nodes = [
        {"id": "root", "label": "R", "parent": None, "status": "mixed", "detail": "", "sources": []},
        {"id": "zone", "label": "Z", "parent": "root", "status": "mixed", "detail": "", "sources": []},
        {"id": "leaf", "label": "L", "parent": "zone", "status": "code", "detail": "d.", "sources": []},
        {"id": "rec", "label": "Rec", "parent": "zone", "status": "recorded", "detail": "", "sources": []},
    ]
    seed = repo / "seed.json"
    seed.write_text(json.dumps({"as_of": "x", "nodes": nodes}), encoding="utf-8")
    evid = repo / "evidence"
    (evid / "out").mkdir(parents=True)
    return repo, sha, seed, evid


def _rc(head, evid, nid="leaf", **kw):
    body = f"ok {nid}"
    (evid / "out" / f"{nid}.txt").write_text(body, encoding="utf-8")
    rc = dict(node_id=nid, sha=head, probe="import ok", command="python -c pass", exit_code=0,
              started_at="2026-10-06T10:00:00Z", finished_at="2026-10-06T10:00:01Z",
              output_sha256=hashlib.sha256(body.encode()).hexdigest(), output_tail=body,
              verdict="PASS", kind="import")
    rc.update(kw)
    return rc


def _run(repo, seed, evid, rcs, write=False):
    (evid / "lane.json").write_text(json.dumps(rcs), encoding="utf-8")
    return t.apply(seed, evid, repo, write=write)


def test_good_receipt_turns_green(tmp_path):
    repo, sha, seed, evid = _setup(tmp_path)
    r = _run(repo, seed, evid, [_rc(sha, evid)], write=True)
    node = {n["id"]: n for n in json.loads(seed.read_text(encoding="utf-8"))["nodes"]}["leaf"]
    assert node["status"] == "reported"
    assert f"Прогон 2026-10-06 @ {sha[:8]}: import ok" in node["detail"]
    assert node["sources"] and all(isinstance(x, dict) and x.get("path") for x in node["sources"])
    assert dict(r["zones"]) == {"zone": 1}
    assert (evid / "SUMMARY.md").exists()
    exp = json.loads((evid / "tree.export.json").read_text(encoding="utf-8"))
    assert exp["schema"] == 1 and {"id", "label", "parent", "status", "short"} <= set(exp["nodes"][0])


def test_forged_receipts_rejected(tmp_path):
    repo, sha, seed, evid = _setup(tmp_path)
    bad = [
        _rc(sha, evid, output_sha256="0" * 64),
        _rc(sha, evid, verdict="FAIL"),
        _rc(sha, evid, nid="zone"),
        _rc(sha, evid, nid="rec"),
        _rc(sha, evid, exit_code=1),
        _rc(sha, evid, command="  "),
        _rc(sha, evid, sha="a" * 40),
        _rc(sha, evid, output_tail="x" * 1501),
    ]
    r = _run(repo, seed, evid, bad)
    assert r["accepted"] == []
    assert len(r["rejected"]) == len(bad)
    assert {n["id"]: n["status"] for n in r["seed"]["nodes"]} == {
        "root": "mixed", "zone": "mixed", "leaf": "code", "rec": "recorded"}


def test_dry_run_does_not_write(tmp_path):
    repo, sha, seed, evid = _setup(tmp_path)
    before = seed.read_text(encoding="utf-8")
    r = _run(repo, seed, evid, [_rc(sha, evid)], write=False)
    assert len(r["accepted"]) == 1 and seed.read_text(encoding="utf-8") == before


def test_scrub():
    assert "sk-" not in t.scrub("key sk-abcdefghijklmnopqrstuvwx end")


def test_retire_is_audit_only_and_never_green(tmp_path):
    repo, sha, seed, evid = _setup(tmp_path)
    assert _run(repo, seed, evid, [_rc(sha, evid, verdict="RETIRE", kind="import", reason="x" * 30)])["accepted"] == []
    assert _run(repo, seed, evid, [_rc(sha, evid, verdict="RETIRE", kind="audit", reason="short")])["accepted"] == []
    assert _run(repo, seed, evid, [_rc(sha, evid, verdict="PASS", kind="audit")])["accepted"] == []
    r = _run(repo, seed, evid, [_rc(sha, evid, verdict="RETIRE", kind="audit", reason="no importers, no route, no tests")], write=True)
    assert len(r["accepted"]) == 1
    st = {n["id"]: n["status"] for n in json.loads(seed.read_text(encoding="utf-8"))["nodes"]}
    assert st["leaf"] == "retired"
    exp = json.loads((evid / "tree.export.json").read_text(encoding="utf-8"))
    assert "leaf" not in {n["id"] for n in exp["nodes"]}


def test_green_leaf_loses_the_not_verified_boilerplate(tmp_path):
    repo, sha, seed, evid = _setup(tmp_path)
    data = json.loads(seed.read_text(encoding="utf-8"))
    for n in data["nodes"]:
        if n["id"] == "leaf":
            n["detail"] = "Модуль найден. Семантика и live работоспособность не сертифицированы."
    seed.write_text(json.dumps(data), encoding="utf-8")
    _run(repo, seed, evid, [_rc(sha, evid)], write=True)
    detail = {n["id"]: n for n in json.loads(seed.read_text(encoding="utf-8"))["nodes"]}["leaf"]["detail"]
    assert "Семантика и live работоспособность не сертифицированы." not in detail
    assert "Импорт и существующие тесты прошли" in detail and "Прогон 2026-10-06" in detail


# ---- 'working' = reported + tests passed against the INSTALLED build ----

def _setup_working(tmp_path, status="reported"):
    repo, sha, seed, evid = _setup(tmp_path)
    data = json.loads(seed.read_text(encoding="utf-8"))
    for n in data["nodes"]:
        if n["id"] == "leaf":
            n["status"] = status
    seed.write_text(json.dumps(data), encoding="utf-8")
    (evid / "installed-builds.json").write_text(
        json.dumps([{"sha": sha, "build_dir": "C:/x/BOSSMAN-test", "verified_at": "2026-10-07T00:00:00Z"}]),
        encoding="utf-8")
    return repo, sha, seed, evid


def _irc(head, evid, nid="leaf", **kw):
    body = f"installed ok {nid}"
    (evid / "out" / f"installed-{nid}.txt").write_text(body, encoding="utf-8")
    rc = dict(node_id=nid, sha=head, probe="pytest 3 passed", command="python -m pytest x", exit_code=0,
              started_at="2026-10-07T10:00:00Z", finished_at="2026-10-07T10:00:01Z",
              output_sha256=hashlib.sha256(body.encode()).hexdigest(), output_tail=body,
              verdict="PASS", kind="installed_pytest", installed_build="BOSSMAN-test")
    rc.update(kw)
    return rc


def _irun(repo, seed, evid, rcs, write=False, name="installed-lane.json"):
    (evid / name).write_text(json.dumps(rcs), encoding="utf-8")
    return t.apply(seed, evid, repo, write=write)


def _status(seed):
    return {n["id"]: n for n in json.loads(seed.read_text(encoding="utf-8"))["nodes"]}


def test_installed_receipt_promotes_reported_leaf_to_working(tmp_path):
    repo, sha, seed, evid = _setup_working(tmp_path)
    r = _irun(repo, seed, evid, [_irc(sha, evid)], write=True)
    assert r["rejected"] == [] and len(r["accepted"]) == 1
    node = _status(seed)["leaf"]
    assert node["status"] == "working"
    assert node["detail"].endswith(f" Работает в установленном Bossman @ {sha[:8]}: pytest 3 passed")
    src = node["sources"][-1]
    assert src["kind"] == "receipt" and src["sha"] == sha and src["branch"] and "installed-lane.json#leaf" in src["path"]
    exp = json.loads((evid / "tree.export.json").read_text(encoding="utf-8"))
    assert {n["id"]: n["status"] for n in exp["nodes"]}["leaf"] == "working"
    assert "installed-builds.json" not in (evid / "SUMMARY.md").read_text(encoding="utf-8")


def test_code_leaf_can_go_green_then_working_in_one_run(tmp_path):
    repo, sha, seed, evid = _setup_working(tmp_path, status="code")
    (evid / "lane.json").write_text(json.dumps([_rc(sha, evid)]), encoding="utf-8")
    r = _irun(repo, seed, evid, [_irc(sha, evid)], write=True)
    assert len(r["accepted"]) == 2 and _status(seed)["leaf"]["status"] == "working"


def test_forged_installed_receipts_never_promote(tmp_path):
    repo, sha, seed, evid = _setup_working(tmp_path)
    bad = [
        _irc(sha, evid, kind="pytest"),
        _irc(sha, evid, kind="import"),
        _irc(sha, evid, verdict="FAIL"),
        _irc(sha, evid, exit_code=1),
        _irc(sha, evid, probe=" "),
        _irc(sha, evid, command=""),
        _irc(sha, evid, sha="a" * 40),
        _irc(sha, evid, output_sha256="0" * 64),
        _irc(sha, evid, nid="zone"),
        _irc(sha, evid, nid="rec"),
        _irc(sha, evid, kind=["installed_pytest"]),
    ]
    r = _irun(repo, seed, evid, bad)
    assert r["accepted"] == [] and len(r["rejected"]) == len(bad)
    assert _status(seed)["leaf"]["status"] == "reported"


def test_non_reported_leaf_is_not_promoted(tmp_path):
    for st in ("code", "branch", "recorded"):
        d = tmp_path / st
        d.mkdir()
        repo, sha, seed, evid = _setup_working(d, status=st)
        r = _irun(repo, seed, evid, [_irc(sha, evid)])
        assert r["accepted"] == [] and "not reported" in r["rejected"][0][2]
        assert _status(seed)["leaf"]["status"] == st


def test_unknown_build_sha_and_missing_builds_file_rejected(tmp_path):
    repo, sha, seed, evid = _setup_working(tmp_path)
    (evid / "installed-builds.json").write_text(json.dumps([{"sha": "b" * 40, "build_dir": "x", "verified_at": "y"}]))
    r = _irun(repo, seed, evid, [_irc(sha, evid)])
    assert r["accepted"] == [] and "registered installed build" in r["rejected"][0][2]
    (evid / "installed-builds.json").unlink()
    r = _irun(repo, seed, evid, [_irc(sha, evid)])
    assert r["accepted"] == []


def test_installed_receipt_in_ordinary_lane_file_is_rejected(tmp_path):
    repo, sha, seed, evid = _setup_working(tmp_path)
    r = _irun(repo, seed, evid, [_irc(sha, evid)], name="lane.json")
    assert r["accepted"] == [] and "outside installed" in r["rejected"][0][2]


def test_installed_hash_mismatch_when_output_file_is_edited(tmp_path):
    repo, sha, seed, evid = _setup_working(tmp_path)
    rc = _irc(sha, evid)
    (evid / "out" / "installed-leaf.txt").write_text("tampered", encoding="utf-8")
    r = _irun(repo, seed, evid, [rc])
    assert r["accepted"] == [] and r["rejected"][0][2] == "output hash mismatch"
