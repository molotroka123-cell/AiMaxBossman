"""PIT participant runtime configuration.

Same Bossman data_dir, same secret store pattern as the existing Telegram
companion: config.json holds no secrets; tokens live in the encrypted
credentials store next to it. The PIT root is
``<BOSSMAN_DATA_DIR>/pit-v1.7/`` — never inside a Git checkout.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from bcc.telegram_companion.config import CompanionError, Person, local_url, positive_id

PIT_CONFIG_NAME = "config.json"
PIT_CREDENTIALS_NAME = "credentials.enc"

# Owner-configured zero-cost model allowlist. Runtime eligibility is verified
# against the live provider catalog on every start; a name here is only a
# candidate, never a proof of zero cost.
DEFAULT_FREE_CHAT_MODELS: tuple[str, ...] = (
    "typesafe/jev-1.5",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nex-agi/nex-n2.5-pro:free",
)

_SECRET_ENV = (
    ("bot_token", "BOSSMAN_PIT_BOT_TOKEN"),
    ("provider_key", "BOSSMAN_PIT_PROVIDER_KEY"),
    ("core_token", "BOSSMAN_PIT_CORE_TOKEN"),
    ("vision_token", "BOSSMAN_PIT_VISION_TOKEN"),
)


def default_data_dir() -> Path:
    base = os.environ.get("BOSSMAN_DATA_DIR", "").strip()
    if base:
        return Path(base)
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share"))) / "Bossman" / "CommandCenter"


def pit_home(data_dir: Path) -> Path:
    return Path(data_dir) / "pit-v1.7"


def config_path(data_dir: Path) -> Path:
    return pit_home(data_dir) / PIT_CONFIG_NAME


def credentials_path(data_dir: Path) -> Path:
    return pit_home(data_dir) / PIT_CREDENTIALS_NAME


def looks_like_repo(path: Path) -> bool:
    """Runtime data must never live inside a Git checkout."""
    current = Path(path).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return True
    return False


@dataclass(frozen=True)
class PITSettings:
    """Participant-mode settings. Every participant is zero-start and equal:
    the transport-level owner/guest roles never grant PIT authority."""

    data_dir: Path
    people: tuple[Person, ...]
    chat_models: tuple[str, ...] = DEFAULT_FREE_CHAT_MODELS
    provider_base_url: str = "https://openrouter.ai/api/v1"
    local_url: str = ""
    local_models: tuple[str, ...] = ()
    search_url: str = ""
    core_url: str = "http://127.0.0.1:8800"
    max_tokens: int = 1024
    remote_timeout: float = 120.0
    local_timeout: float = 120.0
    catalog_refresh_seconds: int = 900
    discovery_mode: str = "collection_first"
    collection_mode: str = "high_recall"
    allowlist_open: bool = False
    bot_token: str = field(default="", repr=False)
    provider_key: str = field(default="", repr=False)
    core_token: str = field(default="", repr=False)
    vision_token: str = field(default="", repr=False)
    identity_salt: str = field(default="", repr=False)
    proxy: str = field(default="", repr=False)

    def __post_init__(self):
        if not 1 <= len(self.people) <= 50:
            raise ValueError("PIT allowlist needs 1..50 participants")
        if len({p.key for p in self.people}) != len(self.people):
            raise ValueError("duplicate Telegram identity in PIT allowlist")
        if self.provider_base_url.strip() != self.provider_base_url or not self.provider_base_url:
            raise ValueError("provider base URL invalid")
        url = urlsplit(self.provider_base_url)
        if url.scheme not in {"http", "https"} or url.username or url.password or url.query or url.fragment:
            raise ValueError("provider base URL invalid")
        loopback = False
        try:
            loopback = ipaddress.ip_address(url.hostname or "").is_loopback
        except ValueError:
            loopback = False
        if url.scheme != "https" and not loopback:
            raise ValueError("remote provider endpoint must be https or loopback")
        if not self.chat_models or any(not isinstance(m, str) or not 0 < len(m.strip()) <= 120 for m in self.chat_models):
            raise ValueError("chat model allowlist must be 1..N exact model ids")
        if len(set(self.chat_models)) != len(self.chat_models):
            raise ValueError("duplicate chat model ids")
        if bool(self.local_url) != bool(self.local_models):
            raise ValueError("local route needs both local_url and local_models")
        if self.local_url:
            local_url(self.local_url)
            if any(not isinstance(m, str) or not 0 < len(m.strip()) <= 120
                   for m in self.local_models):
                raise ValueError("local model ids must be non-empty strings <=120 chars")
            if len(set(self.local_models)) != len(self.local_models):
                raise ValueError("duplicate local model ids")
        if self.search_url:
            local_url(self.search_url)
        local_url(self.core_url)
        if (isinstance(self.max_tokens, bool) or not isinstance(self.max_tokens, int)
                or not 64 <= self.max_tokens <= 4096):
            raise ValueError("invalid max_tokens limits")
        if (isinstance(self.remote_timeout, bool) or not isinstance(self.remote_timeout, (int, float))
                or not 10 <= self.remote_timeout <= 600):
            raise ValueError("invalid remote timeout limits")
        if (isinstance(self.catalog_refresh_seconds, bool) or not isinstance(self.catalog_refresh_seconds, int)
                or not 300 <= self.catalog_refresh_seconds <= 86400):
            raise ValueError("invalid catalog refresh interval")
        if self.discovery_mode not in {"off", "balanced", "collection_first"}:
            raise ValueError("invalid discovery mode")
        if self.collection_mode not in {"off", "high_recall"}:
            raise ValueError("invalid collection mode")
        if type(self.allowlist_open) is not bool:
            raise ValueError("allowlist_open must be a boolean")
        if not self.identity_salt:
            raise ValueError("identity salt is required in credentials")
        try:
            raw = bytes.fromhex(self.identity_salt)
        except ValueError:
            raise ValueError("identity salt must be hex") from None
        if len(raw) < 16:
            raise ValueError("identity salt must be at least 16 bytes")
        if not self.bot_token:
            raise ValueError("bot token is required")
        if not self.provider_key and not self.local_models:
            raise ValueError("provider key is required without a local model")

    def participant(self, user_id: int, chat_id: int) -> Person | None:
        for person in self.people:
            if person.user_id == user_id and person.chat_id == chat_id:
                return person
        return None

    def person_by_user(self, user_id: int) -> Person | None:
        for person in self.people:
            if person.user_id == user_id:
                return person
        return None


def _read_credentials(home: Path) -> dict:
    secret_file = home / PIT_CREDENTIALS_NAME
    if not secret_file.is_file():
        return {}
    from bcc.secrets import Vault
    value = Vault(home).decrypt(secret_file.read_text(encoding="utf-8"))
    if value is None:
        raise ValueError("PIT credentials cannot be decrypted")
    data = json.loads(value)
    return data if isinstance(data, dict) else {}


def load(path: Path) -> PITSettings:
    """Load config.json + encrypted credentials + env overrides.

    Secret values never belong in the public configuration file; providing
    them there is refused the same way the companion config refuses them.
    """
    raw = path.read_bytes()
    if len(raw) > 65536:
        raise ValueError("configuration too large")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("configuration must be an object")
    data["data_dir"] = str(data.get("data_dir") or default_data_dir())
    data["people"] = tuple(Person(**p) for p in data.get("people", []))
    for retired in ("owner_id", "allowlist"):
        data.pop(retired, None)
    secret_keys = {"bot_token", "provider_key", "core_token", "vision_token", "identity_salt", "proxy"}
    if secret_keys & set(data):
        raise ValueError("use the encrypted credential store or BOSSMAN_PIT_* environment")
    secrets = _read_credentials(path.parent)
    secrets["identity_salt"] = secrets.get("identity_salt", "")
    for key, env_name in _SECRET_ENV:
        env_value = os.environ.get(env_name, "").strip()
        if env_value:
            secrets[key] = env_value
    if not secrets.get("identity_salt"):
        raise ValueError("PIT credentials missing identity_salt; run setup")
    for key in (*[k for k, _ in _SECRET_ENV], "identity_salt"):
        data[key] = secrets.get(key, "")
    data["chat_models"] = tuple(data.get("chat_models") or DEFAULT_FREE_CHAT_MODELS)
    # Non-secret runtime knobs may be overridden by the environment (owner run
    # helpers), exactly like the companion's env file contract.
    data["local_url"] = os.environ.get("BOSSMAN_PIT_LOCAL_URL", data.get("local_url", "")).strip()
    env_locals = os.environ.get("BOSSMAN_PIT_LOCAL_MODELS", "").strip()
    if env_locals:
        data["local_models"] = tuple(m.strip() for m in env_locals.split(",") if m.strip())
    else:
        data["local_models"] = tuple(data.get("local_models") or ())
    data["allowlist_open"] = bool(data.get("allowlist_open", False))
    return PITSettings(**data)


def save_setup(
    path: Path,
    *,
    people: list[Person],
    chat_models: list[str],
    provider_base_url: str,
    core_url: str,
    search_url: str = "",
    local_url: str = "",
    local_models: list[str] | None = None,
    allowlist_open: bool = False,
    bot_token: str = "",
    provider_key: str = "",
    core_token: str = "",
    vision_token: str = "",
    proxy: str = "",
) -> None:
    """Create config + encrypted credentials atomically; refuses to overwrite."""
    if path.exists():
        raise CompanionError("CONFIG_EXISTS_EDIT_LOCALLY_WITH_BACKUP")
    if not bot_token or bot_token.lower().startswith(("replace", "your")):
        raise CompanionError("TELEGRAM_TOKEN_REQUIRED")
    data: dict = {
        "data_dir": str(path.parent.parent),
        "people": [{"user_id": p.user_id, "chat_id": p.chat_id, "role": p.role} for p in people],
        "chat_models": chat_models,
        "provider_base_url": provider_base_url,
        "core_url": core_url,
        "search_url": search_url,
        "local_url": local_url,
        "local_models": list(local_models or ()),
        "allowlist_open": bool(allowlist_open),
    }
    import secrets as _secrets
    credentials = {
        "bot_token": bot_token,
        "provider_key": provider_key,
        "core_token": core_token,
        "vision_token": vision_token,
        "identity_salt": _secrets.token_hex(32),
    }
    from bcc.secrets import Vault
    from bcc.auth import _restrict_to_owner
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    vault = Vault(path.parent)
    config_text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    # Validate before anything touches disk: a bad allowlist must not half-create a runtime.
    parsed = json.loads(config_text)
    PITSettings(
        data_dir=Path(parsed["data_dir"]),
        people=tuple(Person(**p) for p in parsed["people"]),
        chat_models=tuple(parsed["chat_models"]),
        provider_base_url=parsed["provider_base_url"],
        core_url=parsed["core_url"],
        search_url=parsed.get("search_url", ""),
        local_url=parsed.get("local_url", ""),
        local_models=tuple(parsed.get("local_models") or ()),
        allowlist_open=bool(parsed.get("allowlist_open", False)),
        bot_token=credentials["bot_token"],
        provider_key=credentials["provider_key"],
        core_token=credentials["core_token"],
        vision_token=credentials["vision_token"],
        identity_salt=credentials["identity_salt"],
    )
    secret_path = path.parent / PIT_CREDENTIALS_NAME
    for target, text in ((secret_path, vault.encrypt(json.dumps(credentials, ensure_ascii=False))),
                         (path, config_text)):
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(text)
    _restrict_to_owner(secret_path)
    _restrict_to_owner(vault.path)