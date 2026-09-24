#!/usr/bin/env python3
"""Owner CLI for Bossman 1.5.

Thin client of the authenticated Command Center API. It does not call
OpenRouter, Twitch or models directly.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "bossman-core", ROOT):
    if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

from bossman_v3.self_improvement.attempts import CommandCenterApi, resolve_token  # noqa: E402


def client(data_dir: str, url: str) -> CommandCenterApi:
    token = resolve_token(data_dir=data_dir)
    if not token:
        raise RuntimeError("Bossman token not found in data directory")
    return CommandCenterApi(url, token, timeout=60.0)


def show(code: int, body) -> int:
    print(json.dumps({"http": code, "body": body}, ensure_ascii=False, indent=2))
    if 200 <= code < 300:
        if isinstance(body, dict) and body.get("status") == "BLOCKED":
            return 3
        return 0
    return 2


def main(argv=None) -> int:
    default_data = str(Path(os.environ.get("LOCALAPPDATA", ".")) / "Bossman" / "CommandCenter")
    ap = argparse.ArgumentParser(prog="Bossman-1.5")
    ap.add_argument("--data-dir", default=default_data)
    ap.add_argument("--url", default="http://127.0.0.1:8800")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("quick-test")
    start = sub.add_parser("start")
    start.add_argument("--repo")
    start.add_argument("--cycles", type=int, default=8)
    start.add_argument("--allow-glm", action="store_true")
    start.add_argument("--cadence", type=float, default=15.0)
    start.add_argument("--youtube-url", default="")
    sub.add_parser("stop")
    ns = ap.parse_args(argv)

    try:
        c = client(ns.data_dir, ns.url)
    except (RuntimeError, ValueError) as exc:
        print(f"Bossman 1.5: {exc}", file=sys.stderr)
        return 3

    if ns.cmd == "status":
        code, body = c.get("/api/v15/owner-run/status")
    elif ns.cmd == "quick-test":
        code, body = c.post("/api/v15/owner-run/quick-test", {})
    elif ns.cmd == "stop":
        code, body = c.post("/api/v15/owner-run/stop", {})
    else:
        code, body = c.post("/api/v15/owner-run/start", {
            "repo": ns.repo,
            "cycles": max(1, min(int(ns.cycles), 20)),
            "allow_glm": bool(ns.allow_glm),
            "youtube_url": ns.youtube_url,
            "cadence": max(5.0, min(float(ns.cadence), 120.0)),
        })
    return show(code, body)


if __name__ == "__main__":
    raise SystemExit(main())
