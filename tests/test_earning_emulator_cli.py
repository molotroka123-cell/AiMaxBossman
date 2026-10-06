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


def test_unreadable_input_is_a_clear_error_not_a_traceback(tmp_path, capsys):
    """Missing/undecodable listing, a non-http --fetch URL and an unwritable ledger exit 4 with a message."""
    led = tmp_path / "led.jsonl"
    binary = tmp_path / "bin.txt"
    binary.write_bytes(b"\xff\xfe\x00not utf-8")
    good = tmp_path / "l.txt"
    good.write_text("Remote QA Tester\nTest our web app for 10 hours of testing, $20/hr. Write bug reports.", encoding="utf-8")
    cases = [
        ["--listing-file", str(tmp_path / "missing.txt"), "--ledger", str(led)],
        ["--listing-file", str(binary), "--ledger", str(led)],
        ["--listing-file", str(tmp_path), "--ledger", str(led)],
        ["--fetch", "file:///etc/passwd", "--ledger", str(led)],
        ["--listing-file", str(good), "--ledger", str(tmp_path)],          # the ledger path is a directory
    ]
    for argv in cases:
        assert cli.main(argv) == 4, argv
        err = capsys.readouterr().err
        assert err.startswith("ERROR:"), (argv, err)
    assert not led.exists()


def test_a_network_failure_on_fetch_is_a_clear_error(tmp_path, capsys, monkeypatch):
    import urllib.error

    def offline(url):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(cli, "fetch", offline)
    assert cli.main(["--fetch", "https://example.invalid/job", "--ledger", str(tmp_path / "led.jsonl")]) == 4
    assert capsys.readouterr().err.startswith("ERROR:")
