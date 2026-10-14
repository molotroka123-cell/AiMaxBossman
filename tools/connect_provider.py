"""Connect an OpenAI-compatible provider to Bossman's registry from a key kept OUT of the repo and out of the logs.

    HTF_KEY=<key> python tools/connect_provider.py --name HowToFix --base-url https://api.howtofix.id/v1 \
        --models deepseek-v4.1-flash deepseek-v4-flash [--key-env HTF_KEY] [--price-in 0.1 --price-out 0.4]

The key is read from an environment variable (default PROVIDER_KEY), encrypted into the Bossman vault
(`BCC_DATA_DIR`, same registry the app uses) and never printed. Models are registered WITHOUT a price unless
--price-in/--price-out are given: with the free-only governance an unpriced cloud model stays refused for product
chat until the owner sets a price — the key is stored, spend stays gated. Re-running updates nothing twice.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "command-center"))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--key-env", default="PROVIDER_KEY")
    ap.add_argument("--price-in", type=float)
    ap.add_argument("--price-out", type=float)
    ap.add_argument("--context", type=int, default=128000)
    a = ap.parse_args()
    key = os.environ.get(a.key_env, "").strip()
    if not key or any(ch in key for ch in "\r\n\t "):
        print(f"set {a.key_env} to a single-line key", file=sys.stderr)
        return 2
    from bcc.config import settings
    from bcc.db import Database
    from bcc.events import EventBus
    from bcc.registry import Registry
    from bcc.secrets import Vault

    db = Database(settings.database_url)
    reg = Registry(db, Vault(settings.data_dir), EventBus(db))
    host = a.base_url.split("//", 1)[-1].split("/", 1)[0].lower()
    rows = [p for p in await reg.list_providers() if host in (p.get("base_url") or "").lower()]
    if rows:
        pid = int(rows[0]["id"])
        print("provider exists", pid, rows[0].get("api_key_masked"))
    else:
        p = await reg.create_provider(a.name, "openai_compat", a.base_url, key)
        pid = int(p["id"])
        print("provider created", pid, p.get("api_key_masked"))
    have = {m["name"] for m in await reg.list_models() if m.get("provider_id") == pid}
    for name in a.models:
        if name in have:
            continue
        m = await reg.create_model(provider_id=pid, name=name, alias=f"{a.name.lower()}:{name}", kind="cloud",
                                   context_window=a.context, price_in=a.price_in, price_out=a.price_out,
                                   caps={"tools": True, "coding": True})
        print("model", m.get("id"), name)
    print(json.dumps([{"id": m["id"], "name": m["name"], "pricing_known": m.get("pricing_known")}
                      for m in await reg.list_models() if m.get("provider_id") == pid]))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
