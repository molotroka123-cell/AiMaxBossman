"""Owner task #982 (01.11, AUTO, DeepSeek): 26 terminal calls succeeded, one early `cd <dir> && <read>` exited 128 and the
whole 200 s run failed with «effectful terminal outcome is failed or still unobserved» - `cd` was unknown, so the read
counted as a mutation. Synthetic commands only."""
from __future__ import annotations

from bcc.features.action_contract import _looks_like_mutation
from bcc.finalize import _effect_problem, _effectful


def _row(cmd: str, code: int, i: int) -> dict:
    return {"tool": "terminal.run", "args": {"command": cmd}, "args_hash": f"h{i}", "status": "executed",
            "result_preview": f"exit_code={code}\n..."}


def test_cd_then_a_read_is_a_read():
    for cmd in ("cd C:/w && git log -3", "cd w; ls", "pushd w && dir && popd", "Set-Location w; Get-ChildItem"):
        assert _looks_like_mutation(cmd) is False, cmd
        assert _effectful({"tool": "terminal.run", "args": {"command": cmd}}) is False, cmd


def test_cd_does_not_hide_a_mutation():
    for cmd in ("cd w && rm -rf build", "cd w && python fix.py", "cd w; echo x > f.txt", "cd w && git push"):
        assert _effectful({"tool": "terminal.run", "args": {"command": cmd}}) is True, cmd


def test_a_failed_cd_read_no_longer_fails_the_run():
    rows = [_row("cd C:/w && git log -3", 128, 1), _row("python make_report.py", 0, 2)]
    assert _effect_problem(rows, []) == ""
    # negative control: a FAILED mutation still fails it
    rows = [_row("cd C:/w && python make_report.py", 1, 1), _row("dir", 0, 2)]
    assert _effect_problem(rows, []) == "effectful terminal outcome is failed or still unobserved"


def test_stream_merges_and_discards_are_not_writes():
    for cmd in ("cd w && git status --short 2>&1 | head -20", "git log --oneline 2>/dev/null", "dir 2>nul",
                "Get-ChildItem 2>$null", "ls >/dev/null 2>&1", "git status >&2"):
        assert _effectful({"tool": "terminal.run", "args": {"command": cmd}}) is False, cmd
    for cmd in ("git log > log.txt", "echo x >> notes.md", "ls 2> err.txt", "cat a >/dev/nullx", "echo $HOME"):
        assert _effectful({"tool": "terminal.run", "args": {"command": cmd}}) is True, cmd
