"""tools/earning_emulator.py: the CLI wrapper (HTML to text, refusals, exit codes)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("earning_emulator_cli", ROOT / "tools" / "earning_emulator.py")
cli = importlib.util.module_from_spec(spec)
sys.modules["earning_emulator_cli"] = cli
spec.loader.exec_module(cli)

PAGE = "<html><head><style>x{}</style><script>alert(1)</script></head><body><h1>Remote QA Tester</h1><p>Test our app for 10 hours. $20/hr.<br>Write bug reports &amp; cases.</p></body></html>"


def test_html_to_text_drops_scripts_styles_and_tags():
    t = cli.html_to_text(PAGE)
    assert "alert" not in t and "x{}" not in t and "<" not in t
    assert "Remote QA Tester" in t and "bug reports & cases" in t


def test_cli_writes_a_simulated_record_and_returns_0(tmp_path, capsys):
    f = tmp_path / "l.txt"
    f.write_text("Remote QA Tester\nTest our web app for 10 hours of testing, $20/hr. Write bug reports.", encoding="utf-8")
    led = tmp_path / "led.jsonl"
    assert cli.main(["--listing-file", str(f), "--ledger", str(led)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["SIMULATED"] is True and out["real_money"] == 0 and out["invoice"]["amount"] == 200.0
    assert len(led.read_text(encoding="utf-8").splitlines()) == 1


def test_cli_returns_2_for_a_refused_listing_and_3_for_a_non_free_worker(tmp_path, capsys):
    f = tmp_path / "l.txt"
    f.write_text("Remote helper anywhere, send us money first, $20/hr.", encoding="utf-8")
    led = tmp_path / "led.jsonl"
    assert cli.main(["--listing-file", str(f), "--ledger", str(led)]) == 2
    assert cli.main(["--listing-file", str(f), "--ledger", str(led), "--worker", "gpt-5"]) == 3
    assert cli.main(["--listing-file", str(f), "--ledger", str(led), "--worker", "openrouter-free"]) == 3     # free, but not wired in this build
    assert "REFUSED" in capsys.readouterr().out


def test_fetch_accepts_only_http_urls():
    import pytest
    for bad in ("file:///etc/passwd", "ftp://x/y", "javascript:alert(1)"):
        with pytest.raises(ValueError):
            cli.fetch(bad)
