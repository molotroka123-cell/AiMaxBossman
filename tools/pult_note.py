"""Send one text note to the owner's pult (Telegram companion bot), safely.

Why this exists (owner 19.10 «аудит работы с пультом учитывается в написании кода»): ad-hoc PowerShell 5 scripts
read UTF-8 files as ANSI and delivered garbled Cyrillic. Python always encodes the request body as UTF-8.

Guards: exactly ONE recipient in the companion config (the owner), token read from companion.env and never printed,
length capped at Telegram's 4096 chars, optional pin. ``--dry-run`` prints the payload size instead of sending.

Formatting (owner 21.10 «Bold, код удобно скопировать, максимально красиво»): ``--html`` sends Telegram HTML —
``<b>`` headings, ``<code>`` for branch/SHA/path (tap to copy), ``<pre>`` blocks for commands and prompts (copy button).
Long HTML is split between blocks, never inside a ``<pre>``; if Telegram cannot parse the markup the same chunk goes
out as plain text, so a report is never lost. ``--doc`` sends a file with an HTML caption.

Usage:
  python tools/pult_note.py "текст"            # send
  python tools/pult_note.py --file note.txt --pin
  python tools/pult_note.py --html --file report.html
  python tools/pult_note.py --doc prompt.md "<b>Мастер-промт</b> для Argon"
  python tools/pult_note.py --dry-run "текст"
"""
from __future__ import annotations

import argparse
import html as _html
import json
import os
import re
import sys
import urllib.request
import uuid
from pathlib import Path

MAX_LEN = 4096
CAPTION_MAX = 1024


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


def esc(value) -> str:
    """Escape any value for Telegram HTML (only &, <, > are special there)."""
    return _html.escape(str(value), quote=False)


def strip_tags(text: str) -> str:
    """HTML chunk -> readable plain text (fallback when Telegram rejects the markup)."""
    return _html.unescape(re.sub(r"<[^>]+>", "", text))


_PRE = re.compile(r"<pre\b[^>]*>.*?</pre>", re.S)
_PRE_PARTS = re.compile(r"(<pre\b[^>]*>(?:<code\b[^>]*>)?)(.*?)((?:</code>)?</pre>)$", re.S)


def _blocks(text: str) -> list[str]:
    """Paragraphs separated by blank lines; a <pre> block is one paragraph even if it holds blank lines."""
    out, pos = [], 0
    for m in _PRE.finditer(text):
        out += [b for b in text[pos:m.start()].split("\n\n") if b.strip()]
        out.append(m.group(0))
        pos = m.end()
    out += [b for b in text[pos:].split("\n\n") if b.strip()]
    return [b.strip("\n") for b in out]


def _split_block(block: str, limit: int) -> list[str]:
    """One oversized block -> pieces <= limit; a <pre> stays a valid <pre> in every piece."""
    m = _PRE_PARTS.match(block)
    head, body, tail = (m.group(1), m.group(2), m.group(3)) if m else ("", block, "")
    room = limit - len(head) - len(tail)
    pieces, cur = [], ""
    for line in body.split("\n"):
        while len(line) > room:                       # a single line longer than a message: hard cut
            if cur:
                pieces.append(cur)
                cur = ""
            pieces.append(line[:room])
            line = line[room:]
        if cur and len(cur) + 1 + len(line) > room:
            pieces.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        pieces.append(cur)
    return [head + p + tail for p in pieces]


def split_html(text: str, limit: int = MAX_LEN) -> list[str]:
    """Pack paragraphs into messages <= limit chars, never cutting inside a tag pair of a <pre> block."""
    chunks, cur = [], ""
    for block in _blocks((text or "").strip()):
        parts = [block] if len(block) <= limit else _split_block(block, limit)
        for part in parts:
            if cur and len(cur) + 2 + len(part) > limit:
                chunks.append(cur)
                cur = part
            else:
                cur = f"{cur}\n\n{part}" if cur else part
    if cur:
        chunks.append(cur)
    return chunks


def build_payload(chat, text: str, *, html: bool = False) -> bytes:
    text = (text or "").strip()
    if not text:
        raise PultNoteError("пустой текст")
    if len(text) > MAX_LEN:
        if html:
            raise PultNoteError("HTML длиннее 4096 — сначала split_html")
        text = text[: MAX_LEN - 1] + "…"
    payload = {"chat_id": chat, "text": text, "disable_web_page_preview": True}
    if html:
        payload["parse_mode"] = "HTML"
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _post(url: str, body: bytes, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:      # noqa: S310 - fixed https Telegram host
        return json.loads(r.read().decode("utf-8"))


def _send_one(api: str, chat, text: str, html: bool, post) -> int:
    res = post(f"{api}/sendMessage", build_payload(chat, text, html=html))
    if not res.get("ok") and html and "parse" in str(res.get("description", "")).lower():
        res = post(f"{api}/sendMessage", build_payload(chat, strip_tags(text)))    # markup broken: never lose the text
    if not res.get("ok"):
        raise PultNoteError(f"Telegram отказал: {res.get('description', 'без описания')}")
    return res["result"]["message_id"]


def send(text: str, *, pin: bool = False, html: bool = False, cdir: Path | None = None, post=_post) -> dict:
    cdir = cdir or companion_dir()
    chat = owner_chat(cdir)
    token = read_token(cdir)
    api = f"https://api.telegram.org/bot{token}"
    chunks = split_html(text) if html else [text]
    if not chunks:
        raise PultNoteError("пустой текст")
    ids = [_send_one(api, chat, chunk, html, post) for chunk in chunks]
    mid = ids[0]
    pinned = None
    if pin:
        pres = post(f"{api}/pinChatMessage", json.dumps({"chat_id": chat, "message_id": mid,
                                                         "disable_notification": True}).encode("utf-8"))
        pinned = bool(pres.get("ok"))
    out = {"message_id": mid, "pinned": pinned}
    if html:
        out["message_ids"] = ids
    return out


def build_document(chat, path: Path, caption: str = "", *, html: bool = True) -> tuple[bytes, str]:
    """multipart/form-data body for sendDocument; the caption is capped at Telegram's 1024 chars."""
    caption = (caption or "").strip()
    if len(caption) > CAPTION_MAX:
        caption = strip_tags(caption)[: CAPTION_MAX - 1] + "…"
        html = False
    boundary = uuid.uuid4().hex
    fields = [("chat_id", str(chat))] + ([("caption", caption)] if caption else [])
    if caption and html:
        fields.append(("parse_mode", "HTML"))
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8")
             for k, v in fields]
    name = path.name.replace('"', "")
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{name}"\r\n'
                 f"Content-Type: application/octet-stream\r\n\r\n".encode("utf-8") + path.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _post_multipart(url: str, body: bytes, content_type: str, timeout: float = 60.0) -> dict:
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": content_type})
    with urllib.request.urlopen(req, timeout=timeout) as r:      # noqa: S310 - fixed https Telegram host
        return json.loads(r.read().decode("utf-8"))


def send_document(path: Path, caption: str = "", *, html: bool = True, cdir: Path | None = None,
                  post=_post_multipart) -> dict:
    path = Path(path)
    if not path.is_file():
        raise PultNoteError(f"нет файла {path}")
    cdir = cdir or companion_dir()
    chat = owner_chat(cdir)
    api = f"https://api.telegram.org/bot{read_token(cdir)}"
    res = post(f"{api}/sendDocument", *build_document(chat, path, caption, html=html))
    if not res.get("ok") and html and caption and "parse" in str(res.get("description", "")).lower():
        res = post(f"{api}/sendDocument", *build_document(chat, path, strip_tags(caption), html=False))
    if not res.get("ok"):
        raise PultNoteError(f"Telegram отказал: {res.get('description', 'без описания')}")
    return {"message_id": res["result"]["message_id"]}


def build_video(chat, path: Path, caption: str = "", *, html: bool = True) -> tuple[bytes, str]:
    """multipart/form-data body for sendVideo with supports_streaming=true."""
    caption = (caption or "").strip()
    if len(caption) > CAPTION_MAX:
        caption = strip_tags(caption)[: CAPTION_MAX - 1] + "…"
        html = False
    boundary = uuid.uuid4().hex
    fields = [("chat_id", str(chat)), ("supports_streaming", "true")] + ([("caption", caption)] if caption else [])
    if caption and html:
        fields.append(("parse_mode", "HTML"))
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8")
             for k, v in fields]
    name = path.name.replace('"', "")
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="video"; filename="{name}"\r\n'
                 f"Content-Type: video/mp4\r\n\r\n".encode("utf-8") + path.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def send_video(path: Path, caption: str = "", *, html: bool = True, cdir: Path | None = None,
               post=_post_multipart) -> dict:
    path = Path(path)
    if not path.is_file():
        raise PultNoteError(f"нет файла {path}")
    cdir = cdir or companion_dir()
    chat = owner_chat(cdir)
    api = f"https://api.telegram.org/bot{read_token(cdir)}"
    res = post(f"{api}/sendVideo", *build_video(chat, path, caption, html=html))
    if not res.get("ok") and html and caption and "parse" in str(res.get("description", "")).lower():
        res = post(f"{api}/sendVideo", *build_video(chat, path, strip_tags(caption), html=False))
    if not res.get("ok"):
        raise PultNoteError(f"Telegram отказал: {res.get('description', 'без описания')}")
    return {"message_id": res["result"]["message_id"]}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Одна заметка владельцу в пульт (UTF-8, только владелец)")
    ap.add_argument("text", nargs="?", default="")
    ap.add_argument("--file", help="текст из файла UTF-8")
    ap.add_argument("--pin", action="store_true", help="закрепить сообщение")
    ap.add_argument("--html", action="store_true", help="Telegram HTML: <b>, <code>, <pre>; длинное делится по блокам")
    ap.add_argument("--doc", help="отправить файл; текст становится HTML-подписью (до 1024 символов)")
    ap.add_argument("--video", help="отправить MP4-видео; текст становится HTML-подписью (до 1024 символов)")
    ap.add_argument("--dry-run", action="store_true", help="не отправлять, показать размер того, что уйдёт")
    a = ap.parse_args(argv)
    text = Path(a.file).read_text(encoding="utf-8-sig") if a.file else a.text
    try:
        if a.dry_run:
            chat = owner_chat(companion_dir())
            if a.html:
                chunks = split_html(text)
                print(json.dumps({"would_send_messages": len(chunks), "chars": [len(c) for c in chunks]}))
                return 0
            body = build_payload(chat, text)
            print(json.dumps({"would_send_chars": len(json.loads(body)["text"]), "utf8_bytes": len(body)}))
            return 0
        if a.video:
            print(json.dumps(send_video(Path(a.video), text)))
            return 0
        if a.doc:
            print(json.dumps(send_document(Path(a.doc), text)))
            return 0
        print(json.dumps(send(text, pin=a.pin, html=a.html)))
        return 0
    except PultNoteError as e:
        print(f"ОТКАЗ: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
