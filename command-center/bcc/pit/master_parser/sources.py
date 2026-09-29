"""Read-only readers for every place Jeff conversations live.

Sources (owner request 2026-09-28, «Master Parser»):

``jeff-telegram``  ``<data>/pit-v1.7/companion.sqlite3`` — the Telegram Jeff store:
    * ``inbox``        every participant update Jeff received (Fernet-sealed body:
                       text/caption, message id, voice/photo/document markers);
    * ``learning_log`` every answered turn ``{user, assistant}`` (sealed), written
                       only while the participant's memory consent is on; voice
                       notes appear here as their transcript;
    * ``history``      the rolling 16-pair chat context (sealed; a subset of the log).
``jeff-web``       ``<data>/pit-v1.7/web/companion.sqlite3`` — the Jeff window store,
                   same tables, web identities in their own HMAC namespace.
``vault-raw``      ``<data>/pit-v1.7/personalities/<key>/raw/events.jsonl`` — raw
                   events, present only when a participant enabled raw history.
``tg-export``      ``<data>/pit-v1.7/master-parser/inbox/**/*.json`` — Telegram
                   Desktop JSON exports (``result.json``) the owner drops in.
``extra``          optional extra PIT homes (old data-root copies/backups), opt-in.

Every reader opens SQLite with ``mode=ro`` and decrypts with the store's own
``secret.key`` through ``bcc.secrets.Vault`` — it never creates a key, never
writes, never deletes. Nothing here mutates a source.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator

from bcc.secrets import KEY_ENV, KEY_FILE, Vault

MAX_EXPORT_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class RawMessage:
    person_key: str
    surface: str          # telegram | web | export | vault
    source: str           # reader name, e.g. "jeff-telegram/inbox"
    source_ref: str       # stable inside the source, e.g. "inbox:17"
    role: str             # participant | assistant | other
    ts: float
    text: str
    kind: str = "text"    # text | voice | photo | document | sticker | command | empty
    label: str = ""       # reserved; ids are never stored (owner rule)
    platform_message_id: str = ""


@dataclass(slots=True)
class SourceStat:
    name: str
    status: str = "ok"            # ok | missing | locked_key | error
    read: int = 0
    errors: int = 0
    detail: str = ""


@dataclass(slots=True)
class ReadResult:
    messages: list[RawMessage] = field(default_factory=list)
    cursors: dict[str, str] = field(default_factory=dict)
    stats: list[SourceStat] = field(default_factory=list)


class SourceUnavailable(RuntimeError):
    pass


def _ro_connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    return db


def store_opener(home: Path) -> Callable[[str], object]:
    """Decrypt sealed Store bodies with the store's existing key — never create one."""
    if not (os.environ.get(KEY_ENV, "").strip() or (Path(home) / KEY_FILE).is_file()):
        raise SourceUnavailable("store key missing")
    vault = Vault(Path(home))

    def open_sealed(blob: str):
        data = vault.decrypt(blob)
        if data is None:
            raise ValueError("sealed body did not decrypt")
        return json.loads(data)
    return open_sealed


def _uid_of(who: str) -> int | None:
    try:
        return int(str(who).split(":", 1)[0])
    except (TypeError, ValueError):
        return None


def _kind_of(body: dict) -> str:
    text = str(body.get("text") or "")
    if text.strip().startswith("/"):
        return "command"
    if body.get("_voice"):
        return "voice"
    if body.get("_photo"):
        return "photo"
    if body.get("_document"):
        return "document"
    if body.get("_sticker"):
        return "sticker"
    return "text" if text.strip() else "empty"


def _table_exists(db: sqlite3.Connection, name: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                      (name,)).fetchone() is not None


def read_store(home: Path, *, name: str, surface: str, key_for_uid: Callable[[int], str],
               cursors: dict[str, str]) -> ReadResult:
    """Incrementally read one Jeff conversation store (inbox, learning_log, history)."""
    result = ReadResult()
    path = Path(home) / "companion.sqlite3"
    stat = SourceStat(name)
    result.stats.append(stat)
    if not path.is_file():
        stat.status = "missing"
        return result
    try:
        opener = store_opener(Path(home))
    except SourceUnavailable as exc:
        stat.status, stat.detail = "locked_key", str(exc)
        return result
    try:
        db = _ro_connect(path)
    except sqlite3.Error as exc:
        stat.status, stat.detail = "error", type(exc).__name__
        return result
    try:
        for table in ("inbox", "learning_log", "history"):
            if not _table_exists(db, table):
                continue
            cursor_name = f"{name}/{table}"
            after = int(cursors.get(cursor_name, "0") or 0)
            high = after
            column = "created"
            for row in db.execute(f"SELECT id, who, body, {column} FROM {table} "  # noqa: S608
                                  "WHERE id>? ORDER BY id", (after,)):
                high = max(high, int(row["id"]))
                uid = _uid_of(row["who"])
                if uid is None or uid <= 0:
                    continue
                person_key = key_for_uid(uid)
                label = ""   # never persist a Telegram/web id; the key is enough
                try:
                    body = opener(row["body"])
                except Exception:  # noqa: BLE001 — one bad row never stops the pass
                    stat.errors += 1
                    continue
                ts = float(row[column] or 0.0)
                if table == "inbox":
                    if not isinstance(body, dict):
                        continue
                    kind = _kind_of(body)
                    text = str(body.get("text") or "")
                    if not body:
                        kind, text = "empty", ""
                    result.messages.append(RawMessage(
                        person_key, surface, f"{name}/inbox", f"inbox:{row['id']}", "participant",
                        ts, text, kind, label, str(body.get("_message_id") or "")))
                    stat.read += 1
                    continue
                if table == "learning_log" and isinstance(body, dict):
                    user, assistant = str(body.get("user") or ""), str(body.get("assistant") or "")
                elif table == "history" and isinstance(body, list) and len(body) == 2:
                    user, assistant = str(body[0] or ""), str(body[1] or "")
                else:
                    stat.errors += 1
                    continue
                ref = "log" if table == "learning_log" else "hist"
                if user.strip():
                    kind = "command" if user.strip().startswith("/") else "text"
                    result.messages.append(RawMessage(
                        person_key, surface, f"{name}/{table}", f"{ref}:{row['id']}:u",
                        "participant", ts - 0.001, user, kind, label))
                    stat.read += 1
                if assistant.strip():
                    result.messages.append(RawMessage(
                        person_key, surface, f"{name}/{table}", f"{ref}:{row['id']}:a",
                        "assistant", ts, assistant, "text", label))
                    stat.read += 1
            result.cursors[cursor_name] = str(high)
    except sqlite3.Error as exc:
        stat.status, stat.detail = "error", type(exc).__name__
    finally:
        db.close()
    return result


def read_vault_raw(root: Path, *, cursors: dict[str, str], name: str = "vault-raw") -> ReadResult:
    """``personalities/<key>/raw/events.jsonl`` — byte-offset cursor per person."""
    result = ReadResult()
    stat = SourceStat(name)
    result.stats.append(stat)
    if not Path(root).is_dir():
        stat.status = "missing"
        return result
    for events in sorted(Path(root).glob("*/raw/events.jsonl")):
        person_key = events.parent.parent.name
        cursor_name = f"{name}/{person_key}"
        offset = int(cursors.get(cursor_name, "0") or 0)
        try:
            size = events.stat().st_size
            if size < offset:           # never truncated by us; re-read defensively
                offset = 0
            with events.open("rb") as handle:
                handle.seek(offset)
                chunk = handle.read()
        except OSError:
            stat.errors += 1
            continue
        consumed = chunk.rfind(b"\n") + 1
        for number, line in enumerate(chunk[:consumed].splitlines()):
            try:
                event = json.loads(line)
            except ValueError:
                stat.errors += 1
                continue
            if not isinstance(event, dict):
                continue
            text = str(event.get("text") or "")
            role = str(event.get("role") or event.get("direction") or "participant")
            role = "assistant" if role in {"assistant", "bot", "out", "outgoing", "jeff"} else "participant"
            ts = _parse_ts(event.get("ts") or event.get("at") or event.get("created"))
            ref = f"raw:{offset}:{number}"
            result.messages.append(RawMessage(
                person_key, "vault", name, ref, role, ts, text,
                "command" if text.strip().startswith("/") else ("text" if text.strip() else "empty"),
                "", str(event.get("message_id") or "")))
            stat.read += 1
        result.cursors[cursor_name] = str(offset + consumed)
    return result


def _parse_ts(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        try:
            return float(value)
        except ValueError:
            pass
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except ValueError:
            return 0.0
    return 0.0


def _flatten_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_flatten_text(part) for part in value)
    if isinstance(value, dict):
        return str(value.get("text") or "")
    return ""


def _export_chats(data) -> Iterator[dict]:
    if isinstance(data, dict) and isinstance(data.get("messages"), list):
        yield data
    chats = data.get("chats") if isinstance(data, dict) else None
    if isinstance(chats, dict) and isinstance(chats.get("list"), list):
        for chat in chats["list"]:
            if isinstance(chat, dict) and isinstance(chat.get("messages"), list):
                yield chat


def _export_uid(value) -> int | None:
    text = str(value or "")
    if text.startswith("user") and text[4:].isdigit():
        return int(text[4:])
    return None


def read_exports(inbox: Path, *, known: dict[int, str], cursors: dict[str, str],
                 name: str = "tg-export") -> ReadResult:
    """Telegram Desktop JSON exports. Only known Jeff participants are attributed.

    A personal chat with a known participant keeps both sides (the other side
    as context, role ``other``); in any other chat only the known participant's
    own messages are kept. Strangers are never pulled into a corpus.
    """
    result = ReadResult()
    stat = SourceStat(name)
    result.stats.append(stat)
    if not Path(inbox).is_dir():
        stat.status = "missing"
        return result
    for path in sorted(Path(inbox).rglob("*.json")):
        try:
            if path.stat().st_size > MAX_EXPORT_BYTES:
                stat.errors += 1
                continue
            raw = path.read_bytes()
        except OSError:
            stat.errors += 1
            continue
        digest = hashlib.sha256(raw).hexdigest()
        cursor_name = f"{name}/{digest[:32]}"
        if cursors.get(cursor_name):
            continue
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except ValueError:
            stat.errors += 1
            continue
        for chat in _export_chats(data):
            peer = chat.get("id")
            peer = int(peer) if isinstance(peer, int) or (isinstance(peer, str) and peer.isdigit()) else None
            personal = chat.get("type") in {"personal_chat", "bot_chat"} and peer in known
            for message in chat.get("messages", []):
                if not isinstance(message, dict) or message.get("type", "message") != "message":
                    continue
                sender = _export_uid(message.get("from_id"))
                text = _flatten_text(message.get("text"))
                if personal:
                    owner_uid = peer
                    role = "participant" if sender == peer else "other"
                elif sender in known:
                    owner_uid, role = sender, "participant"
                else:
                    continue
                ts = _parse_ts(message.get("date_unixtime") or message.get("date"))
                kind = ("command" if text.strip().startswith("/") else
                        "text" if text.strip() else
                        "voice" if message.get("media_type") in {"voice_message", "video_message"} else
                        "photo" if message.get("photo") else "empty")
                result.messages.append(RawMessage(
                    known[owner_uid], "export", name,
                    f"{digest[:16]}:{chat.get('id')}:{message.get('id')}", role, ts, text, kind,
                    "", ""))
                stat.read += 1
        result.cursors[cursor_name] = path.name[:120] or "done"
    return result


def telegram_uids(home: Path) -> set[int]:
    """Participant Telegram ids seen by a Jeff store (read-only)."""
    path = Path(home) / "companion.sqlite3"
    ids: set[int] = set()
    if not path.is_file():
        return ids
    try:
        db = _ro_connect(path)
    except sqlite3.Error:
        return ids
    try:
        for table in ("inbox", "learning_log", "history"):
            if not _table_exists(db, table):
                continue
            for (who,) in db.execute(f"SELECT DISTINCT who FROM {table}"):  # noqa: S608
                uid = _uid_of(who)
                if uid and uid > 0:
                    ids.add(uid)
    except sqlite3.Error:
        pass
    finally:
        db.close()
    return ids


def fingerprint(paths: Iterable[Path]) -> dict[str, str]:
    """sha256 of every existing source file — the no-mutation proof used by tests/reports."""
    out = {}
    for path in paths:
        path = Path(path)
        if path.is_file():
            out[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out
