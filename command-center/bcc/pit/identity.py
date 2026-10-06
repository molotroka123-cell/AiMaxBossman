from __future__ import annotations

import hashlib
import hmac
import re
from pathlib import Path

_PERSON_KEY = re.compile(r"^[0-9a-f]{64}$")


def derive_person_key(telegram_user_id: int | str, salt: bytes) -> str:
    """Return a stable pseudonymous key without exposing Telegram IDs in paths."""
    if len(salt) < 16:
        raise ValueError("PIT identity salt must be at least 16 bytes")
    raw = str(int(telegram_user_id)).encode("ascii")
    return hmac.new(salt, b"telegram:" + raw, hashlib.sha256).hexdigest()


def validate_person_key(person_key: str) -> str:
    value = str(person_key).strip().lower()
    if not _PERSON_KEY.fullmatch(value):
        raise ValueError("invalid PIT person_key")
    return value


def scoped_person_dir(root: Path, person_key: str) -> Path:
    """Resolve one person's namespace and fail closed on any path escape."""
    root = Path(root).resolve()
    key = validate_person_key(person_key)
    target = (root / key).resolve()
    if target.parent != root:
        raise ValueError("person namespace escaped PIT root")
    return target
