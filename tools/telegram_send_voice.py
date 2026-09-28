"""Send a voice note (OGG/Opus) to a Telegram chat with dedupe + audit record.

Part of the Jeff night-greeting workflow. Rules baked in:
- caller passes explicit --chat (orchestrator resolves the consenting recipients);
- --exclude-ids are ALWAYS rejected even if passed by mistake;
- --dedupe-log: JSON file of past sends; identical (chat, sha256) is skipped;
- on uncertain result the tool reports status and does NOT auto-retry.

Token: env TG_COMPANION_BOT_TOKEN, else %LOCALAPPDATA%\\Bossman\\telegram-companion\\companion.env
       (never printed). No private text is logged.

Usage:
  python tools/telegram_send_voice.py --file OUT.ogg --chat ID --run-id ID \
      [--caption TEXT] [--exclude-ids 1,2] [--dedupe-log PATH] [--voice NAME] [--duration S]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import httpx

TC_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "telegram-companion"


def utf8_console() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
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
        raise SystemExit("no bot token")
    return token


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--chat", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--caption", default="")
    ap.add_argument("--exclude-ids", default="")
    ap.add_argument("--dedupe-log", default="")
    ap.add_argument("--voice", default="")
    ap.add_argument("--duration", type=float, default=0.0)
    a = ap.parse_args()

    chat = int(a.chat)
    excluded = {int(x) for x in a.exclude_ids.split(",") if x.strip()}
    if chat in excluded:
        print(f"SKIP chat {chat}: id is on the permanent exclude list")
        return

    path = Path(a.file)
    if not path.is_file():
        raise SystemExit(f"file not found: {path}")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()

    log_path = Path(a.dedupe_log) if a.dedupe_log else path.parent / "sent_log.json"
    history = []
    if log_path.is_file():
        try:
            history = json.loads(log_path.read_text(encoding="utf-8-sig"))
        except Exception:  # noqa: BLE001
            history = []
    if any(r.get("chat") == chat and r.get("sha256") == sha for r in history):
        print(f"SKIP chat {chat}: already sent (dedupe)")
        return

    token = load_token()
    with httpx.Client(timeout=300) as c:
        r = c.post(
            f"https://api.telegram.org/bot{token}/sendVoice",
            data={"chat_id": str(chat), "caption": a.caption[:1000]},
            files={"voice": (path.name, path.read_bytes(), "audio/ogg")},
        )
        d = r.json()
        record = {
            "run_id": a.run_id,
            "chat": chat,
            "file": path.name,
            "sha256": sha[:16],
            "voice": a.voice,
            "duration_s": a.duration,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "ok": bool(d.get("ok")),
            "message_id": (d.get("result") or {}).get("message_id") if d.get("ok") else None,
            "error": None if d.get("ok") else json.dumps(d, ensure_ascii=False)[:200],
        }
        history.append(record)
        log_path.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
        if d.get("ok"):
            print(f"SENT to chat {chat}: message_id={record['message_id']}")
        else:
            print(f"FAILED chat {chat}: {record['error']}")


if __name__ == "__main__":
    utf8_console()
    main()
