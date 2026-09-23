"""Cloud model keys from the terminal — stored by the BACKEND, encrypted.

The key travels once, in a request body, to the running Bossman, which
encrypts it in its vault (Fernet, key file in the data dir) — the same store
the web UI writes. It survives restarts because the backend persists it; the
terminal keeps nothing but, for a key the owner DECLINED to import, a salted
HMAC so it does not ask again. The key is never printed, never logged, never
accepted in argv; the only thing shown is the backend's mask («…last4»).

Comparison with a stored key is by that mask only — no plaintext is ever
fetched from the backend.
"""
from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import os
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .api_client import BossmanError, Client
from .console import sanitize
from .records import EXIT_OK, EXIT_USAGE, record


@dataclass(frozen=True)
class Vendor:
    name: str
    title: str
    kind: str                 # provider adapter kind
    base_url: str
    env: tuple[str, ...]
    via_openrouter: bool = False


VENDORS: dict[str, Vendor] = {
    "anthropic": Vendor("anthropic", "Anthropic (Claude)", "anthropic", "https://api.anthropic.com",
                        ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY")),
    "openrouter": Vendor("openrouter", "OpenRouter", "openai_compat", "https://openrouter.ai/api/v1",
                         ("OPENROUTER_API_KEY",), via_openrouter=True),
    "openai": Vendor("openai", "OpenAI", "openai_compat", "https://api.openai.com/v1",
                     ("OPENAI_API_KEY",)),
    "gemini": Vendor("gemini", "Google Gemini", "openai_compat",
                     "https://generativelanguage.googleapis.com/v1beta/openai",
                     ("GEMINI_API_KEY", "GOOGLE_API_KEY")),
}
ALIASES = {"claude": "anthropic", "google": "gemini"}


def vendor_of(name: str | None) -> Vendor:
    key = ALIASES.get(str(name or "").lower(), str(name or "").lower())
    if key not in VENDORS:
        raise UsageErrorLike(f"неизвестный провайдер «{sanitize(name)}»; доступны: " + ", ".join(VENDORS))
    return VENDORS[key]


class UsageErrorLike(BossmanError):
    def __init__(self, message: str):
        super().__init__(message, kind="usage")


def mask(key: str) -> str:
    """Same mask as the backend (bcc.secrets.mask)."""
    return "…" + key[-4:] if len(key) > 4 else "…"


def _host(url: str) -> str:
    return (urlsplit(url or "").hostname or "").lower()


def find_provider(providers: list[dict], vendor: Vendor, base_url: str | None = None) -> dict | None:
    want = _host(base_url or vendor.base_url)
    for p in providers:
        if p.get("kind") != vendor.kind:
            continue
        if _host(p.get("base_url") or "") == want or (vendor.kind == "anthropic" and not p.get("base_url")
                                                       and want == _host(vendor.base_url)):
            return p
    return None


# ----------------------------------------------------------------- decline memory


def _config_path(data_dir: Path) -> Path:
    return Path(data_dir) / "terminal" / "config.json"


def _load_config(data_dir: Path) -> dict:
    try:
        data = json.loads(_config_path(data_dir).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_config(data_dir: Path, data: dict) -> None:
    path = _config_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _fingerprint(config: dict, env_name: str, key: str) -> str:
    salt = config.get("salt")
    if not isinstance(salt, str) or len(salt) < 16:
        salt = config["salt"] = secrets.token_hex(16)
    return hmac.new(bytes.fromhex(salt), f"{env_name}\0{key}".encode("utf-8"), hashlib.sha256).hexdigest()


def was_declined(data_dir: Path, env_name: str, key: str) -> bool:
    config = _load_config(data_dir)
    if not config.get("salt"):
        return False
    return _fingerprint(config, env_name, key) in set(config.get("declined_env_keys") or [])


def remember_declined(data_dir: Path, env_name: str, key: str) -> None:
    config = _load_config(data_dir)
    fp = _fingerprint(config, env_name, key)
    declined = [d for d in config.get("declined_env_keys") or [] if isinstance(d, str)]
    if fp not in declined:
        declined.append(fp)
    config["declined_env_keys"] = declined[-50:]
    _save_config(data_dir, config)


# ----------------------------------------------------------------- operations


def store_key(client: Client, vendor: Vendor, key: str, *, base_url: str | None = None,
              register_models: bool = True) -> dict:
    """Create or update the vendor's provider with `key` through the API, then
    register the models its catalog lists. Returns a secret-free summary."""
    key = key.strip()
    if not key or any(ch in key for ch in "\r\n\t "):
        raise UsageErrorLike("пустой или многострочный ключ")
    if vendor.via_openrouter:
        body = {"api_key": key}
        if base_url:
            body["base_url"] = base_url
        res = client.post("/api/openrouter/connect", body)
        return {"vendor": vendor.name, "provider_id": res.get("provider_id"), "created": res.get("created"),
                "mask": mask(key), "models_registered": res.get("models"), "models": None,
                "catalog_error": res.get("catalog_error")}
    providers = client.get("/api/providers") or []
    existing = find_provider(providers, vendor, base_url)
    if existing is None:
        row = client.post("/api/providers", {"name": vendor.title, "kind": vendor.kind,
                                             "base_url": base_url or vendor.base_url, "api_key": key})
        created = True
    else:
        row = client.patch(f"/api/providers/{existing['id']}/key", {"api_key": key})
        created = False
    provider_id = row.get("id")
    summary = {"vendor": vendor.name, "provider_id": provider_id, "created": created,
               "mask": row.get("api_key_masked"), "models": [], "models_registered": 0}
    if register_models and provider_id is not None:
        try:
            summary.update(register_catalog(client, vendor, provider_id))
        except BossmanError as exc:
            summary["catalog_error"] = exc.message
    return summary


def register_catalog(client: Client, vendor: Vendor, provider_id: int) -> dict:
    cat = client.get(f"/api/providers/{provider_id}/catalog") or {}
    known_aliases = {m.get("alias") for m in client.get("/api/models") or []}
    added, available = [], []
    for item in cat.get("models") or []:
        mid = str(item.get("id") or "")
        if not mid:
            continue
        available.append(mid)
        if item.get("registered"):
            continue
        alias = mid if mid not in known_aliases else f"{vendor.name}:{mid}"
        try:
            client.post("/api/models", {"provider_id": provider_id, "name": mid, "alias": alias,
                                        "kind": "cloud"})
            known_aliases.add(alias)
            added.append(alias)
        except BossmanError:
            continue
    return {"models": available, "models_registered": len(added), "models_added": added}


def remove_key(client: Client, vendor: Vendor, *, base_url: str | None = None) -> dict:
    providers = client.get("/api/providers") or []
    existing = find_provider(providers, vendor, base_url)
    if existing is None:
        raise BossmanError(f"провайдер {vendor.title} не заведён", kind="not_found")
    row = client.delete(f"/api/providers/{existing['id']}/key")
    return {"vendor": vendor.name, "provider_id": existing["id"], "mask": row.get("api_key_masked")}


def env_candidates(client: Client) -> list[dict]:
    """Keys present in THIS process environment and not yet stored (by mask)."""
    providers = client.get("/api/providers") or []
    found = []
    for vendor in VENDORS.values():
        for env_name in vendor.env:
            key = (os.environ.get(env_name) or "").strip()
            if not key:
                continue
            existing = find_provider(providers, vendor)
            stored = (existing or {}).get("api_key_masked")
            found.append({"vendor": vendor, "env": env_name, "key": key,
                          "already_stored": stored == mask(key)})
            break                       # one env name per vendor is enough
    return found


def list_keys(client: Client) -> list[dict]:
    out = []
    for p in client.get("/api/providers") or []:
        out.append({"id": p.get("id"), "name": sanitize(p.get("name")), "kind": p.get("kind"),
                    "host": _host(p.get("base_url") or "") or None,
                    "key": p.get("api_key_masked") or None})
    return out


def read_secret(prompt: str, *, from_stdin: bool) -> str:
    if from_stdin:
        return (sys.stdin.readline() if sys.stdin else "").strip()
    if not (sys.stdin and sys.stdin.isatty()):
        raise UsageErrorLike("нет терминала для скрытого ввода; используйте --stdin")
    return getpass.getpass(prompt).strip()


def run_keys(client: Client, out, args) -> int:
    action = args.action
    if action == "list":
        items = list_keys(client)
        if out.machine:
            out.json(record("keys", ok=True, items=items, exit_code=EXIT_OK))
        else:
            for it in items:
                out.say(f"  #{it['id']} {it['name']} ({it['kind']}, {it['host'] or '—'}): "
                        f"{'ключ ' + it['key'] if it['key'] else 'ключа нет'}")
            if not items:
                out.say("  провайдеров нет")
        return EXIT_OK
    if action in ("set", "remove"):
        if not args.vendor:
            raise UsageErrorLike(f"укажите провайдера: bossman keys {action} <" + "|".join(VENDORS) + ">")
        vendor = vendor_of(args.vendor)
        if action == "remove":
            res = remove_key(client, vendor, base_url=args.base_url)
            if out.machine:
                out.json(record("keys", ok=True, action="remove", **res, exit_code=EXIT_OK))
            else:
                out.say(f"ключ {vendor.title} удалён из Bossman")
            return EXIT_OK
        key = read_secret(f"Ключ {vendor.title} (ввод скрыт): ", from_stdin=args.stdin)
        res = store_key(client, vendor, key, base_url=args.base_url,
                        register_models=not args.no_models)
        key = ""
        if out.machine:
            out.json(record("keys", ok=True, action="set", **res, exit_code=EXIT_OK))
        else:
            _say_stored(out, vendor, res)
        return EXIT_OK
    if action == "import-env":
        found = [f for f in env_candidates(client) if not f["already_stored"]]
        done = []
        for f in found:
            vendor = f["vendor"]
            if not args.yes:
                if not (sys.stdin and sys.stdin.isatty()):
                    raise UsageErrorLike("импорт без терминала требует --yes")
                if not ask_import(out, f, client.target.data_dir):
                    continue
            res = store_key(client, vendor, f["key"], register_models=not args.no_models)
            done.append({"env": f["env"], **res})
            if not out.machine:
                _say_stored(out, vendor, res)
        if out.machine:
            out.json(record("keys", ok=True, action="import-env", imported=done,
                            skipped=[f["env"] for f in env_candidates(client) if f["already_stored"]],
                            exit_code=EXIT_OK))
        elif not found:
            out.say("новых ключей в окружении нет")
        return EXIT_OK
    return EXIT_USAGE


def ask_import(out, found: dict, data_dir: Path) -> bool:
    """One line, once. A declined key is remembered by salted HMAC only."""
    env_name, key = found["env"], found["key"]
    if was_declined(data_dir, env_name, key):
        return False
    sys.stdout.write(f"Найден {env_name} в этом окне — сохранить в Bossman (зашифровано, сохранится "
                     f"после перезагрузки)? [Y/n] ")
    sys.stdout.flush()
    answer = (sys.stdin.readline() if sys.stdin else "").strip().lower()
    if answer in ("", "y", "yes", "д", "да"):
        return True
    remember_declined(data_dir, env_name, key)
    return False


def _say_stored(out, vendor: Vendor, res: dict) -> None:
    out.say(f"ключ {vendor.title} сохранён в Bossman ({res.get('mask') or '…'}), "
            f"провайдер #{res.get('provider_id')}{' создан' if res.get('created') else ' обновлён'}")
    models = res.get("models") or []
    if models:
        out.say("  доступные модели: " + ", ".join(sanitize(m) for m in models[:12])
                + (f" (+{len(models) - 12})" if len(models) > 12 else ""))
        out.say("  выбрать: /models use <модель> в чате")
    elif res.get("catalog_error"):
        out.say(f"  каталог моделей не получен: {sanitize(res['catalog_error'])}")
