"""Pre-TTS capture for security-sensitive replies (autonomy freeze, line C, task 6).

Before Jeff speaks a reply (Telegram voice reply or the Jeff window's speak button), the EXACT text handed to the
TTS engine is classified. A security-sensitive reply (identity, disclosure, secrets, privacy, security topics)
gets one durable audit row first - SHA-256 of the spoken text, the category and a redacted copy - so what was
said aloud can be audited later even though audio is never stored. The row is flushed and fsynced before the
engine runs; if it cannot be written the caller refuses to synthesize (fail closed).

Rows live under the PIT runtime logs (``<data_dir>/pit-v1.7/logs/pre_tts_audit.jsonl``), never in Git.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

PRE_TTS_AUDIT_SCHEMA = "bossman.pit.pre-tts-audit/1"
AUDIT_FILE = "pre_tts_audit.jsonl"
REDACTED_MAX = 2000

_CATEGORIES: tuple[tuple[str, re.Pattern[str]], ...] = tuple((name, re.compile(rx, re.I)) for name, rx in (
    ("secret", r"ключ\w*|\bkey\b|api[- ]?key|токен\w*|\btokens?\b|парол\w*|password|secret|секрет\w*|seed|\b2fa\b|"
               r"\botp\b|credential|\[скрыто\]|\[REDACTED"),
    ("disclosure", r"инструкц\w*|промпт\w*|\bprompt|скрыт\w+\s+правил|hidden\s+rules|системн\w+|system\s+(?:prompt|"
                   r"message)|конфиг\w*|config|endpoint|эндпоинт|сервер\w*|server|\bapi\b|\bпорт\w*|\bport\b|"
                   r"внутренн\w+\s+устройств"),
    ("identity", r"\bмодел\w*|\bmodel|провайдер\w*|provider|кто\s+(?:я|ты)|who\s+(?:i|you)\s+am|я\s+jeff|i'm\s+jeff|"
                 r"i\s+am\s+jeff|\bjev\b"),
    ("privacy", r"владел\w*|\bowner|памят\w*|\bmemory|местополож\w*|location|другого\s+пользовател|other\s+users?|"
                r"личн\w+\s+данн|personal\s+data"),
    ("security", r"взлом\w*|\bhack|malware|вирус\w*|вредонос\w*|фишинг\w*|phishing|approval\w*|подтвержден\w*|"
                 r"команд\w+\s+на\s+пк|\bshell\b|jailbreak|джейлбрейк"),
))
_DIGITS = re.compile(r"\d{6,}")


def security_category(text: str) -> str:
    """'' for an ordinary reply, else the first matching sensitive category."""
    value = str(text or "")
    for name, pattern in _CATEGORIES:
        if pattern.search(value):
            return name
    return ""


def redact(text: str) -> str:
    from .identity_guard import guard_reply
    value = guard_reply(str(text or "")).text
    return _DIGITS.sub("[digits]", value)[:REDACTED_MAX]


def audit_path(audit_dir: Path | str) -> Path:
    return Path(audit_dir) / AUDIT_FILE


def _append_durable(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def capture(text: str, *, surface: str, audit_dir: Path | str, category: str = "") -> dict | None:
    """Write the audit row for ``text`` (the exact TTS input) when it is security-sensitive.

    Returns the row, or None for an ordinary reply. Raises ``OSError`` when the row cannot be made durable:
    the caller must then not synthesize."""
    value = str(text or "")
    category = category or security_category(value)
    if not category:
        return None
    row = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "schema": PRE_TTS_AUDIT_SCHEMA,
        "surface": str(surface)[:20],
        "category": category,
        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
        "chars": len(value),
        "redacted": redact(value),
    }
    _append_durable(audit_path(audit_dir), row)
    return row


def read_rows(audit_dir: Path | str, *, last: int = 200) -> list[dict]:
    path = audit_path(audit_dir)
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines()[-last:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows
