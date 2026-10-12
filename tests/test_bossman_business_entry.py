"""Reports / Operations manual import entry point, from a checkout and as the bundle ships it."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "exchange_volume" / "fixtures" / "SYNTHETIC_export.html"


def _run(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", "-X", "utf8", str(script), *args],
                          capture_output=True, text=True, timeout=120)


def test_the_entry_point_runs_import_and_report_from_a_checkout(tmp_path):
    script = ROOT / "tools" / "bossman_business.py"
    assert _run(script).returncode == 2                                  # no tool named: usage, not a traceback
    out = tmp_path / "res"
    done = _run(script, "exchange", "run", "--export", str(FIXTURE), "--period", "business", "--out", str(out))
    assert done.returncode == 0, done.stderr
    done = _run(script, "report", "--result", str(out / "volume_result.json"), "--deals", str(out / "deals.csv"),
                "--out", str(out))
    assert done.returncode == 0, done.stderr
    assert sorted(p.name for p in out.iterdir()) == ["deals.csv", "disputed.csv", "report.md", "volume_result.json"]
    text = (out / "report.md").read_text(encoding="utf-8")
    assert "Это не прибыль и не доход" in text and "неполный период" in text    # the synthetic export starts mid-period


def test_the_builder_ships_the_tools_where_the_entry_point_looks_for_them(tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import build_windows_bundle as bundle
    finally:
        sys.path.remove(str(ROOT / "tools"))
    support = tmp_path / "app-support"
    support.mkdir()
    shipped = bundle.install_business_tools(support)
    assert shipped["kind"] == "manual_import"
    assert (support / "business_tools" / "exchange_volume" / "cli.py").is_file()
    assert (support / "business_tools" / "ops_intake" / "cli.py").is_file()
    assert any(name == "bossman_business.py" for _, name in bundle.SUPPORT_SCRIPTS)
    assert "Reports-Import.cmd" in bundle.launcher_files()
    # as the bundle lays it out: the entry beside business_tools/, run with the isolated interpreter
    shutil.copyfile(ROOT / "tools" / "bossman_business.py", support / "bossman_business.py")
    out = tmp_path / "installed"
    done = _run(support / "bossman_business.py", "exchange", "run", "--export", str(FIXTURE), "--period", "business",
                "--out", str(out))
    assert done.returncode == 0, done.stderr
    assert (out / "volume_result.json").is_file()
