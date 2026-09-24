#!/usr/bin/env python3
"""Bossman 1.5 free-provider expansion preflight.

This tool expands capacity without turning "free" into a loophole:
- it never creates accounts, accepts terms, solves CAPTCHAs or generates keys;
- it never rotates accounts/organizations to evade a provider's limits;
- it only probes providers for which the owner supplied a key;
- a provider is eligible for zero-cost worker routing only after an explicit
  local free-tier confirmation flag is present;
- unknown pricing remains BLOCKED, never assumed free.

It is safe to run before the owner creates any extra accounts: status/onboarding
are local-only.  `probe` performs GET /models against configured providers.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = (
    HERE / "config" / "v1.5" / "provider-pool.json"
    if (HERE / "config" / "v1.5" / "provider-pool.json").is_file()
    else ROOT / "config" / "v1.5" / "provider-pool.json"
)
SECRET_FILE = Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "secrets" / "provider-pool.env"
OPENROUTER_SECRET = Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "secrets" / "openrouter-test.env"
MAX_MODELS = 100


def load(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    providers = data.get("providers")
    if not isinstance(providers, list) or not providers:
        raise ValueError("provider pool has no providers")
    seen = set()
    for p in providers:
        pid = str(p.get("id") or "")
        if not pid or pid in seen:
            raise ValueError("provider ids must be unique and non-empty")
        seen.add(pid)
        base = str(p.get("base_url") or "")
        if not base.startswith("https://"):
            raise ValueError(f"{pid}: cloud base_url must be https")
        if not str(p.get("key_env") or ""):
            raise ValueError(f"{pid}: key_env missing")
    policy = data.get("policy") or {}
    if policy.get("auto_signup") is not False or policy.get("multi_account_limit_evasion") is not False:
        raise ValueError("provider-pool policy must forbid automatic signup and quota evasion")
    return data


def _env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        rows = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for raw in rows:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key and value:
            out[key] = value
    return out


def key_for(provider: dict[str, Any]) -> str:
    name = str(provider["key_env"])
    direct = os.environ.get(name, "").strip()
    if direct:
        return direct
    for path in (SECRET_FILE, OPENROUTER_SECRET):
        value = _env_file(path).get(name, "").strip()
        if value:
            return value
    return ""


def confirm_env(provider: dict[str, Any]) -> str:
    return "BOSSMAN_PROVIDER_" + str(provider["id"]).upper().replace("-", "_") + "_FREE_OK"


def owner_confirmed_free(provider: dict[str, Any]) -> bool:
    return os.environ.get(confirm_env(provider), "").strip().lower() in {"1", "true", "yes", "on"}


def status(data: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for p in data["providers"]:
        key_present = bool(key_for(p))
        confirmed = owner_confirmed_free(p)
        rows.append({
            "id": p["id"],
            "base_url": p["base_url"],
            "key_env": p["key_env"],
            "key_present": key_present,
            "owner_confirmed_free": confirmed,
            "zero_cost_worker_eligible": key_present and confirmed,
            "signup_url": p.get("signup_url"),
            "tier": p.get("tier"),
            "priority": int(p.get("priority") or 999),
            "free_evidence": p.get("free_evidence") or {},
            "account_state": (
                "READY_FOR_ZERO_COST_PROBE" if key_present and confirmed
                else "OWNER_CONFIRM_FREE_TIER" if key_present
                else "OWNER_REQUIRED"
            ),
            "free_confirmation_env": confirm_env(p),
        })
    return {
        "schema": "bossman.v1.5.provider-pool-status/1",
        "policy": data["policy"],
        "providers": rows,
        "ready_zero_cost_providers": [r["id"] for r in rows if r["zero_cost_worker_eligible"]],
    }


def _models_url(base: str) -> str:
    return base.rstrip("/") + "/models"


def probe_one(provider: dict[str, Any], *, timeout: float = 20.0) -> dict[str, Any]:
    key = key_for(provider)
    row: dict[str, Any] = {
        "id": provider["id"],
        "key_present": bool(key),
        "owner_confirmed_free": owner_confirmed_free(provider),
        "url": _models_url(str(provider["base_url"])),
        "status": "OWNER_REQUIRED" if not key else "NOT_RUN",
        "models": [],
        "rate_limit_headers": {},
    }
    if not key:
        return row
    req = urllib.request.Request(row["url"], method="GET")
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "bossman-v15-provider-pool/1")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - allowlisted config, https enforced
            raw = resp.read(2_000_001)
            code = resp.status
            headers = dict(resp.headers.items())
    except urllib.error.HTTPError as exc:
        code = exc.code
        raw = exc.read(64_000)
        headers = dict((exc.headers or {}).items())
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        row.update(status="UNAVAILABLE", error=type(exc).__name__, latency_ms=round((time.monotonic()-started)*1000))
        return row
    row["http_status"] = code
    row["latency_ms"] = round((time.monotonic() - started) * 1000)
    for k, v in headers.items():
        if k.lower().startswith(("x-ratelimit", "ratelimit", "retry-after")):
            row["rate_limit_headers"][k] = str(v)[:200]
    if not 200 <= code < 300:
        row["status"] = "KEY_REJECTED" if code in (401, 403) else "HTTP_ERROR"
        return row
    if len(raw) > 2_000_000:
        row["status"] = "RESPONSE_TOO_LARGE"
        return row
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        row["status"] = "BAD_JSON"
        return row
    items = body.get("data") if isinstance(body, dict) else None
    if not isinstance(items, list):
        row["status"] = "BAD_SCHEMA"
        return row
    rows = []
    for item in items[:MAX_MODELS]:
        if isinstance(item, dict) and item.get("id"):
            rows.append(str(item["id"])[:200])
    row["models"] = rows
    row["status"] = "AVAILABLE_CONFIRMED_FREE" if row["owner_confirmed_free"] else "AVAILABLE_UNCONFIRMED_COST"
    return row


def write_template(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Bossman provider pool — local owner file. NEVER commit this file.",
        "# Add only keys for accounts you personally created/accepted.",
        "",
    ]
    for p in data["providers"]:
        lines += [
            f"# {p['id']}: {p.get('signup_url') or ''}",
            f"{p['key_env']}=",
            f"{confirm_env(p)}=0",
            "",
        ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("onboarding")
    p = sub.add_parser("probe")
    p.add_argument("--timeout", type=float, default=20.0)
    t = sub.add_parser("write-template")
    t.add_argument("--out", default=str(SECRET_FILE))
    ns = ap.parse_args(argv)
    data = load(Path(ns.config))
    if ns.cmd == "status":
        print(json.dumps(status(data), ensure_ascii=False, indent=2))
        return 0
    if ns.cmd == "onboarding":
        st = status(data)
        rows = []
        for row in sorted(st["providers"], key=lambda x: (x.get("priority", 999), x["id"])):
            if row["zero_cost_worker_eligible"]:
                continue
            rows.append({
                "provider": row["id"],
                "priority": row.get("priority"),
                "account_state": row["account_state"],
                "signup_url": row.get("signup_url"),
                "key_env": row["key_env"],
                "free_confirmation_env": row["free_confirmation_env"],
                "free_evidence": row.get("free_evidence") or {},
                "owner_actions": [
                    "open the official signup page",
                    "owner reviews/accepts provider terms and any CAPTCHA",
                    "owner creates/copies the API key",
                    "store the key only in Bossman's local provider-pool secret file or environment",
                    "set the provider FREE_OK flag only after the owner confirms the account/tier is intended for zero-cost use",
                    "run provider probe and record actual rate-limit headers",
                ],
                "forbidden": [
                    "automatic acceptance of Terms of Service",
                    "CAPTCHA bypass",
                    "creating additional accounts to evade quotas",
                    "automatic recharge or paid upgrade",
                ],
            })
        print(json.dumps({"schema": "bossman.v1.5.provider-onboarding/1",
                          "parallel_nonblocking": True, "items": rows}, ensure_ascii=False, indent=2))
        return 0
    if ns.cmd == "write-template":
        out = Path(ns.out)
        if out.resolve().is_relative_to(ROOT.resolve()):
            raise SystemExit("refusing to write provider secrets template inside the repository")
        write_template(data, out)
        print(json.dumps({"status": "TEMPLATE_WRITTEN", "path": str(out)}, ensure_ascii=False))
        return 0
    rows = [probe_one(p, timeout=ns.timeout) for p in data["providers"]]
    result = {
        "schema": "bossman.v1.5.provider-probe/1",
        "providers": rows,
        "eligible": [r["id"] for r in rows if r["status"] == "AVAILABLE_CONFIRMED_FREE"],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["eligible"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
