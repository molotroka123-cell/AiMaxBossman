"""Deliver a generated video to the owner's Telegram chat (Bossman companion bot).

Last leg of the video workflow: result file -> Telegram. Uses the SAME bot and
owner chat already configured for the Telegram Companion; adds no new authority
(sends to the owner chat only, nothing is read from Telegram).

Token: env TG_COMPANION_BOT_TOKEN, else %LOCALAPPDATA%\\Bossman\\telegram-companion\\companion.env
       (loaded into this process only; never printed, never logged).
Chat:  config.json people[*].chat_id with role "owner" (or --chat override).

Usage:
  python tools/telegram_send_video.py --file PATH [--caption TEXT] [--chat ID]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
TC_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "telegram-companion"


def utf8_console() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


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
    ap.add_argument("--file", required=True)
    ap.add_argument("--caption", default="")
    ap.add_argument("--chat", default="")
    a = ap.parse_args()

    path = Path(a.file)
    if not path.is_file():
        raise SystemExit(f"file not found: {path}")
    if path.stat().st_size > 50 * 1024 * 1024:
        raise SystemExit("file larger than the Telegram bot API 50 MB limit")

    chat = int(a.chat) if a.chat else owner_chat_id()
    token = load_token()
    with httpx.Client(timeout=600) as c:
        r = c.post(
            f"https://api.telegram.org/bot{token}/sendVideo",
            data={"chat_id": str(chat), "caption": a.caption[:1000],
                  "supports_streaming": "true"},
            files={"video": (path.name, path.read_bytes(), "video/mp4")},
        )
        d = r.json()
        if d.get("ok"):
            print(f"delivered to chat {chat}: message_id={d['result']['message_id']}")
        else:
            print(f"TELEGRAM FAILED: {json.dumps(d, ensure_ascii=False)[:400]}")


if __name__ == "__main__":
    utf8_console()
    main()
