"""Redaction layer: nothing reaches learning/training data before passing through here.

Three stages, all deterministic and offline:
1. key-name drop: whole fields that carry private chat content or identities are REMOVED
   (not masked) - a mask would still tell a reader that a message existed and how long it was;
2. text scrub: secrets (API keys, bot tokens, bearer headers, private keys, passwords), e-mails,
   phone numbers, Telegram chat/user ids and @handles, t.me links, are replaced by markers;
3. the existing journal recogniser (key-name + token-shape) runs last as a second net.

``assert_clean`` is the gate used before any write: it re-scans and raises if anything that the
patterns know about is still present, so a regression in stage 1/2 fails loudly instead of leaking.
SHA-256 digests are NOT scrubbed: they are evidence references, not secrets.
"""
from __future__ import annotations

import re
from typing import Any

MARK = "[УДАЛЕНО:{}]"

#: fields whose presence in a record means private content; removed entirely.
DROP_KEYS = frozenset({
    "message", "messages", "text", "chat", "chat_text", "chat_log", "chat_id", "from_id", "user_id", "telegram_id",
    "username", "first_name", "last_name", "phone", "phone_number", "email", "mail", "transcript", "conversation",
    "prompt", "system_prompt", "body", "content", "reply", "caption", "voice", "photo", "contact", "address",
    "result", "output", "stdout", "stderr", "title",
})
SECRET_KEY_RE = re.compile(r"(token(?!s(?![a-z])|s_)|secret|password|passwd|api[_-]?key|authorization|cookie|bearer|private[_-]?key|"
                           r"vault|credential|seed|otp|2fa)", re.I)

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S)),
    ("tg-bot-token", re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b")),
    ("api-key", re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|sk_(?:live|test)_[A-Za-z0-9]{12,}|hf_[A-Za-z0-9]{20,}|"
                           r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AIza[0-9A-Za-z_-]{30,}|"
                           r"xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|nvapi-[A-Za-z0-9_-]{20,}|"
                           r"or-v1-[A-Za-z0-9]{20,})\b")),
    ("bearer", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.I)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("password", re.compile(r"\b(?:password|passwd|пароль|secret|token|api[_-]?key)\s*[:=]\s*\S+", re.I)),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("tg-link", re.compile(r"\b(?:https?://)?t\.me/[A-Za-z0-9_+/-]+", re.I)),
    ("chat-id", re.compile(r"\b(?:chat[_ -]?id|user[_ -]?id|from[_ -]?id|tg[_ -]?id|telegram[_ -]?id)\b\W{0,3}-?\d{5,}",
                           re.I)),
    ("handle", re.compile(r"(?<![\w@])@[A-Za-z][A-Za-z0-9_]{4,31}\b")),
    ("phone", re.compile(r"(?<![\w.])\+?\d[\d\s().-]{8,16}\d(?![\w.])")),
)
_HEX_ONLY = re.compile(r"^[0-9a-f]{32,128}$")


def scrub_text(text: str) -> str:
    """Replace every known sensitive shape in free text. A bare hex digest is left alone."""
    if _HEX_ONLY.match(text.strip()):
        return text
    out = text
    for name, rx in _PATTERNS:
        if name == "phone":
            out = rx.sub(lambda m, n=name: m.group(0) if _is_digest_like(m.group(0)) else MARK.format(n), out)
        else:
            out = rx.sub(MARK.format(name), out)
    return out


def _is_digest_like(s: str) -> bool:
    digits = re.sub(r"\D", "", s)
    return len(digits) < 9 or len(digits) > 15


def sanitize(value: Any, *, _depth: int = 0) -> Any:
    """Return a copy of ``value`` safe for learning data (see module docstring)."""
    if _depth > 12:
        return MARK.format("too-deep")
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            key = str(k)
            low = key.lower()
            if low in DROP_KEYS:
                continue
            if SECRET_KEY_RE.search(low) and not low.endswith(("_sha256", "_hash", "sha256")):
                continue
            out[key] = sanitize(v, _depth=_depth + 1)
        return out
    if isinstance(value, (list, tuple, set)):
        return [sanitize(v, _depth=_depth + 1) for v in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return scrub_text(str(value))


def findings(value: Any) -> list[str]:
    """Names of sensitive shapes still present (used by the gate and by tests)."""
    found: list[str] = []

    def walk(v: Any, depth: int = 0) -> None:
        if depth > 12:
            return
        if isinstance(v, str):
            if _HEX_ONLY.match(v.strip()):
                return
            for name, rx in _PATTERNS:
                for m in rx.finditer(v):
                    if name == "phone" and _is_digest_like(m.group(0)):
                        continue
                    found.append(name)
        elif isinstance(v, dict):
            for k, x in v.items():
                low = str(k).lower()
                if low in DROP_KEYS:
                    found.append(f"key:{low}")
                elif SECRET_KEY_RE.search(low) and not low.endswith(("_sha256", "_hash", "sha256")):
                    found.append(f"key:{low}")
                walk(x, depth + 1)
        elif isinstance(v, (list, tuple, set)):
            for x in v:
                walk(x, depth + 1)

    walk(value)
    return found


class RedactionError(ValueError):
    pass


def assert_clean(value: Any) -> None:
    bad = findings(value)
    if bad:
        raise RedactionError("sensitive data would reach learning data: " + ", ".join(sorted(set(bad))))
