"""Windows launchers of the Bossman 1.2 terminal, for the one-download archive.

Templates only: tools/build_windows_bundle.py does not import this module yet.
To ship them, merge TERMINAL_LAUNCHERS into `launcher_files()` there (the
builder writes them with CRLF line ends, like the other launchers).

* Bossman-CLI.cmd       — opens `bossman chat` (the owner's conversation window)
* Bossman-Terminal.cmd  — the same, under the name used in the owner's notes
* bossman.cmd           — pass-through for Claude Code / scripts:
                          `bossman.cmd -p "…" --output-format stream-json`,
                          `bossman.cmd exec --input-file task.json`

All three: the archive's own runtime (no system Python), UTF-8 code page, any
current directory, paths with spaces and Cyrillic (every path quoted). In
bossman.cmd the environment script's own messages go to stderr, so stdout of
a machine command stays JSON only.
"""
from __future__ import annotations

CLI_CMD = r"""@echo off
setlocal
rem Bossman 1.2 terminal: talk to the SAME Bossman in this window (bossman chat).
rem Tasks keep running in Bossman after this window is closed.
set "BOSSMAN_HOME=%~dp0"
call "%BOSSMAN_HOME%app-support\_env.cmd"
if errorlevel 1 exit /b 1
chcp 65001 >nul
"%BOSSMAN_HOME%runtime\python.exe" -m bossman.cli chat %*
exit /b %ERRORLEVEL%
"""

PASSTHROUGH_CMD = r"""@echo off
setlocal
rem Bossman 1.2 terminal for Claude Code and scripts: bossman.cmd -p "..." --output-format stream-json
rem stdout carries only the command's output (JSON/JSONL for machine commands).
set "BOSSMAN_HOME=%~dp0"
call "%BOSSMAN_HOME%app-support\_env.cmd" 1>&2
if errorlevel 1 exit /b 3
chcp 65001 >nul
"%BOSSMAN_HOME%runtime\python.exe" -m bossman.cli %*
exit /b %ERRORLEVEL%
"""

TERMINAL_LAUNCHERS: dict[str, str] = {
    "Bossman-CLI.cmd": CLI_CMD,
    "Bossman-Terminal.cmd": CLI_CMD,
    "bossman.cmd": PASSTHROUGH_CMD,
}
