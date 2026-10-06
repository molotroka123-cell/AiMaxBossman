"""CLI = thin client of the SAME HTTP backend the Bossman page uses (``serve`` runs that backend)."""
from __future__ import annotations

import argparse
import json
import os
import sys

DEFAULT_PORT = int(os.environ.get("APP_PORT") or os.environ.get("PORT") or os.environ.get("POKERVISION_PORT") or 8931)


def _call(method: str, path: str, body: dict | None = None, port: int = DEFAULT_PORT):
    import httpx
    headers = {"Authorization": f"Bearer {os.environ['POKERVISION_TOKEN']}"} if os.environ.get("POKERVISION_TOKEN") else {}
    r = httpx.request(method, f"http://127.0.0.1:{port}{path}", json=body, headers=headers, timeout=60, trust_env=False)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"raw": r.text[:200]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="poker-vision")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the backend (loopback only)")
    sub.add_parser("status"); sub.add_parser("state"); sub.add_parser("history"); sub.add_parser("stop"); sub.add_parser("capabilities")
    r = sub.add_parser("replay"); r.add_argument("path"); r.add_argument("--adapter", default="poker_train")
    t = sub.add_parser("trainer"); t.add_argument("--url", default="http://127.0.0.1:3000/"); t.add_argument("--act", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "serve":
        os.environ["POKERVISION_PORT"] = str(a.port)
        from .api import main as serve
        sys.argv = [sys.argv[0], "--port", str(a.port)]
        serve()
        return 0
    if a.cmd == "replay":
        code, out = _call("POST", "/api/v1/session", {"mode": "replay", "adapter": a.adapter, "path": a.path}, a.port)
    elif a.cmd == "trainer":
        code, out = _call("POST", "/api/v1/session", {"mode": "trainer", "url": a.url, "act": a.act}, a.port)
    elif a.cmd == "stop":
        code, out = _call("POST", "/api/v1/stop", None, a.port)
    else:
        path = {"status": "/api/v1/status", "state": "/api/v1/state", "history": "/api/v1/history", "capabilities": "/api/v1/capabilities"}[a.cmd]
        code, out = _call("GET", path, None, a.port)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if code < 400 else 1


if __name__ == "__main__":
    raise SystemExit(main())
