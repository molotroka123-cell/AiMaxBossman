"""RC19 owner run: «Bossman Jeff» shortcut (pythonw, no console) exited 1 silently.

Under pythonw sys.stdout/sys.stderr are None. The Command Center window binds
them to a log file first; the Jeff launcher did not. Now it does.
"""
import sys

from bcc import jeff_desktop


def test_launcher_without_console_logs_to_the_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    code = jeff_desktop.main(["--data-dir", str(tmp_path), "--port", "18859", "--no-window"])
    log = tmp_path / "desktop-console.log"
    assert log.is_file(), "no console → output must go to a file in the data dir"
    text = log.read_text(encoding="utf-8")
    assert "запуск без консоли" in text and "[jeff]" in text
    assert code != 0          # unconfigured data dir: an explained refusal, not a crash
