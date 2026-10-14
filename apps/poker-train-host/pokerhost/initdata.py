"""Telegram Mini App initData check (https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app).

The player is whoever Telegram signed, never what the page claims: secret = HMAC_SHA256("WebAppData", bot_token),
hash = HMAC_SHA256(secret, sorted "key=value" lines without `hash`). Pure functions, no I/O.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

MAX_AGE_S = 24 * 3600          # a stale initData (copied link, replay) is refused


def verify(init_data: str, bot_tokens: list[str] | tuple[str, ...], *, now: float | None = None,
           max_age_s: int = MAX_AGE_S) -> dict | None:
    """Return the signed Telegram user ({id, first_name, username, ...}) or None if anything is off."""
    if not init_data or len(init_data) > 4096:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    got = pairs.pop("hash", "")
    if not got:
        return None
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items())).encode()
    for token in bot_tokens:
        if not token:
            continue
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        if hmac.compare_digest(hmac.new(secret, check, hashlib.sha256).hexdigest(), got):
            break
    else:
        return None
    try:
        auth_date = int(pairs.get("auth_date", "0"))
        user = json.loads(pairs.get("user", "null"))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(user, dict) or not isinstance(user.get("id"), int):
        return None
    if abs((now if now is not None else time.time()) - auth_date) > max_age_s:
        return None
    return user


def verify_bot(init_data: str, bots: dict[str, str], **kw) -> tuple[dict | None, str | None]:
    """Like verify(), but also says WHICH bot signed it ('jeff' / 'pult'), so the reply goes through that bot."""
    for name, token in bots.items():
        user = verify(init_data, [token], **kw) if token else None
        if user is not None:
            return user, name
    return None, None


def display_name(user: dict) -> str:
    name = " ".join(str(user.get(k) or "").strip() for k in ("first_name", "last_name")).strip()
    name = name or (("@" + user["username"]) if user.get("username") else f"Игрок {user['id'] % 10000}")
    return "".join(ch for ch in name if ch.isprintable())[:32]


def sign_for_tests(fields: dict, bot_token: str) -> str:
    """Build a correctly signed initData string (tests only)."""
    from urllib.parse import urlencode
    data = {k: (json.dumps(v, separators=(",", ":")) if isinstance(v, dict) else str(v)) for k, v in fields.items()}
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items())).encode()
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return urlencode({**data, "hash": hmac.new(secret, check, hashlib.sha256).hexdigest()})
