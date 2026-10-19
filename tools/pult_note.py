"""Send one text note to the owner's pult (Telegram companion bot), safely.

Why this exists (owner 19.10 «аудит работы с пультом учитывается в написании кода»): ad-hoc PowerShell 5 scripts
read UTF-8 files as ANSI and delivered garbled Cyrillic. Python always encodes the request body as UTF-8.

Guards: exactly ONE recipient in the companion config (the owner), token read from companion.env and never printed,
plain text only, length capped at Telegram's 4096 chars, optional pin. ``--dry-run`` prints the payload size instead of
sending.

Usage:
  python tools/pult_note.py "текст"            # send
  python tools/pult_note.py --file note.txt --pin
  python tools/pult_note.py --dry-run "текст"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

MAX_LEN = 4096


class PultNoteError(Exception):
    pass


def companion_dir(env: dict | None = None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("LOCALAPPDATA", str(Path.home()))) / "Bossman" / "telegram-companion"


def read_token(cdir: Path) -> str:
    env_file = cdir / "companion.env"
    if not env_file.is_file():
        raise PultNoteError(f"нет файла {env_file}")
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        name, sep, value = line.partition("=")
        if sep and name.strip() == "TG_COMPANION_BOT_TOKEN":
            token = value.strip().strip('"').strip("'")
            if token:
                return token
    raise PultNoteError("в companion.env нет TG_COMPANION_BOT_TOKEN")


def owner_chat(cdir: Path):
    cfg = json.loads((cdir / "config.json").read_text(encoding="utf-8-sig"))
    people = cfg.get("people") or []
    if len(people) != 1:
        raise PultNoteError(f"в пульте должен быть ровно один получатель (владелец), найдено {len(people)} — отказ")
    p = people[0]
    chat = (p.get("chat_id") or p.get("id")) if isinstance(p, dict) else p
    if chat in (None, ""):
        raise PultNoteError("у получателя нет chat_id")
    return chat


def build_payload(chat, text: str) -> bytes:
    text = (text or "").strip()
    if not text:
        raise PultNoteError("пустой текст")
    if len(text) > MAX_LEN:
        text = text[: MAX_LEN - 1] + "…"
    return json.dumps({"chat_id": chat, "text": text, "disable_web_page_preview": True},
                      ensure_ascii=False).encode("utf-8")


def _post(url: str, body: bytes, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:      # noqa: S310 - fixed https Telegram host
        return json.loads(r.read().decode("utf-8"))


def send(text: str, *, pin: bool = False, cdir: Path | None = None, post=_post) -> dict:
    cdir = cdir or companion_dir()
    chat = owner_chat(cdir)
    token = read_token(cdir)
    api = f"https://api.telegram.org/bot{token}"
    res = post(f"{api}/sendMessage", build_payload(chat, text))
    if not res.get("ok"):
        raise PultNoteError(f"Telegram отказал: {res.get('description', 'без описания')}")
    mid = res["result"]["message_id"]
    pinned = None
    if pin:
        pres = post(f"{api}/pinChatMessage", json.dumps({"chat_id": chat, "message_id": mid,
                                                         "disable_notification": True}).encode("utf-8"))
        pinned = bool(pres.get("ok"))
    return {"message_id": mid, "pinned": pinned}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Одна заметка владельцу в пульт (UTF-8, только владелец)")
    ap.add_argument("text", nargs="?", default="")
    ap.add_argument("--file", help="текст из файла UTF-8")
    ap.add_argument("--pin", action="store_true", help="закрепить сообщение")
    ap.add_argument("--dry-run", action="store_true", help="не отправлять, показать размер того, что уйдёт")
    a = ap.parse_args(argv)
    text = Path(a.file).read_text(encoding="utf-8-sig") if a.file else a.text
    try:
        if a.dry_run:
            body = build_payload(owner_chat(companion_dir()), text)
            print(json.dumps({"would_send_chars": len(json.loads(body)["text"]), "utf8_bytes": len(body)}))
            return 0
        print(json.dumps(send(text, pin=a.pin)))
        return 0
    except PultNoteError as e:
        print(f"ОТКАЗ: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
