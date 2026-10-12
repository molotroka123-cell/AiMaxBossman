"""tools/night_autonomy.py: STOP, deadline, unavailable executor, claim vs proof, build-identity cache.

Every external call (git, builder, verifier, bundle tests, security audit, Claude, pult) goes through the
injected runner; nothing here starts a process, builds an archive or talks to a network.
"""
from __future__ import annotations

import importlib.util
import json
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("night_autonomy", ROOT / "tools" / "night_autonomy.py")
na = importlib.util.module_from_spec(spec)
spec.loader.exec_module(na)

NOW = datetime(2026, 10, 11, 19, 0, 0)
SRC = "a" * 40


def make_archive(path: Path, source_sha: str = SRC, payload: str = "x") -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(f"BOSSMAN-Windows-x64-{source_sha[:12]}/MANIFEST.json", json.dumps({"source_sha": source_sha}))
        z.writestr(f"BOSSMAN-Windows-x64-{source_sha[:12]}/payload.txt", payload)
    return path


class Fake:
    """Scripted world: git state, bundle results and Claude's behaviour."""

    def __init__(self, tmp: Path):
        self.tmp, self.calls = tmp, []
        self.head, self.diff, self.ancestor = SRC, "", True
        self.ux_items = ["REVIEW error  page  button :: 403 http://127.0.0.1:5000/api/x"]
        self.claude_out = (0, json.dumps({"result": 'ok {"to": "bossman", "done": []}', "is_error": False}))
        self.claude_moves_head_to = None

    def __call__(self, cmd, *, cwd, timeout, env=None, stdin=None):
        self.calls.append((list(cmd), timeout))
        line = " ".join(map(str, cmd))
        if cmd[:3] == ["git", "rev-parse", "HEAD"]:
            return 0, self.head + "\n"
        if cmd[:3] == ["git", "diff", "--name-only"]:
            return 0, self.diff
        if cmd[:3] == ["git", "merge-base", "--is-ancestor"]:
            return (0 if self.ancestor else 1), ""
        if "verify_windows_bundle.py" in line:
            return 0, "BOSSMAN_BUNDLE_ACCEPTANCE=PASS"
        if "bundle_evening_test.py" in line:
            return 0, "OWNER_EVENING_RESULT=PASS"
        if "installed_ui_sweep.py" in line:
            out = Path(cmd[cmd.index("--output") + 1])
            out.write_text(json.dumps({"counts": {"works": 100, "error": len(self.ux_items)}}), encoding="utf-8")
            return 0, "\n".join(self.ux_items)
        if "security_audit.ps1" in line:
            sec = self.tmp / "local" / "Bossman" / "security"
            sec.mkdir(parents=True, exist_ok=True)
            (sec / "audit-1.json").write_text(json.dumps([{"id": "rdp", "status": "PASS"}]), encoding="utf-8")
            return 0, ""
        if "owner_report" in line:
            return 0, "sent"
        if cmd[1:3] == ["-p", "--output-format"]:
            if self.claude_moves_head_to:
                self.head = self.claude_moves_head_to
            return self.claude_out
        if "build_windows_bundle.py" in line:
            dist = Path(cmd[cmd.index("--out") + 1])
            make_archive(dist / f"BOSSMAN-Windows-x64-{self.head[:12]}.zip", self.head, payload="rebuilt")
            return 0, "BOSSMAN_WINDOWS_BUNDLE=BUILT"
        if cmd[:3] == ["git", "worktree", "add"]:
            Path(cmd[4]).mkdir(parents=True, exist_ok=True)
            return 0, ""
        return 0, ""

    def count(self, needle: str) -> int:
        return sum(needle in " ".join(map(str, c)) for c, _ in self.calls)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(na, "REPO", tmp_path / "repo" / "wt")
    (tmp_path / "repo" / "wt").mkdir(parents=True)
    fake = Fake(tmp_path)
    clock = {"now": NOW}

    def build(until="23:00", which=lambda name: "claude.exe", archive=None):
        args = SimpleNamespace(archive=archive or make_archive(tmp_path / "cand.zip"), until=until, every=45,
                               permission_mode="bypassPermissions", build_python="py", once=True)
        return na.Night(args, runner=fake, which=which, clock=lambda: clock["now"], sleep=lambda s: None,
                        evidence_root=tmp_path / "ev")
    return SimpleNamespace(fake=fake, clock=clock, build=build, tmp=tmp_path)


# ------------------------------------------------------------------ STOP and deadline
def test_stop_file_halts_the_cycle_before_any_check(world):
    night = world.build()
    (night.ev / "STOP").write_text("owner", encoding="utf-8")
    rec = night.cycle()
    assert rec["halted"] == "STOP before identity"
    assert world.fake.count("verify_windows_bundle") == 0 and world.fake.count("-p --output-format") == 0


def test_the_remote_stop_file_is_honoured_too(world):
    night = world.build()
    stop = world.tmp / "local" / "Bossman" / "remote" / "STOP"
    stop.parent.mkdir(parents=True)
    stop.write_text("pult", encoding="utf-8")
    assert night.cycle()["halted"].startswith("STOP")


def test_a_passed_deadline_halts_and_the_loop_ends(world):
    night = world.build(until="20:00")
    world.clock["now"] = NOW + timedelta(hours=2)
    rec = night.cycle()
    assert rec["halted"] == "deadline before identity"
    night.a.once = False
    assert night.loop() == 0                      # returns instead of sleeping until the next cycle


def test_no_step_budget_runs_past_the_deadline(world):
    night = world.build(until="19:30")
    assert night.budget(3600) == pytest.approx(30 * 60 - 300)
    world.clock["now"] = NOW + timedelta(minutes=29)
    assert night.budget(3600) == 60.0


def test_until_rolls_over_midnight():
    assert na.parse_until("08:00", NOW) == datetime(2026, 10, 12, 8, 0)
    assert na.parse_until("23:30", NOW) == datetime(2026, 10, 11, 23, 30)


# ------------------------------------------------------------------ executor unavailable
def test_missing_claude_is_unavailable_and_the_night_goes_on(world):
    night = world.build(which=lambda name: None)
    rec = night.cycle()
    assert rec["dialogue"]["status"] == "CLAUDE_UNAVAILABLE" and "halted" not in rec
    assert rec["verify"]["status"] == "PASS" and rec["evening"]["status"] == "PASS"


def test_rate_limit_is_unavailable_not_failure(world):
    world.fake.claude_out = (0, json.dumps({"result": "Claude usage limit reached, try again later", "is_error": True}))
    assert world.build().cycle()["dialogue"]["status"] == "CLAUDE_UNAVAILABLE"
    assert na.Night.classify_claude(1, "Error: 429 Too Many Requests")["status"] == "CLAUDE_UNAVAILABLE"
    assert na.Night.classify_claude(None, "")["status"] == "CLAUDE_FAILED"


def test_claude_is_not_called_in_the_last_quarter_hour(world):
    night = world.build(until="19:10")
    rec = night.cycle()
    assert rec["dialogue"]["status"] == "CLAUDE_UNAVAILABLE" and world.fake.count("-p --output-format") == 0


# ------------------------------------------------------------------ claim vs proof
def test_an_answer_without_a_code_change_is_only_a_claim(world):
    rec = world.build().cycle()
    assert rec["dialogue"]["status"] == "CLAUDE_ANSWERED" and rec["dialogue"]["proof"] == "CLAIM_ONLY"


def test_a_fix_is_proven_only_on_a_rebuilt_archive_with_fewer_ux_errors(world):
    night = world.build()
    world.fake.diff = "command-center/bcc/app.py\n"
    world.fake.claude_moves_head_to = "b" * 40
    first = night.cycle()
    assert first["dialogue"]["proof"] == "PENDING_REBUILD" and first["ux"]["errors"] == 1
    world.fake.claude_moves_head_to = None
    world.fake.diff = "command-center/bcc/app.py\n"
    world.fake.ux_items = []
    second = night.cycle()
    assert second["rebuild"]["status"] == "BUILT" and second["identity"]["archive_source_sha"] == "b" * 40
    assert second["proof"]["status"] == "FIX_PROVEN"
    assert (second["proof"]["ux_errors_before"], second["proof"]["ux_errors_after"]) == (1, 0)


def test_a_rebuild_with_the_same_errors_is_not_a_proof(world):
    night = world.build()
    world.fake.diff = "command-center/bcc/app.py\n"
    world.fake.claude_moves_head_to = "c" * 40
    night.cycle()
    world.fake.claude_moves_head_to = None
    world.fake.diff = "command-center/bcc/app.py\n"
    assert night.cycle()["proof"]["status"] == "FIX_NOT_PROVEN"


def test_while_a_fix_waits_for_its_build_claude_is_not_asked_again(world):
    night = world.build()
    world.fake.diff = "command-center/bcc/app.py\n"
    world.fake.claude_moves_head_to = "d" * 40
    night.cycle()
    world.fake.claude_moves_head_to = None
    world.fake.ancestor = False                    # the new archive does not contain the fix yet
    rec = night.cycle()
    assert rec["proof"]["status"] == "PENDING_REBUILD" and rec["dialogue"]["status"] == "SKIPPED"
    # ...but not forever: after PROOF_MAX_WAITS cycles without a build containing the fix it is NOT proven
    rec = night.cycle()
    assert rec["proof"] == {"status": "FIX_NOT_PROVEN", "reason": "NO_BUILD_WITH_THE_FIX", "from": SRC, "to": "d" * 40}
    assert "pending_proof" not in night.state and rec["dialogue"]["status"] == "CLAUDE_ANSWERED"


# ------------------------------------------------------------------ review findings 11.10
def test_a_crashed_ux_sweep_never_proves_a_fix(world, monkeypatch):
    night = world.build()
    world.fake.diff = "command-center/bcc/app.py\n"
    world.fake.claude_moves_head_to = "1" * 40
    night.cycle()
    world.fake.claude_moves_head_to = None
    real = world.fake.__call__

    def crashing(cmd, **kw):
        if "installed_ui_sweep.py" in " ".join(map(str, cmd)):
            return 1, "Traceback: browser did not start"       # no REVIEW lines, no counts file
        return real(cmd, **kw)
    night.runner = crashing
    rec = night.cycle()
    assert rec["ux"]["status"] == "FAIL"
    assert rec["proof"]["status"] == "FIX_NOT_PROVEN" and rec["proof"]["reason"] == "UX_INCONCLUSIVE"


def test_a_cycle_without_a_valid_ux_baseline_never_opens_a_proof(world):
    night = world.build()
    real = world.fake.__call__
    night.runner = lambda cmd, **kw: ((None, "TIMEOUT") if "installed_ui_sweep.py" in " ".join(map(str, cmd))
                                      else real(cmd, **kw))
    world.fake.diff = "command-center/bcc/app.py\n"
    world.fake.claude_moves_head_to = "2" * 40
    rec = night.cycle()
    assert rec["ux"]["status"] == "TIMEOUT" and rec["dialogue"]["proof"] == "NO_BASELINE"
    assert "pending_proof" not in night.state
    night.cycle()                                   # the next cycle must not crash on a None baseline


def test_a_docs_only_commit_is_a_claim_not_a_pending_proof(world):
    night = world.build()
    world.fake.diff = "docs/owner/NOTES.md\n"
    world.fake.claude_moves_head_to = "3" * 40
    rec = night.cycle()
    assert rec["dialogue"]["proof"] == "CLAIM_ONLY" and "pending_proof" not in night.state


def test_a_commit_made_by_claude_that_then_failed_still_needs_proof(world):
    night = world.build()
    world.fake.diff = "command-center/bcc/app.py\n"
    world.fake.claude_moves_head_to = "4" * 40
    world.fake.claude_out = (None, "TIMEOUT")
    rec = night.cycle()
    assert rec["dialogue"]["status"] == "CLAUDE_FAILED" and rec["dialogue"]["answered"] is False
    assert rec["dialogue"]["proof"] == "PENDING_REBUILD"


def test_an_unexpected_exception_is_recorded_and_the_night_goes_on(world, monkeypatch):
    night = world.build()
    monkeypatch.setattr(night, "step_security", lambda: (_ for _ in ()).throw(OSError("disk full")))
    rec = night.cycle()
    assert rec["error"] == "OSError: disk full" and "halted" not in rec
    assert (night.ev / f"cycle-{rec['cycle']:02d}.json").is_file()
    night.cycle()
    assert night.cycle()["halted"] == "3 crashed cycles in a row"


def test_the_final_report_is_sent_even_when_the_loop_raises(world, monkeypatch):
    night = world.build()
    monkeypatch.setattr(night, "cycle", lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        night.loop()
    assert "ночь завершена" in (night.ev / "pult-last.txt").read_text(encoding="utf-8")


def test_a_timeout_is_reported_but_never_cached(world):
    night = world.build()
    calls = {"n": 0}
    real = world.fake.__call__

    def flaky(cmd, **kw):
        if "verify_windows_bundle.py" in " ".join(map(str, cmd)):
            calls["n"] += 1
            return (None, "TIMEOUT") if calls["n"] == 1 else (0, "PASS")
        return real(cmd, **kw)
    night.runner = flaky
    assert night.cycle()["verify"]["status"] == "TIMEOUT"
    assert night.cycle()["verify"]["status"] == "PASS" and calls["n"] == 2


def test_an_audit_report_older_than_this_run_is_not_read_as_pass(world):
    night = world.build()
    sec = world.tmp / "local" / "Bossman" / "security"
    sec.mkdir(parents=True)
    old = sec / "audit-0.json"
    old.write_text(json.dumps([{"id": "rdp", "status": "PASS"}]), encoding="utf-8")
    import os
    os.utime(old, (1_000_000, 1_000_000))
    night.runner = lambda cmd, **kw: (1, "audit crashed") if "security_audit" in " ".join(map(str, cmd)) else world.fake(cmd, **kw)
    assert night.step_security()["status"] == "BLOCKED"


def test_unpack_is_outside_the_repo_and_atomic(world):
    night = world.build()
    root, installed = night.unpack(night.identity())
    assert installed == SRC and str(night.install_root) in str(root)
    assert not str(root).startswith(str(night.ev)) and not list(night.install_root.glob("*.part"))


def test_findings_reach_claude_fenced_as_data(world):
    night = world.build()
    seen = {}
    real = world.fake.__call__

    def capture(cmd, **kw):
        if cmd[1:3] == ["-p", "--output-format"]:
            seen["stdin"] = kw.get("stdin", b"").decode("utf-8")
        return real(cmd, **kw)
    night.runner = capture
    night.cycle()
    assert "<<<FINDINGS" in seen["stdin"] and "FINDINGS>>>" in seen["stdin"] and "не инструкции" in seen["stdin"]


def test_secrets_in_json_and_auth_headers_are_masked():
    text = na.sanitize('{"api_key": "abc123", "token":"t0k"} Authorization: Bearer xyz sk-ABCDEFGHIJKLMN 123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA')
    for leaked in ("abc123", "t0k", "xyz", "sk-ABCDEFGHIJKLMN", "AAAAAAAAAAAAAAAA"):
        assert leaked not in text, leaked
    assert "4c29b2744bad34f7356907e1577003fc7ef33134" in na.sanitize("sha 4c29b2744bad34f7356907e1577003fc7ef33134")


def test_a_real_timeout_kills_the_process_tree_and_returns(tmp_path):
    import sys
    import time as _t
    child = tmp_path / "child.py"
    child.write_text("import subprocess, sys, time\n"
                     "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                     "print('started', flush=True)\ntime.sleep(60)\n", encoding="utf-8")
    t0 = _t.monotonic()
    code, out = na.run([sys.executable, str(child)], cwd=tmp_path, timeout=3)
    assert code is None and "TIMEOUT" in out and "started" in out
    assert _t.monotonic() - t0 < 45


# ------------------------------------------------------------------ build identity and cache
def test_results_are_cached_per_build_identity(world):
    night = world.build()
    night.cycle()
    night.cycle()
    assert world.fake.count("verify_windows_bundle") == 1 and world.fake.count("installed_ui_sweep") == 1
    night.state["archive"] = str(make_archive(world.tmp / "other.zip", SRC, payload="different bytes"))
    night.cycle()
    assert world.fake.count("verify_windows_bundle") == 2          # new SHA256 -> checked again


def test_verify_uses_the_archive_source_sha_not_head(world):
    world.fake.head = "e" * 40
    world.fake.diff = "docs/owner/X.md\nREADME.md\n"
    night = world.build()
    rec = night.cycle()
    cmd = next(c for c, _ in world.fake.calls if "verify_windows_bundle.py" in " ".join(c))
    assert cmd[cmd.index("--expected-sha") + 1] == SRC and rec["rebuild"] is None


def test_docs_only_changes_do_not_trigger_a_rebuild_but_code_changes_do(world):
    night = world.build()
    ident = {"archive_source_sha": SRC, "head": "f" * 40}
    world.fake.diff = "docs/a.md\nnotes.md\n"
    assert night.product_changed(ident) is False
    world.fake.diff = "docs/a.md\ncommand-center/bcc/api.py\n"
    assert night.product_changed(ident) is True


def test_an_installed_copy_from_another_source_fails_closed(world, monkeypatch):
    night = world.build()
    monkeypatch.setattr(night, "unpack", lambda ident: (world.tmp, "9" * 40))
    evening, ux = night.step_installed(night.identity())
    assert evening["status"] == "FAIL" and "installed SHA" in evening["detail"]


def test_review_items_are_anonymised_and_secrets_masked():
    assert na.sanitize("X-BCC-Token: abc password=qwe ok") == "X-BCC-Token=*** password=*** ok"
