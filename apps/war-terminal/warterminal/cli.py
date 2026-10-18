"""CLI: python -m warterminal [--port 8830] [--data DIR]. Binds to loopback only. Live public sources are the only mode (no demo mode exists)."""
from __future__ import annotations

import argparse
import os


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        import sys
        argv = sys.argv[1:]
    if argv and argv[0] == "fx":                       # `warterminal fx collect|report`: daily FX tracker, no server needed
        from .fx_tracker import main as fx_main
        return fx_main(argv[1:])
    ap = argparse.ArgumentParser(prog="warterminal",description="Bossman War & Procurement Terminal (loopback only)")
    ap.add_argument("--port", type=int, default=8830)
    ap.add_argument("--data", default=None, help="data dir (default ~/.war-terminal or WAR_TERMINAL_DATA)")
    ap.add_argument("--live", action="store_true", help="kept for old launchers: live reading of public sources is now always on")
    args = ap.parse_args(argv)
    if args.data:
        os.environ["WAR_TERMINAL_DATA"] = args.data
    os.environ.setdefault("WAR_TERMINAL_LIVE", "true")      # WAR_TERMINAL_LIVE=0 in the environment remains the emergency network kill switch
    import uvicorn
    from .api import create_app
    app = create_app()
    print(f"[war-terminal] http://127.0.0.1:{args.port}/  tokens: {app.state.store.path.replace('war.db', 'tokens.json')}  live={app.state.live}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    return 0
