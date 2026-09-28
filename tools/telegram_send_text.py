"""Send a text checkpoint report to the owner's Telegram chat (Bossman companion bot).

Same authority model as telegram_send_video.py: sends to the owner chat only,
token loaded into this process only, never printed.

Usage:
  python tools/telegram_send_text.py --text "report" [--chat ID]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

TC_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "telegram-companion"


def load_token() -> str:
    token = os.environ.get("TG_COMPANION_BOT_TOKEN", "").strip()
    envf = TC_DIR / "companion.env"
    if not token and envf.is_file():
        for line in envf.read_text(encoding="utf-8-sig").splitlines():
            if line.startswith("TG_COMPANION_BOT_TOKEN="):
                token = line.split("=", 1)[1].strip().strip('"')
    if not token:
        raise SystemExit("no bot token (env TG_COMPANION_BOT_TOKEN or companion.env)")
    return token


def owner_chat_id() -> int:
    cfg = TC_DIR / "config.json"
    people = (json.loads(cfg.read_text(encoding="utf-8-sig")) or {}).get("people") or []
    for p in people:
        if p.get("role") == "owner" and p.get("chat_id"):
            return int(p["chat_id"])
    raise SystemExit("owner chat_id not found in companion config.json")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", required=True)
    ap.add_argument("--chat", default="")
    a = ap.parse_args()
    chat = int(a.chat) if a.chat else owner_chat_id()
    token = load_token()
    with httpx.Client(timeout=60) as c:
        r = c.post(f"https://api.telegram.org/bot{token}/sendMessage",
                   data={"chat_id": str(chat), "text": a.text[:4000]})
        d = r.json()
        if d.get("ok"):
            print(f"delivered to chat {chat}: message_id={d['result']['message_id']}")
        else:
            print(f"TELEGRAM FAILED: {json.dumps(d, ensure_ascii=False)[:400]}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    main()
