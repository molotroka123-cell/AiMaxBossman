"""CLI: python -m bossmobile [serve] [--port 8840] | pairing | rotate. Binds to loopback only."""
from __future__ import annotations

import argparse
import ipaddress
import sys


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bossmobile", description="Bossman Mobile gateway (loopback only)")
    ap.add_argument("command", nargs="?", default="serve", choices=["serve", "pairing", "rotate"])
    ap.add_argument("--port", type=int, default=8840)
    ap.add_argument("--host", default="127.0.0.1", help="loopback address only; the phone comes via `tailscale serve`")
    ap.add_argument("--url", default="", help="public tailnet URL to print in the pairing link")
    args = ap.parse_args(argv)
    from .auth import load_or_create_pairing, rotate_pairing
    from .config import Settings
    st = Settings()
    if args.command == "pairing":
        tok = load_or_create_pairing(st.data_dir)
        base = (args.url or f"http://127.0.0.1:{args.port}").rstrip("/")
        print(f"pairing token: {tok}")
        print(f"pairing link (fragment is never sent to the server): {base}/m/#pair={tok}")
        return 0
    if args.command == "rotate":
        rotate_pairing(st.data_dir)
        print(f"new pairing token written to {st.data_dir / 'tokens.json'}; all phone sessions revoked "
              "(restart the server to apply)")
        return 0
    if not _loopback(args.host):
        print("refused: Bossman Mobile binds to loopback only; expose it with `tailscale serve` (never funnel)",
              file=sys.stderr)
        return 2
    import uvicorn
    from .api import create_app
    app = create_app(st)
    print(f"[bossman-mobile] http://{args.host}:{args.port}/m/  bossman={st.bossman_url}  "
          f"pairing token file: {st.data_dir / 'tokens.json'}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info", proxy_headers=False, ws="websockets")
    return 0
