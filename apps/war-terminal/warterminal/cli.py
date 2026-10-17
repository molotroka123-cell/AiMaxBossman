"""CLI: python -m warterminal [--port 8830] [--live] [--data DIR]. Binds to loopback only."""
from __future__ import annotations

import argparse
import os


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="warterminal", description="Bossman War & Procurement Terminal (loopback only)")
    ap.add_argument("--port", type=int, default=8830)
    ap.add_argument("--data", default=None, help="data dir (default ~/.war-terminal or WAR_TERMINAL_DATA)")
    ap.add_argument("--live", action="store_true", help="enable external sources (default: off)")
    args = ap.parse_args(argv)
    if args.data:
        os.environ["WAR_TERMINAL_DATA"] = args.data
    if args.live:
        os.environ["WAR_TERMINAL_LIVE"] = "true"
    import uvicorn
    from .api import create_app
    app = create_app()
    print(f"[war-terminal] http://127.0.0.1:{args.port}/  tokens: {app.state.store.path.replace('war.db', 'tokens.json')}  live={app.state.live}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    return 0
