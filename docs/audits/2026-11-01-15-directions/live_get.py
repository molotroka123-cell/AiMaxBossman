"""Read-only GET probe of the live backend (15-direction matrix, 01.11.2026).

Only GET. Prints HTTP status, top-level keys and whitelisted scalar fields (status/enabled/paused/counters).
Never prints chat/turn contents, personal data, tokens or keys. Token is read the same way as
tools/prove_chat_attachments.py api() and is never printed.

Usage: python -X utf8 docs/audits/2026-11-01-15-directions/live_get.py /health/live /api/models ...
"""
import datetime
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8801"


def _token() -> str:
    d = Path(os.environ.get("BCC_DATA_DIR") or Path(os.environ["LOCALAPPDATA"]) / "Bossman" / "CommandCenter")
    return (d / "token").read_text(encoding="utf-8").strip()


SAFE = {"status", "state", "enabled", "paused", "available", "ok", "ready", "count", "total", "mode", "verdict",
        "running", "active", "level", "configured", "healthy", "n", "queued", "alive", "reason", "code",
        "version", "build_sha_short", "source_identity", "phase", "allowed", "live", "dry_run", "broadcast", "kill_switch", "stop", "kind"}


def summarize(o, depth=0):
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            kl = str(k).lower()
            if isinstance(v, (bool, int, float)) or v is None:  # counters/flags only - never text
                out[k] = v
            elif isinstance(v, str):
                if kl in SAFE or kl.endswith(("_count", "count", "_total", "enabled", "paused", "status", "_ok",
                                              "_usd", "_at", "_sha")):
                    out[k] = v[:80] if isinstance(v, str) else v
                else:
                    out[k] = type(v).__name__
            elif isinstance(v, list):
                out[k] = f"list[{len(v)}]"
            elif isinstance(v, dict) and depth < 2:
                out[k] = summarize(v, depth + 1)
            else:
                out[k] = type(v).__name__
        return out
    if isinstance(o, list):
        return f"list[{len(o)}]"
    return type(o).__name__


def main(paths):
    tok = _token()
    res = {}
    for p in paths:
        p = "/" + p.lstrip("/")  # accept "api/models" (Git-bash rewrites a leading "/" into a Windows path)
        req = urllib.request.Request(BASE + p, method="GET", headers={"X-BCC-Token": tok})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                body = r.read()
                try:
                    res[p] = {"http": r.status, "summary": summarize(json.loads(body))}
                except Exception:
                    res[p] = {"http": r.status, "bytes": len(body)}
        except urllib.error.HTTPError as e:
            res[p] = {"http": e.code}
        except Exception as e:  # noqa: BLE001 - probe reports, never raises
            res[p] = {"error": type(e).__name__}
    return {"at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "base": BASE, "results": res}


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1:]), ensure_ascii=False, indent=1))
