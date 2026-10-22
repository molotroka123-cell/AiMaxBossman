"""Локальный токен-доступ (раздел 8): bind на 127.0.0.1 + обязательный X-BCC-Token.

Токен генерируется при первом старте, хранится в data dir с правами 600 и
не попадает в stdout без явного opt-in в интерактивной консоли.
"""
from __future__ import annotations

import hmac
import os
import secrets as _secrets
import subprocess
import sys
import time
import warnings
from pathlib import Path

TOKEN_FILE = "token"
HEADER = "X-BCC-Token"
# Только значение "1" и интерактивный первый запуск разрешают печать токена.
TOKEN_STDOUT_ENV = "BCC_TOKEN_STDOUT"


def _restrict_to_owner(path: Path) -> None:
    """Сузить доступ к файлу токена до владельца. На POSIX всё сделал 0600.

    На Windows режим из `os.open` не создаёт ACL: он переключает единственный
    атрибут «только чтение», и токен — весь контроль доступа к Command Center —
    читается любым процессом сессии пользователя. Явный owner-only DACL ставит
    icacls: `/inheritance:r` снимает унаследованные разрешения, `/grant:r
    <user>:F` оставляет доступ только владельцу.

    Близнец bossman_shared/evidence.py::restrict_to_owner; здесь он повторён
    намеренно — auth стартует раньше всего и не должен тянуть чужой пакет.
    Best-effort: провал не имеет права уронить старт сервера (токен уже есть),
    но и молчать нельзя — предупреждение, не исключение.
    """
    if os.name != "nt":
        return
    user = os.environ.get("USERNAME") or ""
    domain = os.environ.get("USERDOMAIN") or ""
    principal = f"{domain}\\{user}" if domain and user else (user or "%USERNAME%")
    # Каталог получает НАСЛЕДУЕМЫЙ ACE: с обычным `:F` Windows пересчитывает права вложенных файлов, и каждый файл, у которого
    # были только унаследованные ACE, остаётся с ПУСТЫМ DACL (D:AI) — не читается никем, даже владельцем.
    grant = f"{principal}:(OI)(CI)F" if os.path.isdir(path) else f"{principal}:F"   # not Path(): os.name may be patched to nt
    argv = ["icacls", str(path), "/inheritance:r", "/grant:r", grant]
    try:
        proc = subprocess.run(  # noqa: S603 — argv, без строки для оболочки
            argv, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30, check=False)
    except Exception as exc:  # noqa: BLE001 — нет icacls, урезанный образ, таймаут
        warnings.warn(f"не удалось запустить icacls для {path}: {exc!r}; "
                      "файл токена остаётся доступным сессии пользователя",
                      UserWarning, stacklevel=2)
        return
    if proc.returncode != 0:
        warnings.warn(f"icacls не сузил права {path} (код {proc.returncode}): "
                      f"{(proc.stderr or proc.stdout or '').strip()[:200]}",
                      UserWarning, stacklevel=2)


from dataclasses import dataclass
import hashlib
from typing import Iterable


@dataclass(frozen=True, slots=True)
class AuthPrincipal:
    """Verified caller identity derived strictly from server-side credential or token scope (never headers)."""
    role: str  # "owner" | "agent"
    agent_id: str | None = None
    scopes: frozenset[str] = frozenset()


class TokenAuth:
    """Один статический токен владельца на инсталляцию + изолированные scoped-токены для агентов (P2.7)."""

    def __init__(self, data_dir: Path, *, announce: bool = True):
        self.path = Path(data_dir) / TOKEN_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.token, created = self._load_or_create()
        self._agent_tokens: dict[str, dict] = {}
        if announce:
            self.announce(created)

    def _load_or_create(self) -> tuple[str, bool]:
        if self.path.exists():
            value = self.path.read_text(encoding="utf-8").strip()
            if value:
                # Токен мог быть создан версией без ACL — лечим при старте.
                _restrict_to_owner(self.path)
                return value, False
        token = _secrets.token_urlsafe(32)
        try:
            # O_EXCL: два первых запуска одновременно (два ярлыка) не должны
            # переписать токен друг друга — иначе в памяти сервера один токен,
            # а в файле другой, и вход с токеном из файла получает 401.
            fd = os.open(self.path, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
        except FileExistsError:
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                value = self.path.read_text(encoding="utf-8").strip()
                if value:
                    return value, False          # соперник успел первым — его токен
                time.sleep(0.02)
            # Пустой файл, который никто не дописывает, — прежнее поведение.
            fd = os.open(self.path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(token)
        _restrict_to_owner(self.path)  # Windows: режим os.open ACL не ставит
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

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8", "ignore")).hexdigest()

    def issue_agent_token(
        self,
        agent_id: str,
        scopes: Iterable[str] = ("agent.session", "read"),
        *,
        ttl_s: int = 3600,
    ) -> str:
        """Выпустить короткоживущий scoped-токен для агента/воркера (НИКОГДА не равен owner token)."""
        clean_agent_id = str(agent_id or "agent").strip()[:120] or "agent"
        allowed_scopes = frozenset(
            str(s).strip()
            for s in scopes
            if str(s).strip() and str(s).strip().lower() not in ("*", "owner", "auto", "bypass_permissions")
        )
        raw_token = "agtok_" + _secrets.token_urlsafe(24)
        digest = self._hash_token(raw_token)
        now = time.time()
        self._agent_tokens[digest] = {
            "agent_id": clean_agent_id,
            "scopes": allowed_scopes,
            "created_at": now,
            "expires_at": now + max(60, int(ttl_s)),
        }
        return raw_token

    def revoke_agent_token(self, token_or_agent_id: str) -> bool:
        """Отозвать токен агента по значению токена или по agent_id."""
        if not token_or_agent_id:
            return False
        digest = self._hash_token(token_or_agent_id)
        if digest in self._agent_tokens:
            del self._agent_tokens[digest]
            return True
        removed = False
        for k in [k for k, v in self._agent_tokens.items() if v.get("agent_id") == token_or_agent_id]:
            del self._agent_tokens[k]
            removed = True
        return removed

    def resolve_principal(self, token: str | None) -> AuthPrincipal | None:
        """Определить роль вызывающего строго по проверенному токену, а не по HTTP-заголовкам."""
        if not token:
            return None
        if hmac.compare_digest(token.encode("utf-8", "ignore"), self.token.encode()):
            return AuthPrincipal(role="owner", agent_id=None, scopes=frozenset({"*"}))
        digest = self._hash_token(token)
        entry = self._agent_tokens.get(digest)
        if entry is None:
            return None
        if float(entry.get("expires_at", 0)) < time.time():
            self._agent_tokens.pop(digest, None)
            return None
        return AuthPrincipal(
            role="agent",
            agent_id=str(entry.get("agent_id") or "agent"),
            scopes=frozenset(entry.get("scopes") or ()),
        )

    def check(self, token: str | None) -> bool:
        if not token:
            return False
        # сравнение в байтах: строки с не-ASCII compare_digest не принимает
        return hmac.compare_digest(token.encode("utf-8", "ignore"), self.token.encode())

