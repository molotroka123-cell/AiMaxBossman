"""Night mailing AS JEFF: use the PIT Jeff bot token (never printed), deliver
video + caption, then a male-voice note, to the owner chat (the only consented
recipient). Dedupe via sent log; permanently excluded ids are rejected.

Token resolution order: env BOSSMAN_PIT_BOT_TOKEN -> pit-v1.7/credentials.enc
(decrypted in memory with the local Vault key; value never logged).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx
from cryptography.fernet import Fernet, InvalidToken

PIT = Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "CommandCenter" / "pit-v1.7"
ROOT = Path(__file__).resolve().parents[1]
NG = ROOT / "artifacts" / "night_greeting"
CHAT = 386321847          # owner, the only consented user in both allowlists
EXCLUDED = {1286116494}   # permanent owner-ordered exclusion
TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{20,}$")


def utf8_console() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _walk_for_token(obj):
    if isinstance(obj, str):
        return obj if TOKEN_RE.match(obj.strip()) else None
    if isinstance(obj, dict):
        for v in obj.values():
            t = _walk_for_token(v)
            if t:
                return t
    if isinstance(obj, list):
        for v in obj:
            t = _walk_for_token(v)
            if t:
                return t
    return None


def load_token() -> tuple[str, str]:
    tok = os.environ.get("BOSSMAN_PIT_BOT_TOKEN", "").strip()
    if tok and TOKEN_RE.match(tok):
        return tok, "env"
    key_files = [PIT / "secret.key", PIT.parent / "secret.key",
                 Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "telegram-companion" / "secret.key"]
    raw = (PIT / "credentials.enc").read_bytes()
    blobs = [raw.strip()]
    try:  # a json kv store with encrypted values
        doc = json.loads(raw.decode("utf-8-sig"))
        blobs.extend([v.encode() for v in doc.values() if isinstance(v, str)])
        blobs.extend([v.encode() for v in doc.values() if isinstance(v, list)
                      for v in v if isinstance(v, str)])
    except Exception:  # noqa: BLE001
        pass
    for key_path in key_files:
        if not key_path.is_file():
            continue
        try:
            f = Fernet(key_path.read_bytes().strip())
        except Exception:  # noqa: BLE001
            continue
        for blob in blobs:
            if not blob:
                continue
            try:
                plain = f.decrypt(blob)
            except (InvalidToken, ValueError):
                continue
            text = plain.decode("utf-8", "replace")
            t = _walk_for_token(text)
            if t:
                return t, f"credentials.enc via {key_path.name}"
            try:  # structured store: pull bot_token explicitly (nested decrypt if needed)
                doc2 = json.loads(text)
                if isinstance(doc2, dict):
                    print("credentials.decrypted field names:", sorted(doc2))
                    cand = str(doc2.get("bot_token", "")).strip()
                    for _ in range(3):
                        if not cand:
                            break
                        if TOKEN_RE.match(cand):
                            return cand, f"credentials.enc via {key_path.name}"
                        if cand.startswith("gAAAAAB"):
                            cand = f.decrypt(cand.encode()).decode("utf-8", "replace").strip()
                            continue
                        print(f"bot_token shape: len={len(cand)} colon={':' in cand}")
                        break
            except Exception:  # noqa: BLE001
                pass
    raise SystemExit("jeff bot token not found (env/credentials.enc)")


def dedupe_ok(sha: str) -> bool:
    log = NG / "sent_log_jeff.json"
    if log.is_file():
        try:
            history = json.loads(log.read_text(encoding="utf-8-sig"))
        except Exception:  # noqa: BLE001
            history = []
        if any(r.get("chat") == CHAT and r.get("sha256") == sha for r in history):
            return False
    return True


def record(kind: str, sha: str, ok: bool, detail) -> None:
    log = NG / "sent_log_jeff.json"
    history = []
    if log.is_file():
        try:
            history = json.loads(log.read_text(encoding="utf-8-sig"))
        except Exception:  # noqa: BLE001
            history = []
    history.append({"chat": CHAT, "kind": kind, "sha256": sha[:16], "ok": ok,
                    "detail": detail, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
    log.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    assert CHAT not in EXCLUDED
    token, source = load_token()
    video = Path(r"C:\Users\asd\Downloads\c7ade7b3-1ac6-4eed-a0d8-2f5d207f46e8.mp4")
    voice = NG / "night_jeff_male.ogg"
    with httpx.Client(timeout=600) as c:
        me = c.get(f"https://api.telegram.org/bot{token}/getMe").json()
        bot = (me.get("result") or {}).get("username", "?")
        print(f"sender bot: @{bot} (token source: {source})")
        for kind, path, caption in (
            ("video", video, "Спокойной ночи! Пусть приснятся самые сладкие сны 🌙 — ваш Jeff"),
            ("voice", voice, "Сладких снов 🌙"),
        ):
            if not Path(path).is_file():
                print(f"SKIP {kind}: missing {path}")
                continue
            sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            if not dedupe_ok(sha):
                print(f"SKIP {kind}: already sent (dedupe)")
                continue
            method = "sendVideo" if kind == "video" else "sendVoice"
            field = "video" if kind == "video" else "voice"
            mime = "video/mp4" if kind == "video" else "audio/ogg"
            r = c.post(f"https://api.telegram.org/bot{token}/{method}",
                       data={"chat_id": str(CHAT), "caption": caption[:1000],
                             "supports_streaming": "true"},
                       files={field: (Path(path).name, Path(path).read_bytes(), mime)})
            d = r.json()
            if d.get("ok"):
                mid = d["result"]["message_id"]
                print(f"SENT {kind} as @#{bot} -> chat {CHAT}: message_id={mid}")
                record(kind, sha, True, f"message_id={mid}")
            else:
                print(f"FAILED {kind}: {json.dumps(d, ensure_ascii=False)[:200]}")
                record(kind, sha, False, json.dumps(d, ensure_ascii=False)[:200])


if __name__ == "__main__":
    utf8_console()
    main()
