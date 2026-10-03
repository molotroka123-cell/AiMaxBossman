"""One announcement from Jeff to the people who already talk to him (owner-initiated, never automatic).

Used by the Bossman tool ``jeff.broadcast`` (features/tools_jeff.py), which is always an ASK: the owner sees the exact text and the
recipient count before anything is sent. Only people who already wrote to Jeff are reachable (Telegram allows nothing else); the
owner's private block rule beats everything; sending is sendMessage only (no getUpdates, so the live Jeff poller is untouched);
the Jeff database is opened read-only; the token never leaves this module.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from . import config as pc
from .blocklist import PrivateBlocklist

MAX_TEXT = 3500


def recipients(data_dir: Path | str) -> list[int]:
    """Telegram user ids (== private chat ids) that have written to Jeff, from his own read-only store."""
    db_path = pc.pit_home(Path(data_dir)) / "companion.sqlite3"
    if not db_path.is_file():
        return []
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    try:
        whos = {r[0] for r in db.execute("SELECT DISTINCT who FROM history")}
        whos |= {r[0] for r in db.execute("SELECT DISTINCT who FROM inbox WHERE lane='chat'")}
    finally:
        db.close()
    out = []
    for who in sorted(whos):
        uid, _, cid = str(who).partition(":")
        if uid.isdigit() and uid == cid and int(uid) > 0:
            out.append(int(uid))
    return out


def _clean(text: str) -> str:
    text = str(text or "").strip()
    if not text:
        raise ValueError("EMPTY_TEXT")
    if len(text) > MAX_TEXT:
        raise ValueError("TEXT_TOO_LONG")
    return text


def plan(data_dir: Path | str, text: str) -> dict:
    """What a send would do right now: recipient count after the block rule, never any id (only the count)."""
    text = _clean(text)
    settings = pc.load(pc.config_path(Path(data_dir)))
    blocklist = PrivateBlocklist.from_settings(settings)
    from ..telegram_companion.config import Person
    deliverable = 0
    ids = recipients(data_dir)
    for uid in ids:
        person = Person(uid, uid, "owner" if uid == settings.people[0].user_id else "guest")
        if not blocklist.blocks_person(person):
            deliverable += 1
    return {"people": len(ids), "deliverable": deliverable, "chars": len(text)}


async def send(data_dir: Path | str, text: str) -> dict:
    """Send ``text`` to every deliverable person. Per-person failures are counted, never fatal; ids are never returned."""
    text = _clean(text)
    from ..telegram_companion.adapters import Telegram
    from ..telegram_companion.config import Person
    from .runtime import _transport_settings
    settings = pc.load(pc.config_path(Path(data_dir)))
    blocklist = PrivateBlocklist.from_settings(settings)
    telegram = Telegram(_transport_settings(settings))
    base = telegram.authorize_delivery
    telegram.authorize_delivery = lambda p: (not blocklist.blocks_person(p)) and (settings.allowlist_open or bool(base(p)))
    sent = failed = blocked = 0
    try:
        for uid in recipients(data_dir):
            person = Person(uid, uid, "owner" if uid == settings.people[0].user_id else "guest")
            if not telegram.authorize_delivery(person):
                blocked += 1
                continue
            try:
                await telegram.send(person, text)
                sent += 1
            except Exception:  # noqa: BLE001 - one unreachable person must not stop the others
                failed += 1
    finally:
        await telegram.close()
    return {"sent": sent, "failed": failed, "blocked": blocked}
