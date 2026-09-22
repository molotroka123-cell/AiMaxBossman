"""Local configuration, exact private-chat identities, no credentials in repr."""
from __future__ import annotations

import ipaddress
import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


DEFAULT_PERSONA = (
    "Ты — Bossman, локальный ИИ-помощник владельца этого компьютера. Тёплый, но прямой. "
    "Отвечай кратко и по делу, без воды и дежурных фраз. Честно говори, когда не уверен или не знаешь. "
    "Если вопрос неоднозначен — задай один уточняющий вопрос. Отвечай на языке собеседника. "
    "Форматируй для Telegram: короткие абзацы, простые списки, без таблиц и длинной разметки. "
    "Ты не Claude, не Anthropic и не реальный человек — ты Bossman.")


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
    # Optional second local route (e.g. a fast MoE model on another loopback
    # port). Used by /fast and as a LOCAL fallback when MAIN times out, before
    # any cloud consideration. Empty = disabled.
    fast_url: str = ""
    fast_model: str = ""
    fast_timeout: float = 60.0
    # Which local route answers plain messages, and whether MAIN may fall back
    # to FAST. `enabled=False` keeps the saved setup but refuses to serve.
    default_route: str = "main"
    fast_fallback: bool = True
    enabled: bool = True
    # Which local route sees photos: "auto" = first of main/fast whose server
    # advertises vision (llama.cpp /props modalities.vision).
    vision_route: str = "auto"
    # Local image generation through Bossman Studio (owner only by default).
    image_enabled: bool = False
    image_model: str = "sdcpp:z-image-turbo"
    image_size: int = 1024
    image_steps: int = 8
    image_guests: bool = False
    image_deadline: int = 900
    image_min_free_gb: float = 12.0
    # Conversation style and local per-user learning.
    persona: str = DEFAULT_PERSONA
    learning: dict = field(default_factory=dict)      # {"<user_id>": bool}; missing = on
    retention_days: int = 90
    profile_every: int = 10
    owner_priority: bool = True
    # Owner-only computer console in Telegram: menu, Bossman approvals, allowed
    # screenshot, STOP/pause/resume. It never executes anything itself — every
    # effect is a Bossman task or a decision on Bossman's own approval queue.
    # Off by default; guests never get it.
    pc_control: bool = False
    # Owner-only Claude Code bridge (/claude), restored by the owner's decision on
    # 2026-09-22. Off by default; there is still no raw shell from Telegram.
    claude_bridge: bool = False
    claude_cwd: str = ""
    claude_permission_mode: str = "acceptEdits"
    claude_timeout: int = 1800
    codex_bridge: bool = False
    codex_sandbox: str = "workspace-write"
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
        if bool(self.fast_url) != bool(self.fast_model):
            raise ValueError("fast route needs both fast_url and fast_model")
        if self.fast_url:
            local_url(self.fast_url)
        # Large dense local models (e.g. 27B on an iGPU) need minutes, not
        # seconds, for a 512-token answer; 600 s is still a bounded wait.
        for timeout in (self.local_timeout, self.fast_timeout):
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 600:
                raise ValueError("invalid model limits")
        if not 64 <= self.max_tokens <= 2048:
            raise ValueError("invalid model limits")
        if self.default_route not in {"main", "fast"} or (self.default_route == "fast" and not self.fast_model):
            raise ValueError("default route must be main, or fast with a configured fast model")
        if self.vision_route not in {"auto", "main", "fast"} or (self.vision_route == "fast" and not self.fast_model):
            raise ValueError("vision route must be auto, main, or fast with a configured fast model")
        if (type(self.image_enabled) is not bool or type(self.image_guests) is not bool or
                not self.image_model.startswith("sdcpp:") or len(self.image_model) > 120 or
                type(self.image_size) is not int or self.image_size not in {512, 768, 1024} or
                type(self.image_steps) is not int or not 4 <= self.image_steps <= 20 or
                type(self.image_deadline) is not int or not 60 <= self.image_deadline <= 3600 or
                isinstance(self.image_min_free_gb, bool) or not 0 <= self.image_min_free_gb <= 128):
            raise ValueError("invalid image generation settings (local sd.cpp models only)")
        if not isinstance(self.persona, str) or not 20 <= len(self.persona.strip()) <= 3000:
            raise ValueError("persona must be 20..3000 characters")
        if (not isinstance(self.learning, dict) or
                any(not isinstance(k, str) or not k.isdigit() or type(v) is not bool for k, v in self.learning.items())):
            raise ValueError("learning must map Telegram user ids to booleans")
        if type(self.retention_days) is not int or not 1 <= self.retention_days <= 3650:
            raise ValueError("retention must be 1..3650 days")
        if type(self.profile_every) is not int or not 3 <= self.profile_every <= 200 or type(self.owner_priority) is not bool:
            raise ValueError("invalid learning cadence / priority")
        if type(self.fast_fallback) is not bool or type(self.enabled) is not bool:
            raise ValueError("fast_fallback and enabled must be booleans")
        if type(self.pc_control) is not bool:
            raise ValueError("invalid computer console settings")
        from .claude_bridge import CODEX_SANDBOXES, PERMISSION_MODES
        if (type(self.claude_bridge) is not bool or not isinstance(self.claude_cwd, str)
                or type(self.codex_bridge) is not bool or self.codex_sandbox not in CODEX_SANDBOXES
                or self.claude_permission_mode not in PERMISSION_MODES
                or type(self.claude_timeout) is not int or not 60 <= self.claude_timeout <= 7200):
            raise ValueError("invalid Claude bridge settings")
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
    # Retired with the direct execution path (/sh, process launch). A configuration
    # saved before that removal must still load — but the field grants nothing.
    # The claude_* fields came back with the owner-only Claude bridge; they act
    # only together with claude_bridge: true.
    for retired in ("bossman_launch",):
        data.pop(retired, None)
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
