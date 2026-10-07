"""proof_dossier: every number has a file + sha256 behind it; missing evidence prints НЕТ ДОКАЗАТЕЛЬСТВА (temp dirs only)."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import proof_dossier as pd  # noqa: E402

CYCLE = """task abc started (goal-budget, worker nvidia-nim)
SELF_REPAIR=INDEPENDENT_VERIFICATION_PASS
{
 "task_id": "abc", "case": "goal-budget", "worker": "nvidia-nim", "model": "m",
 "holdout_base": {"total": 10, "failed": 4}, "holdout_patched": {"total": 10, "failed": 0},
 "stages": {"DEFECT_REPRODUCED": true, "MODEL_PATCH_CREATED": true, "EXPERIENCE_AUTO_SAVED": false}
}
"""


def fake_live(sha):
    def f(url, save):
        save.write_text(json.dumps({"build_sha": sha}))
        return {"build_sha": sha}
    return f


def make_repo(root: Path) -> Path:
    repo = root / "repo"
    (repo / "command-center/bcc").mkdir(parents=True)
    (repo / "command-center/bcc/capability_tree_seed.json").write_text(json.dumps({"nodes": [
        {"id": "a", "status": "reported"}, {"id": "b", "status": "reported"}, {"id": "c", "status": "blocked", "label": "stuck"}]}))
    ev = repo / "docs/architecture/bossman-tree-20261005/evidence"
    ev.mkdir(parents=True)
    (ev / "lane.json").write_text(json.dumps([
        {"node_id": "a", "kind": "import", "verdict": "PASS"}, {"node_id": "b", "kind": "pytest", "verdict": "PASS"},
        {"node_id": "c", "kind": "pytest", "verdict": "FAIL"}]))
    (repo / "tools").mkdir()
    (repo / "tools" / "remove_bossman_tomorrow_tasks.ps1").write_text("x")
    return repo


def args_for(tmp: Path, repo: Path, **kw):
    ns = ["--repo", str(repo), "--out-root", str(tmp / "out"), "--date", "2026-10-08", "--no-live",
          "--app-root", str(tmp / "noapp"), "--selfrepair-dir", str(tmp / "none"), "--test-dir", str(tmp / "none")]
    ns += [x for k, v in kw.items() for x in (f"--{k.replace('_', '-')}", str(v))]
    return ns


def test_sha_links_and_counts(tmp_path):
    repo = make_repo(tmp_path)
    sr = tmp_path / "sr"
    sr.mkdir()
    (sr / "cycle1.log").write_text(CYCLE, encoding="utf-8")
    (sr / "cycle2.log").write_text("SELF_REPAIR=NOT_RUN\nсвязь с Bossman потеряна", encoding="utf-8")
    pd.main(args_for(tmp_path, repo, extra_selfrepair=sr))
    out = tmp_path / "out" / "proof-dossier-2026-10-08"
    model = json.loads((out / "proof-dossier.json").read_text(encoding="utf-8"))
    md = (out / "proof-dossier.md").read_text(encoding="utf-8")
    assert (out / "proof-dossier.html").read_text(encoding="utf-8").startswith("<!doctype html>")
    seed = repo / "command-center/bcc/capability_tree_seed.json"
    digest = hashlib.sha256(seed.read_bytes()).hexdigest()
    tree = next(s for s in model["sections"] if s["title"].startswith("2."))
    reported = next(f for f in tree["facts"] if "reported" in f["label"])
    assert reported["value"] == 2 and reported["evidence"][0]["sha256"] == digest
    receipts = next(s for s in model["sections"] if s["title"].startswith("3."))
    assert {f["label"]: f["value"] for f in receipts["facts"]}["kind=pytest"] == 2
    cycles = next(s for s in model["sections"] if s["title"].startswith("4."))
    rows = {r["name"]: r for r in cycles["cycles"]}
    assert rows["cycle1"]["worker"] == "nvidia-nim" and rows["cycle1"]["holdout_base"]["failed"] == 4
    assert rows["cycle1"]["stages"] == ["DEFECT_REPRODUCED", "MODEL_PATCH_CREATED"]
    assert rows["cycle2"]["verdict"] == "NOT_RUN" and "holdout_base" not in rows["cycle2"]
    assert hashlib.sha256((sr / "cycle1.log").read_bytes()).hexdigest() in md
    assert "узел заблокирован: c" in md and "цикл cycle2" in md


def test_missing_evidence_is_labelled_not_invented(tmp_path):
    empty = tmp_path / "emptyrepo"
    empty.mkdir()
    pd.main(args_for(tmp_path, empty))
    out = tmp_path / "out" / "proof-dossier-2026-10-08"
    model = json.loads((out / "proof-dossier.json").read_text(encoding="utf-8"))
    md = (out / "proof-dossier.md").read_text(encoding="utf-8")
    assert pd.NO_EVIDENCE in md
    for s in model["sections"][:7]:
        for f in s["facts"]:
            assert f["missing"] and f["value"] == pd.NO_EVIDENCE and not f["evidence"]
    ident = model["sections"][0]
    assert all(f["value"] == pd.NO_EVIDENCE for f in ident["facts"])


def test_identity_compares_manifest_with_live_file(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    app = tmp_path / "app" / "BOSSMAN-Windows-x64-x"
    app.mkdir(parents=True)
    (app / "MANIFEST.json").write_text(json.dumps({"source_sha": "abc123", "source_dirty": False}))
    monkeypatch.setattr(pd, "fetch_live", fake_live("abc123"))
    ns = ["--repo", str(repo), "--out-root", str(tmp_path / "out"), "--date", "d", "--app-root", str(tmp_path / "app"),
          "--selfrepair-dir", str(tmp_path / "none"), "--test-dir", str(tmp_path / "none")]
    pd.main(ns)
    model = json.loads((tmp_path / "out" / "proof-dossier-d" / "proof-dossier.json").read_text(encoding="utf-8"))
    facts = {f["label"]: f for f in model["sections"][0]["facts"]}
    assert facts["live build == installed MANIFEST"]["value"].startswith("PASS")
    assert len(facts["live build == installed MANIFEST"]["evidence"]) == 2
    monkeypatch.setattr(pd, "fetch_live", fake_live("zzz"))
    pd.main(ns)
    model = json.loads((tmp_path / "out" / "proof-dossier-d" / "proof-dossier.json").read_text(encoding="utf-8"))
    assert next(f for f in model["sections"][0]["facts"] if f["label"].startswith("live build ==" ))["value"].startswith("FAIL")


def test_ux_and_tests_sections(tmp_path):
    repo = make_repo(tmp_path)
    day = tmp_path / "day"
    day.mkdir()
    (day / "ux-sweep.json").write_text(json.dumps({"summary": {"probed": 5, "counts": {"OK": 4, "DEAD": 1}},
                                                   "records": [{"verdict": "DEAD", "route": "home", "label": "x", "detail": "d"}]}))
    (day / "regress-1.log").write_text("..\n90 passed in 81s\nROOT_EXIT=0\n")
    pd.main(args_for(tmp_path, repo, extra_evidence=day))
    model = json.loads((tmp_path / "out" / "proof-dossier-2026-10-08" / "proof-dossier.json").read_text(encoding="utf-8"))
    ux = next(s for s in model["sections"] if s["title"].startswith("5."))
    assert {f["label"]: f["value"] for f in ux["facts"]}["summary.probed"] == 5
    tests = next(s for s in model["sections"] if s["title"].startswith("6."))
    assert tests["facts"][0]["value"].startswith("90 passed")
    issues = next(s for s in model["sections"] if s["title"].startswith("8."))
    assert any("UX DEAD" in f["label"] for f in issues["facts"])


def test_html_escapes(tmp_path):
    repo = make_repo(tmp_path)
    sr = tmp_path / "sr"
    sr.mkdir()
    (sr / "cycle1.log").write_text("SELF_REPAIR=<script>alert(1)</script>\n", encoding="utf-8")
    pd.main(args_for(tmp_path, repo, extra_selfrepair=sr))
    page = (tmp_path / "out" / "proof-dossier-2026-10-08" / "proof-dossier.html").read_text(encoding="utf-8")
    assert "<script>alert" not in page
