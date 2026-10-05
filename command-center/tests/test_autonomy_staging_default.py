"""build_default_runner: a real clone of the candidate SHA served on a free port, then torn down.

The "candidate" is a tiny git repo whose ``bcc`` package only answers /health/live
and /health, so the test exercises clone + process group + readiness + teardown
without starting the real Command Center.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from bcc.autonomy.journal import Journal
from bcc.autonomy.staging import build_default_runner

FAKE_BCC = '''
import argparse, http.server, json, os
p = argparse.ArgumentParser(); p.add_argument("--host"); p.add_argument("--port", type=int)
a = p.parse_args()
open(os.path.join(os.environ["BCC_DATA_DIR"], "started"), "w").write(str(a.port))
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        code = 200 if self.path in ("/health/live", "/health") else 404
        body = json.dumps({"alive": True}).encode()
        self.send_response(code); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass
http.server.HTTPServer((a.host, a.port), H).serve_forever()
'''


def git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env=env).stdout.strip()


@pytest.fixture
def repos(tmp_path):
    repo = tmp_path / "repo"
    pkg = repo / "command-center" / "bcc"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "__main__.py").write_text(FAKE_BCC)
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    root = tmp_path / "autonomy"
    wt = root / "cycles" / "JEFF-0042" / "w1-a1" / "worktree"
    wt.parent.mkdir(parents=True)
    git(tmp_path, "clone", "-q", str(repo), str(wt))
    (wt / "README.md").write_text("candidate\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", "candidate")
    owner = tmp_path / "owner-data"
    owner.mkdir()
    return {"repo": repo, "root": root, "base": git(repo, "rev-parse", "HEAD"),
            "candidate": git(wt, "rev-parse", "HEAD"), "owner": owner}


def runner(r, **kw):
    return build_default_runner(r["root"], r["repo"], owner_data_dir=r["owner"], ready_timeout_s=60, **kw)


def test_candidate_only_in_a_cycle_worktree_is_staged_and_torn_down(repos):
    rep = runner(repos).run(repos["candidate"], ["health", "ready"])
    assert rep.ok, rep
    assert rep.checks["health"]["ok"] and rep.checks["ready"]["ok"] and rep.torn_down
    assert rep.port not in (8800, 8801) and not Path(rep.data_dir).exists()
    assert list(repos["owner"].iterdir()) == []
    kinds = [e["kind"] for e in Journal(repos["root"]).entries()]
    assert kinds == ["staging.started", "staging.finished"]


def test_base_sha_from_the_source_repo(repos):
    assert runner(repos).run(repos["base"], ["health"]).ok


def test_unknown_sha_and_unprobed_checks_fail_closed(repos):
    rep = runner(repos).run("e" * 40, ["health"])
    assert not rep.ok and "no repository or cycle worktree contains" in rep.reason
    rep = runner(repos).run(repos["candidate"], ["health", "jeff_identity"])
    assert not rep.ok and "jeff_identity" in rep.reason


def test_owner_data_dir_as_temp_root_is_refused(repos, tmp_path):
    r = runner(repos)
    r.tmp_root = repos["owner"] / "tmp"
    rep = r.run(repos["candidate"], ["health"])
    assert not rep.ok and "owner data dir" in rep.reason and list(repos["owner"].iterdir()) == []
