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


def _extract_structured_attachment(resolved: Path, suffix: str) -> str:
    """Safely extract bounded text from .csv, .tsv, .json, .jsonl, .xml, or .html files."""
    import csv
    raw_bytes = resolved.read_bytes()
    if suffix == ".xml":
        from bcc.safe_xml import safe_fromstring
        root_el = safe_fromstring(raw_bytes)
        parts: list[str] = []
        for elem in root_el.iter():
            tag = str(elem.tag).split("}", 1)[-1]
            txt = (elem.text or "").strip()
            if txt:
                parts.append(f"{tag}: {txt}")
        return " | ".join(parts)
    text = raw_bytes.decode("utf-8-sig", errors="replace")
    if suffix in {".csv", ".tsv"}:
        delim = "\t" if suffix == ".tsv" else ","
        rows: list[str] = []
        for idx, row in enumerate(csv.reader(text.splitlines(), delimiter=delim)):
            if idx >= 500:
                break
            cleaned_row = [c.strip() for c in row if c and c.strip()]
            if cleaned_row:
                rows.append(" | ".join(cleaned_row))
        return "\n".join(rows)
    if suffix == ".json":
        obj = json.loads(text)
        return json.dumps(obj, ensure_ascii=False)
    if suffix == ".jsonl":
        lines_out: list[str] = []
        for idx, line in enumerate(text.splitlines()):
            if idx >= 500:
                break
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            lines_out.append(json.dumps(obj, ensure_ascii=False))
        return "\n".join(lines_out)
    if suffix == ".html":
        from ..j2.research import sanitize_fetched
        safe_txt, _ = sanitize_fetched(text, max_chars=20_000)
        return safe_txt
    return text


def _export_attachment_text(export_file: Path, relative_file: object,
                            parser: Callable[[Path], str] | None) -> tuple[str, str | None]:
    """Extract bounded text from one Telegram Desktop attachment, never from a URL.

    Attachments are resolved relative to the export JSON and must remain beneath
    that directory. This deliberately excludes symlinks and absolute paths.
    """
    if not isinstance(relative_file, str) or not relative_file.strip():
        return "", None
    rel = Path(relative_file)
    if rel.is_absolute() or rel.drive or ".." in rel.parts:
        return "", "attachment path rejected"
    suffix = rel.suffix.lower()
    structured = {".csv", ".tsv", ".json", ".jsonl", ".xml", ".html"}
    allowed = {".txt", ".md", ".pdf", ".docx", ".pptx", ".xlsx"} | structured
    if suffix not in allowed:
        return "", "unsupported attachment type"
    try:
        root = export_file.parent.resolve(strict=True)
        candidate = export_file.parent / rel
        # Reject symlinks in every existing component, including the final file.
        current = export_file.parent
        for part in rel.parts:
            current = current / part
            if current.is_symlink():
                return "", "attachment symlink rejected"
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
        if not resolved.is_file() or resolved.stat().st_size > 32 * 1024 * 1024:
            return "", "attachment missing or too large"
        if suffix in {".txt", ".md"}:
            extracted = resolved.read_text(encoding="utf-8-sig", errors="replace")
        elif suffix in structured:
            extracted = _extract_structured_attachment(resolved, suffix)
        elif parser is None:
            return "", "document parser unavailable"
        else:
            extracted = parser(resolved)
        cleaned = " ".join(str(extracted or "").split())[:20_000]
        return cleaned, None if cleaned else "no text extracted"
    except Exception as exc:  # noqa: BLE001 — one malformed attachment cannot abort a source scan
        return "", f"attachment extraction failed: {type(exc).__name__}"


def _parse_telegram_html_export(html_text: str) -> list[dict]:
    """Safely parse Telegram Desktop HTML export (`messages*.html`) without executing scripts."""
    from html.parser import HTMLParser
    import re as _re

    class _TGHTMLParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.header_text: list[str] = []
            self.messages: list[dict] = []
            self._in_header = 0
            self._msg_depth = 0
            self._cur_msg: dict | None = None
            self._capture_field: str | None = None
            self._field_depth = 0

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            attr_map = {k: (v or "") for k, v in attrs}
            cls = set(attr_map.get("class", "").split())
            if tag == "div" and "page_header" in cls:
                self._in_header = 1
                return
            if self._in_header > 0 and tag == "div":
                self._in_header += 1
            if tag == "div" and "message" in cls and "service" not in cls:
                self._cur_msg = {
                    "id": attr_map.get("id", ""),
                    "peer_id": attr_map.get("data-peer-id", ""),
                    "from_id": attr_map.get("data-from-id", ""),
                    "date": "",
                    "from_name": "",
                    "text_parts": [],
                }
                self._msg_depth = 1
                return
            if self._cur_msg is not None:
                if tag == "div":
                    self._msg_depth += 1
                if tag == "div" and "date" in cls and attr_map.get("title"):
                    self._cur_msg["date"] = attr_map["title"]
                if tag == "div" and "from_name" in cls:
                    self._capture_field = "from_name"
                    self._field_depth = 1
                elif tag == "div" and "text" in cls:
                    self._capture_field = "text"
                    self._field_depth = 1
                elif self._capture_field and tag == "div":
                    self._field_depth += 1
                elif self._capture_field == "text" and tag == "br":
                    self._cur_msg["text_parts"].append("\n")

        def handle_endtag(self, tag: str) -> None:
            if self._in_header > 0 and tag == "div":
                self._in_header -= 1
            if self._cur_msg is not None and tag == "div":
                if self._capture_field:
                    self._field_depth -= 1
                    if self._field_depth <= 0:
                        self._capture_field = None
                self._msg_depth -= 1
                if self._msg_depth <= 0:
                    self.messages.append(self._cur_msg)
                    self._cur_msg = None

        def handle_data(self, data: str) -> None:
            if self._in_header > 0:
                self.header_text.append(data)
            if self._cur_msg is not None:
                if self._capture_field == "from_name":
                    self._cur_msg["from_name"] += data
                elif self._capture_field == "text":
                    self._cur_msg["text_parts"].append(data)

    parser = _TGHTMLParser()
    parser.feed(html_text[:8 * 1024 * 1024])
    parser.close()
    header_joined = " ".join("".join(parser.header_text).split())
    header_uid_match = _re.search(r"\b(\d{5,15})\b", header_joined)
    default_peer = int(header_uid_match.group(1)) if header_uid_match else None
    out: list[dict] = []
    for m in parser.messages:
        text = "".join(m["text_parts"]).strip()
        if not text:
            continue
        peer_str = str(m.get("peer_id") or "").strip()
        peer_uid = int(peer_str) if peer_str.isdigit() else default_peer
        from_id = str(m.get("from_id") or "").strip()
        sender_uid = _export_uid(from_id)
        if sender_uid is None:
            fn_match = _re.search(r"\b(\d{5,15})\b", str(m.get("from_name") or ""))
            if fn_match:
                sender_uid = int(fn_match.group(1))
        out.append({
            "id": m.get("id") or str(len(out) + 1),
            "peer_uid": peer_uid,
            "sender_uid": sender_uid,
            "date": m.get("date") or "",
            "text": text,
        })
    return out


def read_exports(inbox: Path, *, known: dict[int, str], cursors: dict[str, str],
                 name: str = "tg-export",
                 document_parser: Callable[[Path], str] | None = None) -> ReadResult:
    """Telegram Desktop JSON and HTML exports. Only known Jeff participants are attributed.

    A personal chat with a known participant keeps both sides (the other side
    as context, role ``other``); in any other chat only the known participant's
    own messages are kept. Strangers are never pulled into a corpus.
    """
    result = ReadResult()
    stat = SourceStat(name)
    result.stats.append(stat)
    if document_parser is None:
        # Optional, lazy, local-only Docling integration. Importing Master Parser
        # does not initialize models or download weights.
        try:
            from .documents import parse_with_docling
            document_parser = parse_with_docling
        except ImportError:
            document_parser = None
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
                attachment = message.get("file")
                extracted, extraction_error = _export_attachment_text(path, attachment,
                                                                        document_parser)
                if extraction_error:
                    # A message with an unsupported/unsafe attachment remains
                    # visible as a message, and the source report records why.
                    if attachment:
                        stat.errors += 1
                        stat.detail = extraction_error
                if extracted:
                    text = (text + "\n\n[Extracted local attachment text — untrusted data]\n" +
                            extracted).strip()
                    kind = "document"
                kind = ("command" if text.strip().startswith("/") else
                        "document" if attachment else
                        "text" if text.strip() else
                        "voice" if message.get("media_type") in {"voice_message", "video_message"} else
                        "photo" if message.get("photo") else "empty")
                result.messages.append(RawMessage(
                    known[owner_uid], "export", name,
                    f"{digest[:16]}:{chat.get('id')}:{message.get('id')}", role, ts, text, kind,
                    "", ""))
                stat.read += 1
        result.cursors[cursor_name] = path.name[:120] or "done"

    for html_path in sorted(Path(inbox).rglob("messages*.html")):
        try:
            if html_path.stat().st_size > MAX_EXPORT_BYTES:
                stat.errors += 1
                continue
            raw_html = html_path.read_bytes()
        except OSError:
            stat.errors += 1
            continue
        digest = hashlib.sha256(raw_html).hexdigest()
        cursor_name = f"{name}/html-{digest[:32]}"
        if cursors.get(cursor_name):
            continue
        try:
            parsed_msgs = _parse_telegram_html_export(raw_html.decode("utf-8-sig", errors="replace"))
        except Exception:  # noqa: BLE001
            stat.errors += 1
            continue
        for item in parsed_msgs:
            peer_uid = item.get("peer_uid")
            sender_uid = item.get("sender_uid")
            if peer_uid in known:
                owner_uid = peer_uid
                role = "participant" if (sender_uid == peer_uid or sender_uid is None) else "other"
            elif sender_uid in known:
                owner_uid, role = sender_uid, "participant"
            else:
                continue
            text = str(item.get("text") or "").strip()
            ts = _parse_ts(item.get("date"))
            kind = "command" if text.startswith("/") else ("text" if text else "empty")
            result.messages.append(RawMessage(
                known[owner_uid], "export", name,
                f"html:{digest[:16]}:{item.get('id')}", role, ts, text, kind, "", ""
            ))
            stat.read += 1
        result.cursors[cursor_name] = html_path.name[:120] or "done"
    return result


def read_call_transcripts(calls_dir: Path, *, cursors: dict[str, str],
                          name: str = "jeff-calls") -> ReadResult:
    """Incrementally read voice/call transcript JSONL files (`<pit>/calls/*.jsonl`)."""
    result = ReadResult()
    stat = SourceStat(name)
    result.stats.append(stat)
    if not Path(calls_dir).is_dir():
        stat.status = "missing"
        return result
    for path in sorted(Path(calls_dir).glob("*.jsonl")):
        cursor_name = f"{name}/{path.name}"
        offset = int(cursors.get(cursor_name, "0") or 0)
        try:
            size = path.stat().st_size
            if size < offset:
                offset = 0
            with path.open("rb") as handle:
                handle.seek(offset)
                chunk = handle.read()
        except OSError:
            stat.errors += 1
            continue
        consumed = chunk.rfind(b"\n") + 1
        for idx, line in enumerate(chunk[:consumed].splitlines()):
            try:
                rec = json.loads(line)
            except ValueError:
                stat.errors += 1
                continue
            if not isinstance(rec, dict):
                continue
            person_key = str(rec.get("person_key") or "").strip()
            text = str(rec.get("text") or rec.get("transcript") or "").strip()
            if not person_key or not text:
                continue
            role_raw = str(rec.get("role") or "participant")
            role = "assistant" if role_raw in {"assistant", "jeff", "bot"} else "participant"
            ts = _parse_ts(rec.get("ts") or rec.get("created_at") or rec.get("at"))
            call_id = str(rec.get("call_id") or path.stem)[:40]
            result.messages.append(RawMessage(
                person_key, "telegram", name, f"call:{call_id}:{offset}:{idx}",
                role, ts, text, "voice", "", ""
            ))
            stat.read += 1
        result.cursors[cursor_name] = str(offset + consumed)
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
