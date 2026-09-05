"""Подписанные улики (TZ-01 §2.1, EH-01, INV-1/INV-6).

Доверие к улике «по префиксу source» — security by naming. Здесь — свойство
«улику с verified=True может создать только код, у которого есть ключ процесса»:
HMAC-SHA256 над каноническим JSON (sort_keys, без пробелов). Ключ никогда не
проходит через модель: он лежит в файле 0600 и читается только этим модулем.

    from bossman_shared import evidence
    fields = evidence.sign_fields(payload, signer="bossman_v3.memory.journal")
    evidence.verify(payload | fields_without_sig, fields["sig"])  # True

Ключ: `BOSSMAN_EVIDENCE_KEY_FILE`, иначе `<BOSSMAN_DATA_DIR или ~/.bossman>/keys/evidence.key`
(32 байта `secrets.token_bytes`, создаётся при первом обращении, права 0600).
Без ключа подписать нельзя (исключение), проверить — нельзя (False): fail-closed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

KEY_BYTES = 32
ENV_KEY_FILE = "BOSSMAN_EVIDENCE_KEY_FILE"
ENV_DATA_DIR = "BOSSMAN_DATA_DIR"
SIG_FIELDS = ("sig", "signer", "nonce", "issued_at")
# Кто вправе подписывать улики verified=True. Строка `source` — информационная.
TRUSTED_SIGNERS = frozenset({"bossman_v3.memory.journal", "bcc.v2.verification", "bossman_v3.verifier"})

_cache: dict[str, bytes] = {}


class EvidenceKeyUnavailable(RuntimeError):
    pass


def key_path() -> Path:
    explicit = os.environ.get(ENV_KEY_FILE)
    if explicit:
        return Path(explicit).expanduser()
    base = Path(os.environ.get(ENV_DATA_DIR) or "~/.bossman").expanduser()
    return base / "keys" / "evidence.key"


def load_or_create_key(path: Path | None = None) -> bytes:
    p = Path(path) if path is not None else key_path()
    cached = _cache.get(str(p))
    if cached is not None:
        return cached
    if p.exists():
        key = p.read_bytes()
        if len(key) < KEY_BYTES:
            raise EvidenceKeyUnavailable(f"evidence key at {p} is too short ({len(key)} bytes)")
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(KEY_BYTES)
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, key)
        finally:
            os.close(fd)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    _cache[str(p)] = key
    return key


def reset_cache() -> None:
    _cache.clear()


def canonical(payload: Mapping[str, Any]) -> bytes:
    body = {k: v for k, v in payload.items() if k != "sig"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def sign(payload: Mapping[str, Any], *, key: bytes | None = None) -> str:
    k = key if key is not None else load_or_create_key()
    return hmac.new(k, canonical(payload), hashlib.sha256).hexdigest()


def verify(payload: Mapping[str, Any], sig: str, *, key: bytes | None = None) -> bool:
    if not sig or not isinstance(sig, str):
        return False
    try:
        k = key if key is not None else load_or_create_key()
    except (EvidenceKeyUnavailable, OSError):
        return False
    return hmac.compare_digest(hmac.new(k, canonical(payload), hashlib.sha256).hexdigest(), sig)


def sign_fields(payload: Mapping[str, Any], *, signer: str, key: bytes | None = None) -> dict[str, str]:
    """Поля подписи для улики: signer/nonce/issued_at входят в подписываемое тело."""
    if signer not in TRUSTED_SIGNERS:
        raise ValueError(f"signer {signer!r} is not allowed to sign evidence")
    meta = {"signer": signer, "nonce": uuid.uuid4().hex,
            "issued_at": datetime.now(timezone.utc).isoformat()}
    body = {**{k: v for k, v in payload.items() if k not in SIG_FIELDS}, **meta}
    return {**meta, "sig": sign(body, key=key)}


def verify_signed(record: Mapping[str, Any], *, key: bytes | None = None) -> bool:
    """Запись с полями sig/signer/nonce/issued_at: подпись валидна И signer доверенный."""
    if record.get("signer") not in TRUSTED_SIGNERS:
        return False
    body = {k: v for k, v in record.items() if k != "sig"}
    return verify(body, str(record.get("sig") or ""), key=key)
