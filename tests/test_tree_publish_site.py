"""tools/tree_publish_site.py with temporary local git repos (no network)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT_FOR_TEST = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_FOR_TEST / "tools"))
import tree_publish_site as p  # noqa: E402

STUB_SYNC = """import argparse, json
from pathlib import Path
ap = argparse.ArgumentParser()
ap.add_argument("--source"); ap.add_argument("--registry"); ap.add_argument("--out")
a = ap.parse_args()
assert Path(a.registry).name == "tree-registry.json"
out = Path(a.out); (out / "data").mkdir(exist_ok=True)
(out / "data" / "meta.json").write_text(json.dumps({"counts": {"by_status": {"working": 2, "reported": 5}},
    "source": {"head": "abc12345"}}), encoding="utf-8")
print("SYNC=OK")
"""
GOOD_TEST = "import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertTrue(True)\n"
BAD_TEST = "import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.fail('x')\n"


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *a],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def env(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
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
    git(site, "remote", "add", "origin", str(origin))
    git(site, "push", "-q", "origin", "main")
    src = tmp_path / "src"
    (src / "command-center" / "bcc").mkdir(parents=True)
    (src / p.EVID_REL).mkdir(parents=True)
    (src / p.SEED_REL).write_text(json.dumps({"as_of": "x", "nodes": [
        {"id": "root", "label": "R", "parent": None, "status": "mixed", "detail": "", "sources": []}]}), encoding="utf-8")
    git(src, "init", "-q", "-b", "main")
    git(src, "add", "-A")
    git(src, "commit", "-q", "-m", "src")
    return site, src, origin


def head(repo):
    return git(repo, "rev-parse", "HEAD")


def quiet(*_):
    return None


def test_dry_run_regenerates_but_never_commits_or_pushes(env):
    site, src, origin = env
    src_head, site_head = head(src), head(site)
    res = p.publish(site, src, push=False, log=quiet)
    assert res["tests_ok"] and not res["pushed"] and res["committed"] is None
    assert res["by_status"] == {"working": 2, "reported": 5} and res["head"] == "abc12345"
    assert head(site) == site_head and head(src) == src_head
    assert (site / "data" / "meta.json").exists()
    assert git(origin, "rev-parse", "main") == site_head


def test_push_commits_only_generated_paths_and_pushes_main(env):
    site, src, origin = env
    res = p.publish(site, src, push=True, log=quiet)
    assert res["pushed"] and git(origin, "rev-parse", "main") == head(site) == res["committed"]
    msg = git(site, "log", "-1", "--format=%B")
    assert "working=2" in msg and "Claude-Session:" in msg
    assert git(site, "status", "--porcelain") == ""
    assert head(src) == git(src, "rev-parse", "main")


def test_push_refused_when_site_tests_fail(env):
    site, src, origin = env
    (site / "tests" / "test_ok.py").write_text(BAD_TEST, encoding="utf-8")
    git(site, "add", "-A")
    git(site, "commit", "-q", "-m", "break tests")
    before = git(origin, "rev-parse", "main")
    with pytest.raises(p.Refused, match="tests failed"):
        p.publish(site, src, push=True, log=quiet)
    assert git(origin, "rev-parse", "main") == before
    assert not git(site, "diff", "--cached", "--name-only")


def test_push_refused_with_unrelated_uncommitted_changes(env):
    site, src, origin = env
    (site / "scripts" / "sync_bossman.py").write_text(STUB_SYNC + "\n# local edit\n", encoding="utf-8")
    before = git(origin, "rev-parse", "main")
    with pytest.raises(p.Refused, match="unrelated uncommitted"):
        p.publish(site, src, push=True, log=quiet)
    assert git(origin, "rev-parse", "main") == before and head(site) == before


def test_push_refused_off_main(env):
    site, src, origin = env
    git(site, "checkout", "-q", "-b", "side")
    with pytest.raises(p.Refused, match="not main"):
        p.publish(site, src, push=True, log=quiet)


def test_generated_paths_and_pycache_do_not_count_as_unrelated(env):
    site, _, _ = env
    (site / "data").mkdir()
    (site / "data" / "x.json").write_text("{}", encoding="utf-8")
    (site / "tests" / "__pycache__").mkdir()
    (site / "tests" / "__pycache__" / "a.pyc").write_bytes(b"0")
    assert p.unrelated_changes(site) == []


def test_missing_site_clone_is_refused(tmp_path):
    with pytest.raises(p.Refused):
        p.publish(tmp_path / "nope", ROOT_FOR_TEST, push=False, log=quiet)
