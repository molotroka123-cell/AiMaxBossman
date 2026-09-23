"""Bossman 1.2 terminal client — another control surface of the SAME Bossman.

`bossman chat` (human, one scrollable column), `bossman exec` / `bossman -p`
(machine contract for Claude Code and scripts), and small scriptable commands
(status, events, result, approve, deny, stop, keys, code, …). All of them talk
to the running Command Center API; nothing here learns, plans, stores memory or
decides approvals. Entry point: `bossman` (bossman-core's CLI dispatches the
terminal commands here) or `python -m bcc.terminal_cli`.
"""
from __future__ import annotations

CLIENT_VERSION = "1.2.0"


def main(argv: list[str] | None = None) -> int:
    from .cli import main as _main
    return _main(argv)
