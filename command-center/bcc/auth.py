"""Локальный токен-доступ (раздел 8): bind на 127.0.0.1 + обязательный X-BCC-Token.

Токен генерируется при первом старте, хранится в data dir с правами 600 и
не попадает в stdout без явного opt-in в интерактивной консоли.
"""
from __future__ import annotations

import hmac
import os
import secrets as _secrets
import sys
from pathlib import Path

TOKEN_FILE = "token"
HEADER = "X-BCC-Token"
# Только значение "1" и интерактивный первый запуск разрешают печать токена.
TOKEN_STDOUT_ENV = "BCC_TOKEN_STDOUT"


class TokenAuth:
    """Один статический токен на инсталляцию (single-user MVP)."""

    def __init__(self, data_dir: Path, *, announce: bool = True):
        self.path = Path(data_dir) / TOKEN_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.token, created = self._load_or_create()
        if announce:
            self.announce(created)

    def _load_or_create(self) -> tuple[str, bool]:
        if self.path.exists():
            value = self.path.read_text(encoding="utf-8").strip()
            if value:
                return value, False
        token = _secrets.token_urlsafe(32)
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(token)
        return token, True

    def announce(self, created: bool = False) -> None:
        head = "создан новый токен" if created else "токен"
        if os.environ.get(TOKEN_STDOUT_ENV) == "0":
            # Запуск без консоли: stdout — файловый журнал, который владелец
            # пересылает при разборе сбоя. Токен остаётся в своём файле (600).
            return
        stream = sys.stdout
        if stream is None:
            return  # pythonw (ярлык BOSSMAN): консоли нет, токен — в файле с правами 600
        interactive_opt_in = (created and os.environ.get(TOKEN_STDOUT_ENV) == "1"
                              and bool(getattr(stream, "isatty", lambda: False)()))
        text = f"[bcc] файл токена доступа: {self.path}"
        if interactive_opt_in:
            text = f"[bcc] {head} доступа: {self.token}\n" + text
        try:
            # Русская консоль Windows (cp1251/cp1252): печать токена не должна
            # ронять старт сервера кодировочной ошибкой.
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — StringIO в тестах, пайпы и т.п.
            pass
        try:
            print(text, flush=True)
        except (UnicodeEncodeError, OSError, ValueError):
            try:
                print(text.encode("ascii", "backslashreplace").decode("ascii"), flush=True)
            except Exception:  # noqa: BLE001 — анонс лишь удобство, не требование
                pass

    def check(self, token: str | None) -> bool:
        if not token:
            return False
        # сравнение в байтах: строки с не-ASCII compare_digest не принимает
        return hmac.compare_digest(token.encode("utf-8", "ignore"), self.token.encode())
