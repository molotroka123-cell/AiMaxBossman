"""Local configuration, exact private-chat identities, no credentials in repr."""
from __future__ import annotations

import ipaddress
import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


class CompanionError(RuntimeError):
    """Only stable, secret-free codes are allowed in diagnostic output."""


def local_url(value: str) -> str:
    url = urlsplit(value)
    if url.scheme not in {"http", "https"} or url.username or url.password or url.query or url.fragment:
        raise ValueError("local endpoint: invalid URL")
    try:
        loopback = ipaddress.ip_address(url.hostname or "").is_loopback
        _ = url.port
    except ValueError:
        loopback = False
    if not loopback:
        raise ValueError("local endpoint: explicit loopback IP required")
    return value.rstrip("/")


def positive_id(value: object) -> int:
    if type(value) is not int or not 0 < value < 2**52:
        raise ValueError("identity must be a positive Telegram integer")
    return value


@dataclass(frozen=True)
class Person:
    user_id: int
    chat_id: int
    role: str = "guest"
    agent_id: int | None = None

    def __post_init__(self):
        positive_id(self.user_id)
        positive_id(self.chat_id)
        if self.user_id != self.chat_id or self.role not in {"owner", "guest"}:
            raise ValueError("only explicitly bound private chats are supported")
        if self.agent_id is not None:
            positive_id(self.agent_id)

    @property
    def key(self) -> str:
        return f"{self.user_id}:{self.chat_id}"


@dataclass(frozen=True)
class Settings:
    people: tuple[Person, ...]
    local_url: str = "http://127.0.0.1:8080/v1"
    local_model: str = ""
    core_url: str = "http://127.0.0.1:8800"
    search_url: str = ""  # existing local SearXNG, not a new search daemon
    cloud_model: str = ""  # exact anthropic/claude-* OpenRouter model id
    cloud_daily_usd: float = 0.0
    cloud_request_usd: float = 0.05
    local_timeout: float = 15.0
    max_tokens: int = 512
    monitor_seconds: int = 60
    bot_token: str = field(default="", repr=False)
    core_token: str = field(default="", repr=False)
    cloud_token: str = field(default="", repr=False)
    local_token: str = field(default="", repr=False)
    proxy: str = field(default="", repr=False)

    def __post_init__(self):
        if not 1 <= len(self.people) <= 8 or sum(p.role == "owner" for p in self.people) != 1:
            raise ValueError("exactly one owner and at most seven guests required")
        assigned = [p.agent_id for p in self.people if p.agent_id is not None]
        if len(set(assigned)) != len(assigned):
            raise ValueError("each Telegram principal needs a distinct scoped executor")
        if len({p.key for p in self.people}) != len(self.people):
            raise ValueError("duplicate Telegram identity")
        for url in (self.local_url, self.core_url):
            local_url(url)
        if self.search_url:
            local_url(self.search_url)
        if not 1 <= self.local_timeout <= 60 or not 64 <= self.max_tokens <= 2048:
            raise ValueError("invalid model limits")
        if type(self.monitor_seconds) is not int or not 30 <= self.monitor_seconds <= 3600:
            raise ValueError("monitor interval must be 30..3600 seconds")
        for n in (self.cloud_daily_usd, self.cloud_request_usd):
            if isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) or n < 0:
                raise ValueError("invalid cloud cap")
        if self.cloud_daily_usd > 0 and (not self.cloud_model.startswith("anthropic/claude-") or
                                         not 0 < self.cloud_request_usd <= self.cloud_daily_usd):
            raise ValueError("cloud requires exact Claude model and positive request/day caps")
        if self.proxy:
            u = urlsplit(self.proxy)
            if u.scheme not in {"http", "https"} or not u.hostname or u.fragment or u.query:
                raise ValueError("invalid explicit HTTPS proxy configuration")

    def authorize(self, message: dict) -> Person | None:
        if not isinstance(message, dict):
            return None
        if any(k in message for k in ("forward_origin", "forward_from", "sender_chat")):
            return None  # forwarded material is not an authenticated command
        sender, chat = message.get("from"), message.get("chat")
        if not isinstance(sender, dict) or not isinstance(chat, dict):
            return None
        if sender.get("is_bot") is not False or chat.get("type") != "private":
            return None
        if type(sender.get("id")) is not int or type(chat.get("id")) is not int:
            return None
        return next((p for p in self.people if p.user_id == sender["id"] and p.chat_id == chat["id"]), None)


def load(path: Path) -> Settings:
    raw = path.read_bytes()
    if len(raw) > 65536:
        raise ValueError("configuration too large")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("configuration must be an object")
    data["people"] = tuple(Person(**p) for p in data.get("people", []))
    # Secret values never belong in the public configuration file.
    secret_fields = ("bot_token", "core_token", "cloud_token", "local_token", "proxy")
    if any(k in data for k in secret_fields):
        raise ValueError("use the encrypted secret store or TG_COMPANION_* environment")
    secret_file = path.parent / "credentials.enc"
    secrets = {}
    if secret_file.is_file():
        from bcc.secrets import Vault
        value = Vault(path.parent).decrypt(secret_file.read_text(encoding="utf-8"))
        if value is None:
            raise ValueError("companion credentials cannot be decrypted")
        secrets = json.loads(value)
    for key in secret_fields:
        data[key] = os.environ.get("TG_COMPANION_" + key.upper(), secrets.get(key, ""))
    return Settings(**data)
