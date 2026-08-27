"""Шифрование ключей провайдеров at rest (раздел 8 архитектуры).

Ключ Fernet лежит в data dir с правами 600 и вне git; в API и логи уходит
только маска вида «…last4». Расшифрованный ключ живёт лишь в момент запроса.
"""
from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

KEY_FILE = "secret.key"


class Vault:
    """Fernet поверх файла-ключа в data dir."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / KEY_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fernet = Fernet(self._load_or_create())

    def _load_or_create(self) -> bytes:
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
            # ключ шифрования сменили — считаем, что секрета нет (не роняем сервис)
            return None


def mask(key: str | None) -> str | None:
    """Публичное представление секрета: «…last4». Полный ключ наружу не отдаётся никогда."""
    if not key:
        return None
    return "…" + key[-4:] if len(key) > 4 else "…"


def mask_enc(vault: Vault, blob: str | None) -> str | None:
    """Маска для хранимого шифротекста (без выноса ключа за пределы функции)."""
    return mask(vault.decrypt(blob))
