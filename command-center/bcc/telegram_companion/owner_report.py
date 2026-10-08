"""Owner stage report through the configured Пульт (Telegram companion): ``sendMessage`` only.

    python -m bcc.telegram_companion.owner_report --file <report.md | -> [--send] [--config PATH]

Dry run is the default: it prints the chunk plan and the masked target and sends nothing. ``--send`` delivers
the text to the person whose role is ``owner`` in the companion config (the same lookup as
``bcc.market.notify``), through the companion transport (egress guard and secret scrub included). This module
never calls ``getUpdates``, so it cannot become a second poller of the bot token. The Jeff participant bot is a
different component and is never used for owner reports.

Exit codes: 0 ok (sent or dry run), 2 usage, 3 Пульт not configured (nothing sent), 4 refused (text looks like
it holds a secret), 5 delivery failed.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path
from typing import Any, Callable

from ..pit.secret_filter import contains_secret_hint

EXIT_OK, EXIT_USAGE, EXIT_NOT_CONFIGURED, EXIT_REFUSED, EXIT_SEND_FAILED = 0, 2, 3, 4, 5
CHUNK_CHARS = 3900          # Telegram's hard limit is 4096; the margin covers multi-byte accounting

#: Shapes the pit filter does not know: OpenRouter/OpenAI-style keys, GitHub tokens, AWS access key ids.
_EXTRA_SECRETS = (
    re.compile(r"\bsk-or-[A-Za-z0-9_-]{10,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)


def looks_secret(text: str) -> bool:
    return contains_secret_hint(text) or any(p.search(text) for p in _EXTRA_SECRETS)


def split_chunks(text: str, limit: int = CHUNK_CHARS) -> list[str]:
    """Split at line boundaries into parts of at most ``limit`` characters (an over-long line is cut hard)."""
    parts: list[str] = []
    current = ""
    for line in text.strip().splitlines():
        while len(line) > limit:
            head, line = line[:limit], line[limit:]
            if current:
                parts.append(current)
                current = ""
            parts.append(head)
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            parts.append(current)
            current = line
        else:
            current = candidate
    if current.strip():
        parts.append(current)
    return parts


def _mask(chat_id: int) -> str:
    digits = str(chat_id)
    return "*" * max(0, len(digits) - 3) + digits[-3:]


def _load_owner(config_path: Path | None) -> tuple[Any, Any] | None:
    """(settings, owner person) of the configured Пульт, or None when it is not configured."""
    from .config import load
    from .paths import companion_config_path
    config = config_path or companion_config_path(read_fallback=True)
    if not config.exists():
        return None
    settings = load(config, env_file=config.parent / "companion.env")
    owner = next((p for p in settings.people if p.role == "owner"), None)
    if not settings.enabled or owner is None or not settings.bot_token:
        return None
    return settings, owner


async def send_report(text: str, *, send: bool = False, config_path: Path | None = None,
                      transport_factory: Callable[[Any], Any] | None = None,
                      html: bool = False, pin: bool = False) -> dict:
    """Plan (and with ``send=True`` deliver) one owner report. Never raises for the expected refusals."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "exit": EXIT_USAGE, "reason": "empty report"}
    if looks_secret(text):
        return {"ok": False, "exit": EXIT_REFUSED, "reason": "the text looks like it contains a secret; nothing sent"}
    loaded = _load_owner(config_path)
    if loaded is None:
        return {"ok": False, "exit": EXIT_NOT_CONFIGURED, "reason": "канал Пульта не настроен"}
    settings, owner = loaded
    chunks = split_chunks(text)
    plan = {"chunks": len(chunks), "chars": len(text), "target": _mask(owner.chat_id)}
    if not send:
        return {"ok": True, "exit": EXIT_OK, "sent": False, "dry_run": True, **plan}
    if transport_factory is None:
        from .adapters import Telegram
        transport_factory = Telegram
    transport = transport_factory(settings)
    ids: list = []
    pinned = False
    try:
        for part in chunks:
            ids.append(await transport.send(owner, part, parse_mode="HTML" if html else None))
        if pin and ids and type(ids[0]) is int:
            try:   # a checkpoint is pinned for the owner; a refused pin never fails the report
                await transport.call("pinChatMessage", {"chat_id": owner.chat_id, "message_id": ids[0],
                                                        "disable_notification": True})
                pinned = True
            except Exception:  # noqa: BLE001
                pinned = False
    except Exception as exc:  # noqa: BLE001 - delivery problems become an exit code, not a traceback
        return {"ok": False, "exit": EXIT_SEND_FAILED, "sent": bool(ids),
                "reason": f"delivery failed: {type(exc).__name__}", **plan}
    finally:
        await transport.close()
    return {"ok": True, "exit": EXIT_OK, "sent": True, "dry_run": False, "message_ids": ids, "pinned": pinned, **plan}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bcc.telegram_companion.owner_report",
                                     description="Send a stage report to the owner's Пульт (sendMessage only).")
    parser.add_argument("--file", required=True, help="report text file ('-' = stdin)")
    parser.add_argument("--send", action="store_true", help="really send (default: dry run)")
    parser.add_argument("--config", type=Path, default=None, help="companion config.json (default: the installed one)")
    parser.add_argument("--html", action="store_true", help="Telegram HTML formatting (bold, code, headings; plain fallback)")
    parser.add_argument("--pin", action="store_true", help="pin the first message of this report (checkpoint)")
    args = parser.parse_args(argv)
    try:
        text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"не удалось прочитать отчёт: {type(exc).__name__}", file=sys.stderr)
        return EXIT_USAGE
    result = asyncio.run(send_report(text, send=args.send, config_path=args.config, html=args.html, pin=args.pin))
    if result.get("reason"):
        print(result["reason"], file=sys.stderr)
    elif result.get("dry_run"):
        print(f"DRY RUN: {result['chunks']} part(s), {result['chars']} chars -> chat {result['target']} (use --send)")
    else:
        print(f"sent {result['chunks']} part(s) -> chat {result['target']}")
    return int(result["exit"])


if __name__ == "__main__":
    raise SystemExit(main())
