"""Шифрование ключей провайдеров at rest (раздел 8 архитектуры).

Ключ Fernet лежит в data dir с правами 600 и вне git; в API и логи уходит
только маска вида «…last4». Расшифрованный ключ живёт лишь в момент запроса.
"""
from __future__ import annotations

import os
from pathlib import Path

import logging

from cryptography.fernet import Fernet, InvalidToken

KEY_FILE = "secret.key"
KEY_ENV = "BOSSMAN_VAULT_KEY"
_log = logging.getLogger("bcc.secrets")


class Vault:
    """Fernet-шифрование секретов at-rest.

    Источник ключа (Security Hardening V1.1, H6):
    * если задан `BOSSMAN_VAULT_KEY` (валидный Fernet-ключ) — берём его. Это путь
      для внешнего/защищённого секрет-стора и для РОТАЦИИ: подставил новый ключ в
      env, перешифровал секреты, снял старый. Ключ в файл не пишется.
    * иначе — файл-ключ в data dir (0600, вне git), как раньше.

    Ротация: сгенерировать новый ключ (`Fernet.generate_key()`), выставить в
    `BOSSMAN_VAULT_KEY`, для каждого хранимого секрета decrypt старым → encrypt
    новым, затем убрать старый ключ. См. docs/security/SECURITY_HARDENING_V1_1.md.
    """

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / KEY_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fernet = Fernet(self._load_or_create())

    def _load_or_create(self) -> bytes:
        env_key = (os.environ.get(KEY_ENV) or "").strip()
        if env_key:
            # Ключ из внешнего секрет-стора/env — не персистим на диск.
            return env_key.encode()
        if self.path.exists():
            return self.path.read_bytes().strip()
        key = Fernet.generate_key()
        # O_EXCL + 0600: файл создаётся сразу с правами владельца, без окна гонки
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
        return key

    def encrypt(self, value: str | None) -> str | None:
        if not value:
            return None
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, blob: str | None) -> str | None:
        if not blob:
            return None
        try:
            return self._fernet.decrypt(blob.encode()).decode()
        except InvalidToken:
            # Ключ не подходит к шифротексту (сменили ключ без ре-шифрования, или
            # повреждение). Не роняем сервис, но и НЕ молчим: это security-событие,
            # маскирующее возможную потерю/подмену секрета (H6).
            _log.warning("vault: cannot decrypt a stored secret (key mismatch or tampering); "
                         "treating as absent — check BOSSMAN_VAULT_KEY / rotation")
            return None


def mask(key: str | None) -> str | None:
    """Публичное представление секрета: «…last4». Полный ключ наружу не отдаётся никогда."""
    if not key:
        return None
    return "…" + key[-4:] if len(key) > 4 else "…"


def mask_enc(vault: Vault, blob: str | None) -> str | None:
    """Маска для хранимого шифротекста (без выноса ключа за пределы функции)."""
    return mask(vault.decrypt(blob))
