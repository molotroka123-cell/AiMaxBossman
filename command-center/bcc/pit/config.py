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
import time
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

# OWNER DECISION 2026-10-17: Jeff answers ONLY through a local (loopback) model — no cloud chat model, no cloud
# fallback, no cloud spending. ``load()`` (the one production path: bossman pit start / web / calls) enforces it:
# configured cloud models are ignored and reported, the provider key is dropped, the cloud budget is 0.
# The owner picks the model with config.json ``local_url`` + ``local_models`` (first entry) or
# BOSSMAN_PIT_LOCAL_URL / BOSSMAN_PIT_LOCAL_MODELS; without one Jeff uses the local uncensored default below.
JEFF_LOCAL_ONLY = True
DEFAULT_LOCAL_URL = "http://127.0.0.1:11434/v1"
DEFAULT_LOCAL_MODEL = "bossman-uncensored-chat:latest"

# OWNER DECISION 2026-10-18 (resource governor): while a photo/video generation runs the local model must stay unloaded, so
# Jeff may answer through FREE cloud models for that time only. The only way in is a file written by the resource governor
# (tools/resource_governor): ``<pit home>/generation-cloud.json`` with a fresh heartbeat. A missing, stale, malformed or
# non-free file means nothing changes and ``JEFF_LOCAL_ONLY`` stays in force. The runtime re-checks the heartbeat on
# every turn (``generation_cloud_fresh``), so a dead governor ends cloud answers within the TTL; it never falls back to
# the local model (that is what the governor keeps unloaded).
GENERATION_CLOUD_FILE = "generation-cloud.json"
GENERATION_CLOUD_VIOLATION_FILE = "generation-cloud-violation.json"
GENERATION_CLOUD_SCHEMA = "bossman.pit.generation-cloud/1"
GENERATION_CLOUD_TTL_S = 300.0
GENERATION_CLOUD_MAX_MODELS = 5
GENERATION_CLOUD_MARKER_RU = "(облако, бесплатно)"

BEHAVIOR_SCALE_NAMES = (
    "initiative", "curiosity", "depth", "brevity",
    "warmth", "humor", "directness", "creativity",
)


def default_behavior_scales() -> dict[str, int]:
    return {name: 5 for name in BEHAVIOR_SCALE_NAMES}

def is_denied_model(model_id: object) -> bool:
    """Liquid/LFM never answers a participant as Jeff (owner decisions 2026-09-29/10-01); one policy: model_policy."""
    from .model_policy import is_banned_model
    return isinstance(model_id, str) and is_banned_model(model_id.strip())


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
    local_chat_only: bool = False
    local_share_percent: int = 30
    local_fallback_on_cloud_refusal: bool = False
    chat_deadline_seconds: int = 30
    search_url: str = ""
    core_url: str = "http://127.0.0.1:8800"
    max_tokens: int = 2048
    remote_timeout: float = 120.0
    local_timeout: float = 120.0
    catalog_refresh_seconds: int = 900
    discovery_mode: str = "collection_first"
    collection_mode: str = "high_recall"
    allowlist_open: bool = False
    # Zero-cost cloud requests per UTC day across all participants. Free tiers
    # have daily caps; Jeff stops before hammering them (owner audit shows it).
    cloud_daily_request_budget: int = 200
    # Jeff window only (bossman pit web): no Telegram bot token at all, so this
    # configuration can never start a poller. `bossman pit start` refuses it.
    web_only: bool = False
    # Path of the owner's private Telegram block rule (outside Git). Empty uses
    # the default private location; see bcc.pit.blocklist. Never holds IDs.
    blocked_ids_file: str = ""
    behavior_scales: dict[str, int] = field(default_factory=default_behavior_scales)
    # Model ids the owner configured but the model policy refuses (Liquid/LFM ban):
    # dropped at load time on every start/reload and shown in the route status.
    rejected_models: tuple[str, ...] = ()
    # Cloud chat models the owner's config still names but the local-only rule ignores (shown in the route status).
    ignored_cloud_models: tuple[str, ...] = ()
    # True only while the resource governor's generation-cloud file is fresh (see GENERATION_CLOUD_FILE).
    generation_cloud: bool = False
    # Model ids a Bossman provider offers free on the owner's plan (no ':free' id, no live catalog price): the runtime
    # accepts them as zero-cost routes only together with the usage.cost > 0 guard.
    trusted_free_models: tuple[str, ...] = ()
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
        if (not self.chat_models and not self.local_chat_only) or any(
                not isinstance(m, str) or not 0 < len(m.strip()) <= 120 for m in self.chat_models):
            raise ValueError("chat model allowlist must be 1..N exact model ids")
        if len(set(self.chat_models)) != len(self.chat_models):
            raise ValueError("duplicate chat model ids")
        from .model_policy import is_banned_model
        banned = [m for m in (*self.chat_models, *self.local_models) if is_banned_model(m)]
        if banned:
            raise ValueError(f"banned model family (Liquid/LFM, denied for participants) in the model allowlist: {', '.join(banned)}")
        if bool(self.local_url) != bool(self.local_models):
            raise ValueError("local route needs both local_url and local_models")
        if self.local_url:
            local_url(self.local_url)
            if any(not isinstance(m, str) or not 0 < len(m.strip()) <= 120
                   for m in self.local_models):
                raise ValueError("local model ids must be non-empty strings <=120 chars")
            if len(set(self.local_models)) != len(self.local_models):
                raise ValueError("duplicate local model ids")
        if type(self.local_chat_only) is not bool:
            raise ValueError("local_chat_only must be a boolean")
        if self.local_chat_only and len(self.local_models) != 1:
            raise ValueError("local_chat_only needs exactly one configured local model")
        if type(self.local_share_percent) is not int or not 0 <= self.local_share_percent <= 100:
            raise ValueError("local_share_percent must be 0..100")
        if type(self.local_fallback_on_cloud_refusal) is not bool:
            raise ValueError("local_fallback_on_cloud_refusal must be a boolean")
        if type(self.chat_deadline_seconds) is not int or not 10 <= self.chat_deadline_seconds <= 60:
            raise ValueError("chat_deadline_seconds must be 10..60")
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
        if (type(self.cloud_daily_request_budget) is not int
                or not 0 <= self.cloud_daily_request_budget <= 100_000):
            raise ValueError("cloud_daily_request_budget must be 0..100000")
        if type(self.allowlist_open) is not bool:
            raise ValueError("allowlist_open must be a boolean")
        if (not isinstance(self.behavior_scales, dict)
                or set(self.behavior_scales) != set(BEHAVIOR_SCALE_NAMES)
                or any(type(value) is not int or not 1 <= value <= 10
                       for value in self.behavior_scales.values())):
            raise ValueError("behavior_scales must contain exactly eight integer values from 1 to 10")
        if not self.identity_salt:
            raise ValueError("identity salt is required in credentials")
        try:
            raw = bytes.fromhex(self.identity_salt)
        except ValueError:
            raise ValueError("identity salt must be hex") from None
        if len(raw) < 16:
            raise ValueError("identity salt must be at least 16 bytes")
        if type(self.web_only) is not bool:
            raise ValueError("web_only must be a boolean")
        if not isinstance(self.blocked_ids_file, str) or len(self.blocked_ids_file) > 1024:
            raise ValueError("blocked_ids_file must be a path string")
        if self.blocked_ids_file.strip() and looks_like_repo(Path(self.blocked_ids_file).parent):
            raise ValueError("blocked_ids_file must live outside any Git checkout")
        if self.web_only and self.bot_token:
            raise ValueError("web_only configuration must not carry a Telegram bot token")
        if not self.bot_token and not self.web_only:
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
    for retired in ("owner_id", "allowlist", "local_model"):
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
    data["chat_models"] = tuple(data["chat_models"] if "chat_models" in data
                                else DEFAULT_FREE_CHAT_MODELS)
    # Non-secret runtime knobs may be overridden by the environment (owner run
    # helpers), exactly like the companion's env file contract.
    data["local_url"] = os.environ.get("BOSSMAN_PIT_LOCAL_URL", data.get("local_url", "")).strip()
    env_locals = os.environ.get("BOSSMAN_PIT_LOCAL_MODELS", "").strip()
    if env_locals:
        data["local_models"] = tuple(m.strip() for m in env_locals.split(",") if m.strip())
    else:
        data["local_models"] = tuple(data.get("local_models") or ())
    # Owner model policy: a banned model family never survives a (re)load, whether it
    # came from config.json or the environment; only-banned chat lists use the defaults.
    from .model_policy import split_banned
    data["chat_models"], banned_chat = split_banned(data["chat_models"])
    if banned_chat and not data["chat_models"]:
        data["chat_models"] = DEFAULT_FREE_CHAT_MODELS
    data["local_models"], banned_local = split_banned(data["local_models"])
    if banned_local and not data["local_models"]:
        data["local_url"] = ""
    data["rejected_models"] = (*banned_chat, *banned_local)
    data["allowlist_open"] = bool(data.get("allowlist_open", False))
    data["web_only"] = bool(data.get("web_only", False))
    if data["web_only"]:
        # A web-only data dir never holds a bot token, even from the environment.
        data["bot_token"] = ""
    override = generation_cloud_override(path.parent)
    if override is not None:
        apply_generation_cloud(data, override)
    elif JEFF_LOCAL_ONLY:
        enforce_local_only(data)
    return PITSettings(**data)


def generation_cloud_override(home: Path, now: float | None = None) -> dict | None:
    """The governor's generation-cloud file when it is valid and fresh, else None (fail closed to local-only).

    Two shapes: OpenRouter ``:free`` ids on Jeff's own provider, or ``provider.bcc_provider_id`` = a Bossman provider row
    (bcc.db) whose listed models the governor verified as free for the owner's plan (``load`` re-checks that in bcc.db)."""
    home = Path(home)
    if (home / GENERATION_CLOUD_VIOLATION_FILE).exists():
        return None                       # a reply reported usage.cost > 0: the cloud route is closed until the governor clears it
    try:
        raw = (home / GENERATION_CLOUD_FILE).read_bytes()
        if len(raw) > 8192:
            return None
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != GENERATION_CLOUD_SCHEMA:
        return None
    beat = data.get("heartbeat_at")
    if isinstance(beat, bool) or not isinstance(beat, (int, float)):
        return None
    t = time.time() if now is None else now
    if not -5.0 <= t - float(beat) <= GENERATION_CLOUD_TTL_S:
        return None
    models = data.get("models")
    if not isinstance(models, list) or not 1 <= len(models) <= GENERATION_CLOUD_MAX_MODELS:
        return None
    provider = data.get("provider")
    provider_id = None
    if provider is not None:
        pid = provider.get("bcc_provider_id") if isinstance(provider, dict) else None
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            return None
        provider_id = pid
    from .model_policy import is_banned_model
    clean = []
    for m in models:
        if (not isinstance(m, str) or not 0 < len(m.strip()) <= 120 or m != m.strip()
                or is_banned_model(m) or m in clean):
            return None
        if provider_id is None and not m.endswith(":free"):
            return None
        if provider_id is not None and not re.fullmatch(r"[A-Za-z0-9._:/-]{1,120}", m):
            return None
        clean.append(m)
    return {"models": tuple(clean), "heartbeat_at": float(beat), "provider_id": provider_id}


def generation_cloud_fresh(home: Path, now: float | None = None) -> bool:
    return generation_cloud_override(home, now) is not None


def note_generation_cloud_violation(home: Path, model: str, cost: float) -> None:
    """A cloud reply reported usage.cost > 0. Closes the route for every later load/turn and tells the governor."""
    try:
        target = Path(home) / GENERATION_CLOUD_VIOLATION_FILE
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps({"at": time.time(), "model": str(model)[:120], "cost": float(cost)}), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass


def resolve_bcc_free_route(data_dir: Path, provider_id: int, models: tuple[str, ...]) -> tuple[str, str, tuple[str, ...]]:
    """(base_url, api_key, models) of a Bossman provider for the generation cloud. The key is decrypted in memory with
    the backend's own vault and never logged or returned anywhere else. Only models whose bcc.db row is priced 0/0
    and online are kept; nothing usable raises (so Jeff does not start, instead of starting on the local model)."""
    import sqlite3
    from bcc.secrets import Vault
    db = Path(data_dir) / "bcc.db"
    con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    try:
        row = con.execute("select base_url, api_key_enc from providers where id=?", (provider_id,)).fetchone()
        ok = {r[0] for r in con.execute(
            "select name from models where provider_id=? and price_in=0 and price_out=0 and pricing_known=1 "
            "and status='online'", (provider_id,))}
    finally:
        con.close()
    if not row or not row[1]:
        raise ValueError("generation cloud: provider row or key missing")
    base = str(row[0] or "").strip().rstrip("/")
    if urlsplit(base).scheme != "https":
        raise ValueError("generation cloud: provider must be https")
    key = Vault(Path(data_dir)).decrypt(row[1])
    kept = tuple(m for m in models if m in ok)
    if not key or not kept:
        raise ValueError("generation cloud: no usable free model or key")
    return base, key, kept


def apply_generation_cloud(data: dict, override: dict) -> dict:
    """Free cloud chat only: no local model, no local URL (nothing can load Ollama), no cloud-to-local fallback."""
    data["ignored_cloud_models"] = ()
    data["chat_models"] = tuple(override["models"])
    data["local_models"] = ()
    data["local_url"] = ""
    data["local_chat_only"] = False
    data["local_fallback_on_cloud_refusal"] = False
    data["local_share_percent"] = 0
    data["generation_cloud"] = True
    if override.get("provider_id") is not None:
        base, key, kept = resolve_bcc_free_route(Path(data["data_dir"]), override["provider_id"], override["models"])
        data["provider_base_url"], data["provider_key"] = base, key
        data["chat_models"] = kept
        # not ':free' ids and no catalog prices: free for the owner's plan, enforced by the usage.cost guard in the runtime
        data["trusted_free_models"] = kept
        data["max_tokens"] = max(int(data.get("max_tokens") or 0), 2048)       # thinking models spend the budget on reasoning
    return data


def enforce_local_only(data: dict) -> dict:
    """Rewrite loaded settings so Jeff can only reach ONE loopback model (owner decision 2026-10-17).

    - the owner's first configured local model wins (config.json / BOSSMAN_PIT_LOCAL_MODELS); none = the default
      local uncensored model on the default local Ollama URL; a non-loopback URL is refused by PITSettings;
    - every cloud chat model is dropped (``ignored_cloud_models`` names them for the owner), the provider key is
      cleared (no cloud adapter can authenticate), the daily cloud request budget is 0, and no cloud fallback
      after a local refusal."""
    locals_ = [m for m in (data.get("local_models") or ()) if isinstance(m, str) and m.strip()]
    if not data.get("local_url") or not locals_:
        data["local_url"] = data.get("local_url") or DEFAULT_LOCAL_URL
        locals_ = locals_ or [DEFAULT_LOCAL_MODEL]
    data["local_models"] = (locals_[0],)
    data["ignored_cloud_models"] = tuple(m for m in (data.get("chat_models") or ()) if isinstance(m, str))
    data["chat_models"] = ()
    data["local_chat_only"] = True
    data["local_fallback_on_cloud_refusal"] = False
    data["local_share_percent"] = 100
    data["cloud_daily_request_budget"] = 0
    data["provider_key"] = ""
    return data


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
    local_chat_only: bool = False,
    allowlist_open: bool = False,
    web_only: bool = False,
    bot_token: str = "",
    provider_key: str = "",
    core_token: str = "",
    vision_token: str = "",
    proxy: str = "",
) -> None:
    """Create config + encrypted credentials atomically; refuses to overwrite."""
    if path.exists():
        raise CompanionError("CONFIG_EXISTS_EDIT_LOCALLY_WITH_BACKUP")
    if web_only:
        if bot_token:
            raise CompanionError("WEB_ONLY_REFUSES_TELEGRAM_TOKEN")
    elif not bot_token or bot_token.lower().startswith(("replace", "your")):
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
        "web_only": bool(web_only),
    }
    if local_chat_only:
        data["local_chat_only"] = True          # only written when on: other setups keep their exact file
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
        local_chat_only=bool(parsed.get("local_chat_only", False)),
        allowlist_open=bool(parsed.get("allowlist_open", False)),
        web_only=bool(parsed.get("web_only", False)),
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
