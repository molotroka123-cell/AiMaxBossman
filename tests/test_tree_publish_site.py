"""tools/tree_publish_site.py with temporary local git repos (no network; origin is a local bare repo)."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT_FOR_TEST = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_FOR_TEST / "tools"))
import tree_publish_site as p  # noqa: E402

# stub of the site generator: deliberately emits the STALE facts the real defect showed (13 zones, detached, short head)
STUB_SYNC = """import argparse, json, subprocess
from collections import Counter
from pathlib import Path
ap = argparse.ArgumentParser()
ap.add_argument("--source"); ap.add_argument("--registry"); ap.add_argument("--out")
a = ap.parse_args()
assert Path(a.registry).name == "tree-registry.json"
seed = json.loads((Path(a.source) / "command-center/bcc/capability_tree_seed.json").read_text(encoding="utf-8"))
nodes = [n for n in seed["nodes"] if n["status"] != "retired"]
head = subprocess.run(["git", "-C", a.source, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
out = Path(a.out); (out / "data").mkdir(exist_ok=True)
(out / "data" / "meta.json").write_text(json.dumps({"counts": {"nodes": len(nodes), "zones": 13,
    "by_status": dict(Counter(n["status"] for n in nodes))}, "source": {"head": head[:12], "branch": "detached"}}), encoding="utf-8")
(out / "data" / "tree.json").write_text(json.dumps({"nodes": [{"id": n["id"], "parent": n.get("parent") or ""} for n in nodes]}), encoding="utf-8")
print("SYNC=OK")
"""
GOOD_TEST = "import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertTrue(True)\n"
BAD_TEST = "import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.fail('x')\n"
SEED_NODES = [
    {"id": "bossman", "label": "B", "parent": None, "status": "mixed", "detail": "", "sources": []},
    {"id": "z1", "label": "Z1", "parent": "bossman", "status": "mixed", "detail": "", "sources": []},
    {"id": "z2", "label": "Z2", "parent": "bossman", "status": "mixed", "detail": "", "sources": []},
    {"id": "l1", "label": "L1", "parent": "z1", "status": "reported", "detail": "", "sources": []},
    {"id": "l2", "label": "L2", "parent": "z2", "status": "code", "detail": "", "sources": []},
    {"id": "old", "label": "O", "parent": "z2", "status": "retired", "detail": "", "sources": []},
]


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *a],
                          capture_output=True, text=True, check=True).stdout.strip()


def head(repo):
    return git(repo, "rev-parse", "HEAD")


def quiet(*_):
    return None


def registry_doc(head_sha, nodes=SEED_NODES):
    rows = []
    for n in nodes:
        if n["status"] == "retired":
            continue
        rows.append({"id": n["id"], "zone_parent": "bossman" if n["parent"] == "bossman" else None,
                     "integration_status": n["status"]})
    return {"schema": "bossman-tree-registry-1", "head": head_sha, "leaves": rows}


def snapshot(root: Path) -> dict:
    return {str(f.relative_to(root)): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in sorted(root.rglob("*")) if f.is_file() and ".git" not in f.parts}


@pytest.fixture
def env(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    site_origin = tmp_path / "site-origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(site_origin)], check=True)
    site = tmp_path / "site"
    site.mkdir()
    git(site, "init", "-q", "-b", "main")
    git(site, "config", "user.name", "t")
    git(site, "config", "user.email", "t@t")
    (site / "scripts").mkdir()
    (site / "scripts" / "sync_bossman.py").write_text(STUB_SYNC, encoding="utf-8")
    (site / "tests").mkdir()
    (site / "tests" / "test_ok.py").write_text(GOOD_TEST, encoding="utf-8")
    (site / "llms.txt").write_text("x\n", encoding="utf-8")
    git(site, "add", "-A")
    git(site, "commit", "-q", "-m", "init")
    git(site, "remote", "add", "origin", str(site_origin))
    git(site, "push", "-q", "origin", "main")
    src = tmp_path / "src"
    (src / "command-center" / "bcc").mkdir(parents=True)
    (src / "docs" / "architecture").mkdir(parents=True)
    (src / p.EVID_REL).mkdir(parents=True)
    (src / p.SEED_REL).write_text(json.dumps({"as_of": "x", "source_sha": "e4539337", "selected_branch": "feat/old",
                                              "nodes": SEED_NODES}), encoding="utf-8")
    git(src, "init", "-q", "-b", "main")
    git(src, "add", "-A")
    git(src, "commit", "-q", "-m", "seed")
    first = head(src)
    (src / "docs" / "architecture" / "tree-registry.json").write_text(json.dumps(registry_doc(first)), encoding="utf-8")
    git(src, "add", "-A")
    git(src, "commit", "-q", "-m", "registry")
    git(src, "remote", "add", "origin", str(origin))
    git(src, "push", "-q", "origin", "main")
    return site, src, origin, site_origin


def commit_src(src, rel, text, push=True):
    (src / rel).write_text(text, encoding="utf-8")
    git(src, "add", rel)
    git(src, "commit", "-q", "-m", "change " + rel)
    if push:
        git(src, "push", "-q", "origin", "main")


def meta_of(site_or_dir):
    return json.loads((Path(site_or_dir) / "data" / "meta.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- freshness gate
def test_refuses_a_stale_head(env, tmp_path):
    site, src, origin, _ = env
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    commit_src(other, "README.md", "newer main\n")
    git(other, "push", "-q", "origin", "main")
    assert head(src) != git(origin, "rev-parse", "main")
    with pytest.raises(p.Refused, match="not origin/main"):
        p.publish(site, src, log=quiet)


def test_refuses_a_head_that_is_ahead_of_origin_main(env):
    site, src, _, _ = env
    commit_src(src, "README.md", "local only\n", push=False)
    with pytest.raises(p.Refused, match="not origin/main"):
        p.publish(site, src, log=quiet)


def test_refuses_a_dirty_tree_modified_and_untracked(env):
    site, src, _, _ = env
    (src / p.SEED_REL).write_text((src / p.SEED_REL).read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(p.Refused, match="not clean"):
        p.publish(site, src, log=quiet)
    git(src, "checkout", "--", p.SEED_REL)
    (src / "stray.txt").write_text("x", encoding="utf-8")
    with pytest.raises(p.Refused, match="not clean"):
        p.publish(site, src, log=quiet)


def test_refuses_when_origin_main_cannot_be_resolved(env):
    site, src, _, _ = env
    git(src, "remote", "set-url", "origin", str(src.parent / "does-not-exist.git"))
    with pytest.raises(p.Refused, match="cannot resolve origin"):
        p.publish(site, src, log=quiet)


def test_refuses_a_non_git_source(env, tmp_path):
    site, _, _, _ = env
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(p.Refused):
        p.publish(site, plain, log=quiet)


# ---------------------------------------------------------------- dry run
def test_dry_run_writes_nothing_outside_temp_and_stamps_real_facts(env, tmp_path):
    site, src, origin, site_origin = env
    before = snapshot(tmp_path)
    site_head, src_head = head(site), head(src)
    res = p.publish(site, src, log=quiet)
    assert snapshot(tmp_path) == before                       # no file anywhere under the sandbox changed
    assert head(site) == site_head and head(src) == src_head
    assert git(site, "status", "--porcelain") == "" and git(src, "status", "--porcelain") == ""
    assert git(site_origin, "rev-parse", "main") == site_head
    assert not res["pushed"] and res["committed"] is None and res["tests_ok"]
    assert res["source_sha"] == src_head and res["branch"] == "main"
    assert res["diff"]["added"] and not (site / "data").exists()


def test_meta_is_stamped_with_real_sha_branch_and_seed_zone_count(env, monkeypatch):
    site, src, _, _ = env
    seen = {}
    orig = p.run_site_tests

    def spy(work, python):
        seen["meta"] = json.loads((Path(work) / "data" / "meta.json").read_text(encoding="utf-8"))
        return orig(work, python)

    monkeypatch.setattr(p, "run_site_tests", spy)
    p.publish(site, src, log=quiet)
    m = seen["meta"]
    assert m["counts"]["zones"] == 2 == len(p.seed_facts(src)["zones"])   # the stub claimed 13
    assert m["source"]["head_full"] == head(src) and m["source"]["head"] == head(src)[:12]
    assert m["source"]["branch"] == "main"                                  # the stub claimed "detached"
    assert m["checks"]["ci"]["state"] == "not_run" and m["checks"]["live"]["state"] == "not_run"


def test_zone_count_follows_the_seed(env):
    site, src, _, _ = env
    nodes = SEED_NODES + [{"id": "z3", "label": "Z3", "parent": "bossman", "status": "code", "detail": "", "sources": []}]
    doc = json.loads((src / p.SEED_REL).read_text(encoding="utf-8"))
    doc["nodes"] = nodes
    commit_src(src, p.SEED_REL, json.dumps(doc))
    (src / "docs/architecture/tree-registry.json").write_text(json.dumps(registry_doc("0" * 40, nodes)), encoding="utf-8")
    reg = json.loads((src / "docs/architecture/tree-registry.json").read_text(encoding="utf-8"))
    reg["head"] = git(src, "rev-parse", "HEAD~1")
    commit_src(src, "docs/architecture/tree-registry.json", json.dumps(reg))
    assert p.publish(site, src, log=quiet)["seed_zones"] == 3


# ---------------------------------------------------------------- CI / live claims
def test_no_ci_green_without_a_receipt_for_the_exact_sha(env, tmp_path):
    site, src, _, _ = env
    rdir = tmp_path / "receipts"
    rdir.mkdir()
    sha = head(src)
    assert p.read_receipt(rdir, sha)["ci"]["state"] == "not_run"                     # no file
    (rdir / f"{sha}.json").write_text(json.dumps({"sha": "f" * 40, "ci": {"state": "green"}}), encoding="utf-8")
    assert p.read_receipt(rdir, sha)["ci"]["state"] == "not_run"                     # file name right, inner sha wrong
    (rdir / ("a" * 40 + ".json")).write_text(json.dumps({"sha": "a" * 40, "ci": {"state": "green"}}), encoding="utf-8")
    assert p.read_receipt(rdir, sha)["ci"]["state"] == "not_run"                     # receipt for another sha
    (rdir / f"{sha}.json").write_text(json.dumps({"sha": sha, "ci": {"state": "maybe"}}), encoding="utf-8")
    assert p.read_receipt(rdir, sha)["ci"]["state"] == "not_run"                     # unknown vocabulary is ignored
    assert p.read_receipt(rdir, sha)["live"]["state"] == "not_run"
    (rdir / f"{sha}.json").write_text(json.dumps({"sha": sha, "ci": {"state": "green", "run_url": "u"}}), encoding="utf-8")
    assert p.read_receipt(rdir, sha)["ci"]["state"] == "green"                       # only now
    assert p.read_receipt(rdir, sha)["live"]["state"] == "not_run"                   # and live stays unclaimed
    res = p.publish(site, src, log=quiet, receipts_dir=rdir)
    assert res["checks"] == {"ci": "green", "live": "not_run"}


# ---------------------------------------------------------------- consistency negative controls
def _registry(src):
    return json.loads((src / "docs/architecture/tree-registry.json").read_text(encoding="utf-8"))


def _commit_registry(src, reg):
    commit_src(src, "docs/architecture/tree-registry.json", json.dumps(reg))


def test_registry_missing_a_zone_is_refused(env):
    site, src, _, _ = env
    reg = _registry(src)
    reg["leaves"] = [r for r in reg["leaves"] if r["id"] != "z2"]
    _commit_registry(src, reg)
    with pytest.raises(p.Refused, match="zone mismatch"):
        p.publish(site, src, log=quiet)


def test_registry_status_drift_and_missing_leaf_and_foreign_row_are_refused(env):
    site, src, _, _ = env
    reg = _registry(src)
    reg["leaves"][3]["integration_status"] = "working"
    _commit_registry(src, reg)
    with pytest.raises(p.Refused, match="status differs"):
        p.publish(site, src, log=quiet)
    reg = _registry(src)
    reg["leaves"][3]["integration_status"] = "reported"
    reg["leaves"] = [r for r in reg["leaves"] if r["id"] != "l2"]
    _commit_registry(src, reg)
    with pytest.raises(p.Refused, match="no registry row"):
        p.publish(site, src, log=quiet)
    reg["leaves"].append({"id": "ghost", "zone_parent": None, "integration_status": "code"})
    _commit_registry(src, reg)
    with pytest.raises(p.Refused, match="not seed nodes"):
        p.publish(site, src, log=quiet)


def test_registry_head_not_an_ancestor_is_refused_but_a_lag_only_warns(env):
    site, src, _, _ = env
    warnings = []
    res = p.publish(site, src, log=warnings.append)
    assert res["warnings"] and any("lags" in w for w in warnings)               # fixture registry head is the previous commit
    reg = _registry(src)
    reg["head"] = "b" * 40
    _commit_registry(src, reg)
    with pytest.raises(p.Refused, match="not an ancestor"):
        p.publish(site, src, log=quiet)


def test_meta_with_wrong_node_count_is_refused(env):
    site, src, _, _ = env
    (site / "scripts" / "sync_bossman.py").write_text(STUB_SYNC.replace('"nodes": len(nodes)', '"nodes": 999'), encoding="utf-8")
    git(site, "add", "-A")
    git(site, "commit", "-q", "-m", "bad generator")
    with pytest.raises(p.Refused, match="meta inconsistent"):
        p.publish(site, src, log=quiet)


# ---------------------------------------------------------------- push gate and the old safety rules
def test_push_requires_the_owner_approval_env_var(env):
    site, src, _, site_origin = env
    before = git(site_origin, "rev-parse", "main")
    for bad in ({}, {p.APPROVAL_ENV: "0"}, {p.APPROVAL_ENV: "true"}):
        with pytest.raises(p.Refused, match=p.APPROVAL_ENV):
            p.publish(site, src, push=True, log=quiet, env=bad)
    assert git(site_origin, "rev-parse", "main") == before and head(site) == before


def test_approved_push_commits_only_generated_paths_logs_and_pushes(env, tmp_path):
    site, src, _, site_origin = env
    plog = tmp_path / "publish.jsonl"
    res = p.publish(site, src, push=True, log=quiet, env={p.APPROVAL_ENV: "1"}, publish_log=plog)
    assert res["pushed"] and git(site_origin, "rev-parse", "main") == head(site) == res["committed"]
    msg = git(site, "log", "-1", "--format=%B")
    assert "reported=1" in msg and "Claude-Session:" in msg
    assert git(site, "status", "--porcelain") == ""
    m = meta_of(site)
    assert m["source"]["head_full"] == head(src) and m["counts"]["zones"] == 2 and m["source"]["branch"] == "main"
    row = json.loads(plog.read_text(encoding="utf-8").splitlines()[-1])
    assert row["source_sha"] == head(src) and row["site_commit"] == res["committed"] and row["zones"] == 2
    assert head(src) == git(src, "rev-parse", "main")


def test_push_refused_when_site_tests_fail(env):
    site, src, _, site_origin = env
    (site / "tests" / "test_ok.py").write_text(BAD_TEST, encoding="utf-8")
    git(site, "add", "-A")
    git(site, "commit", "-q", "-m", "break tests")
    git(site, "push", "-q", "origin", "main")
    before = git(site_origin, "rev-parse", "main")
    with pytest.raises(p.Refused, match="tests failed"):
        p.publish(site, src, push=True, log=quiet, env={p.APPROVAL_ENV: "1"})
    assert git(site_origin, "rev-parse", "main") == before and not git(site, "diff", "--cached", "--name-only")


def test_push_refused_with_unrelated_uncommitted_changes(env):
    site, src, _, site_origin = env
    (site / "scripts" / "sync_bossman.py").write_text(STUB_SYNC + "\n# local edit\n", encoding="utf-8")
    before = git(site_origin, "rev-parse", "main")
    with pytest.raises(p.Refused, match="unrelated uncommitted"):
        p.publish(site, src, push=True, log=quiet, env={p.APPROVAL_ENV: "1"})
    assert git(site_origin, "rev-parse", "main") == before


def test_push_refused_off_main(env):
    site, src, _, _ = env
    git(site, "checkout", "-q", "-b", "side")
    with pytest.raises(p.Refused, match="not main"):
        p.publish(site, src, push=True, log=quiet, env={p.APPROVAL_ENV: "1"})


def test_generated_paths_and_pycache_do_not_count_as_unrelated(env):
    site, _, _, _ = env
    (site / "data").mkdir()
    (site / "data" / "x.json").write_text("{}", encoding="utf-8")
    (site / "tests" / "__pycache__").mkdir()
    (site / "tests" / "__pycache__" / "a.pyc").write_bytes(b"0")
    assert p.unrelated_changes(site) == []


def test_missing_site_clone_is_refused(tmp_path):
    with pytest.raises(p.Refused):
        p.publish(tmp_path / "nope", ROOT_FOR_TEST, log=quiet)


def test_push_still_fast_forwards_the_site_and_refuses_divergence():
    import inspect
    src = inspect.getsource(p.publish)
    assert '"pull", "--ff-only", "origin", "main"' in src and "cannot fast-forward" in src


def test_default_is_dry_and_cli_refuses_push_without_env(env, monkeypatch, capsys):
    site, src, _, site_origin = env
    monkeypatch.delenv(p.APPROVAL_ENV, raising=False)
    before = git(site_origin, "rev-parse", "main")
    assert p.main(["--site", str(site), "--source", str(src), "--push"]) == 2
    assert "REFUSED" in capsys.readouterr().out and git(site_origin, "rev-parse", "main") == before
    assert p.main(["--site", str(site), "--source", str(src), "--receipts", str(site.parent / "none")]) == 0
    assert "DRY" in capsys.readouterr().out


def test_autopublish_cmd_uses_a_throwaway_origin_main_worktree_and_is_dry_by_default():
    cmd = (ROOT_FOR_TEST / "tools" / "tree-autopublish.cmd").read_text(encoding="utf-8")
    assert "worktree add --detach" in cmd and "origin/main" in cmd and "worktree remove" in cmd
    assert "BOSSMAN_OWNER_APPROVED_PUBLISH" in cmd and 'set "PUSHFLAG="' in cmd           # --push only when approved
    assert "wt-main-prod" not in cmd and "claude/main-prod" not in cmd and "\ncd " not in cmd   # no stale checkout, no cd into one
