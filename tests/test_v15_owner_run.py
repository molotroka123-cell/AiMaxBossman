from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def load():
    path = ROOT / "tools" / "bossman_15_owner_run.py"
    spec = importlib.util.spec_from_file_location("v15_owner_run_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_unified_start_runs_market_even_if_self_improve_repo_is_owner_required(tmp_path, monkeypatch):
    m = load()
    pids = iter([101, 102])
    monkeypatch.setattr(m, "_spawn", lambda *a, **k: next(pids))
    monkeypatch.setattr(m, "_alive", lambda pid: bool(pid))
    data = tmp_path / "data"
    root = data / "v1.5" / "owner-run"
    out = m.start(root, data_dir=data, repo=None, cycles=8, allow_glm=False,
                  youtube_url="", cadence=15)
    assert out["status"] == "PARTIAL_OWNER_REQUIRED"
    assert out["processes"]["market"]["alive"] is True
    assert out["processes"]["self_improve"]["pid"] is None
    assert "OWNER_REQUIRED_REPO" in out["self_improve_blocker"]
    assert out["policy"]["market_trading_execution"] == "OFF"
    assert out["policy"]["stable_write"] is False


def test_unified_start_launches_both_lanes_and_stop_is_durable(tmp_path, monkeypatch):
    m = load()
    repo = tmp_path / "repo"; (repo / ".git").mkdir(parents=True)
    seen = []
    def spawn(argv, log, cwd=None):
        seen.append((argv, log, cwd)); return 200 + len(seen)
    monkeypatch.setattr(m, "_spawn", spawn)
    monkeypatch.setattr(m, "_alive", lambda pid: bool(pid))
    monkeypatch.setattr(m, "_support", lambda name: tmp_path / name)
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: type("P", (), {"returncode": 0})())

    data = tmp_path / "data"; root = data / "v1.5" / "owner-run"
    out = m.start(root, data_dir=data, repo=repo, cycles=3, allow_glm=True,
                  youtube_url="https://youtube.test/channel", cadence=15)
    assert out["status"] == "RUNNING"
    assert len(seen) == 2
    assert "bcc.market.collector" in " ".join(seen[0][0])
    assert seen[0][2] == ROOT / "command-center"
    joined = " ".join(seen[1][0])
    assert "bossman_15_self_improve.py" in joined and "--cycles 3" in joined and "--allow-glm" in joined

    stopped = m.stop(root)
    assert (root / "STOP").is_file()
    assert Path(out["market_root"]).joinpath("STOP").is_file()
    assert stopped["status"] == "STOP_REQUESTED"


def test_windows_bundle_declares_one_api_backed_v15_launcher_and_runner():
    text = (ROOT / "tools" / "build_windows_bundle.py").read_text(encoding="utf-8")
    assert "bossman_15_owner_run.py" in text
    assert "bossman_15_owner_ctl.py" in text
    assert text.count('"Bossman-1.5.cmd"') == 1
    assert '"Bossman-1.5.cmd": V15_OWNER_CMD' in text
    assert "V15_CMD =" not in text
