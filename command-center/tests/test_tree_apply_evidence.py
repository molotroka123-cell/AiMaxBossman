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
