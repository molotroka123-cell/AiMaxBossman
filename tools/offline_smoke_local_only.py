"""Offline / local-only smoke (MP21-A). No network: any non-loopback connect raises.

Run:  BOSSMAN_LOCAL_ONLY=1 python tools/offline_smoke_local_only.py
Checks: (1) cloud adapters refuse at the provider boundary, (2) a loopback model
answers one tiny chat, (3) a socket-level guard saw zero external connects.
Exit code 0 only when all three hold; prints no secrets.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "command-center", ROOT / "bossman-core"):
    sys.path.insert(0, str(p))

os.environ.setdefault("BOSSMAN_LOCAL_ONLY", "1")
BLOCKED: list[str] = []
_orig = socket.socket.connect


def _guard(self, addr, *a, **k):
    host = addr[0] if isinstance(addr, tuple) else str(addr)
    try:
        ok = ipaddress.ip_address(host).is_loopback
    except ValueError:
        ok = host == "localhost"
    if not ok:
        BLOCKED.append(host)
        raise OSError(f"offline smoke: external connect to {host} blocked")
    return _orig(self, addr, *a, **k)


socket.socket.connect = _guard  # type: ignore[assignment]

from bcc.providers import AnthropicAdapter, OpenAICompatAdapter  # noqa: E402


async def main() -> int:
    res: dict = {"local_only_env": os.environ.get("BOSSMAN_LOCAL_ONLY")}
    for name, ad in (("openrouter", OpenAICompatAdapter(base_url="https://openrouter.ai/api/v1", api_key="x")),
                     ("anthropic", AnthropicAdapter(api_key="x"))):
        try:
            await ad.health()
            res[name] = "NOT_BLOCKED"
        except PermissionError as exc:
            res[name] = f"blocked: {exc}"
    local = OpenAICompatAdapter(base_url=os.environ.get("SMOKE_LOCAL_URL", "http://127.0.0.1:11434/v1"), api_key="local")
    model = os.environ.get("SMOKE_LOCAL_MODEL", "ace-decider-08b:latest")
    t0 = time.perf_counter()
    try:
        out = await local.chat(model, [{"role": "user", "content": "Reply with the single word: ok"}], max_tokens=8)
        res["local_chat"] = {"model": model, "ok": bool(out.text.strip()), "sec": round(time.perf_counter() - t0, 2)}
    except Exception as exc:  # noqa: BLE001
        res["local_chat"] = {"model": model, "ok": False, "error": type(exc).__name__ + ": " + str(exc)[:160]}
    res["external_connects_blocked"] = BLOCKED
    good = (res["openrouter"].startswith("blocked") and res["anthropic"].startswith("blocked")
            and res["local_chat"]["ok"] and not BLOCKED)
    res["verdict"] = "PASS" if good else "FAIL"
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
