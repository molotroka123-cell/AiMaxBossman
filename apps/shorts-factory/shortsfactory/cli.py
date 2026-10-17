"""CLI: python -m shortsfactory [--port 8860] [--data DIR]. Binds to loopback only.
All actions (register source, owner rights approval, jobs, approve/publish) go through the role-checked HTTP API."""
from __future__ import annotations

import argparse
import os


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="shortsfactory", description="Bossman Shorts Factory (loopback only)")
    ap.add_argument("--port", type=int, default=8860)
    ap.add_argument("--data", default=None, help="data dir (default ~/.shorts-factory or SHORTS_FACTORY_DATA)")
    args = ap.parse_args(argv)
    if args.data:
        os.environ["SHORTS_FACTORY_DATA"] = args.data
    import uvicorn
    from .api import create_app
    app = create_app()
    print(f"[shorts-factory] http://127.0.0.1:{args.port}/ui/  data={app.state.data_dir}  tokens={app.state.data_dir / 'tokens.json'}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    return 0
