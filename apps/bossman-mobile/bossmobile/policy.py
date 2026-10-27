"""LOCAL-ONLY rule of the mobile chat (owner: «без облачных моделей»).

A Bossman chat message runs as a Bossman task of ONE agent. The phone may only name an agent for which every
model that agent can run on is on THIS PC:

* Bossman's own view of the agent's model (GET /api/chat/options, computed by provider_governance) says
  locality_detail == "local" and billing == "local" (loopback provider, not a LAN box, not a local proxy to a cloud);
* the agent's fallback model (GET /api/agents -> fallback_model_id) is absent or also a local model on a loopback
  provider (GET /api/models + /api/providers) - otherwise a provider failure could move the turn to the cloud;
* the agent does not carry permissions.cloud_allowed=true (Bossman's router would then be allowed to pick cloud).

"Auto" (no agent) is refused: Bossman's Auto may pick any enabled agent, cloud ones included.
"""
from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit


def loopback_url(url: str) -> bool:
    host = (urlsplit(str(url or "")).hostname or "").strip("[]").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _model_local(model: dict | None, providers: dict[int, dict]) -> bool:
    if not model:
        return False
    prov = providers.get(model.get("provider_id")) or {}
    return model.get("kind") == "local" and loopback_url(prov.get("base_url", ""))


def _cloud_rank(a: dict) -> tuple:
    """Cloud agents first, GLM 5.3 Flash at the very top, then the other cloud ones; local ones follow (owner 24.10)."""
    text = f"{a.get('name') or ''} {(a.get('model') or {}).get('alias') or ''} {(a.get('model') or {}).get('name') or ''}".lower()
    glm = "glm" in text and "5.3" in text and "flash" in text
    strata_or_argon = any(k in text for k in ("strata", "qwen3.8-flash-next", "argon pc-agent"))
    return (0 if a.get("cloud") else 1, 0 if (glm if a.get("cloud") else strata_or_argon) else 1, (a.get("name") or "").lower())


def local_agents(options: dict, agents: list[dict], models: list[dict], providers: list[dict]) -> list[dict]:
    """Every chat agent with ``local`` (strictly local) and ``allowed`` (may be used from the phone).

    A non-local agent is allowed ONLY when the owner set ``permissions.mobile_cloud_ok = true`` on that agent itself
    (explicit per-agent opt-in, owner 24.10 for GLM 5.3 Flash via 7API); it is then labelled ``cloud`` with its provider.
    Disabled / unusable agents are never allowed."""
    by_agent = {int(a["id"]): a for a in agents if isinstance(a, dict) and "id" in a}
    by_model = {int(m["id"]): m for m in models if isinstance(m, dict) and "id" in m}
    by_prov = {int(p["id"]): p for p in providers if isinstance(p, dict) and "id" in p}
    out = []
    for a in options.get("agents") or []:
        view = a.get("model") or {}
        raw = by_agent.get(int(a.get("id", -1))) or {}
        perms = raw.get("permissions") or {}
        reasons = []
        if not a.get("enabled"):
            reasons.append("выключен")
        name_low = str(a.get("name") or "").strip().lower()
        if "eval500 tournament clone" in name_low or name_low.startswith((
            "bossblocks-", "fresh-vibes-", "vault-acl-", "owner-local-", "swapme",
        )):
            reasons.append("служебный или архивный тестовый агент")
        if name_low == "strata (qwen3.8-flash-next)" and any(
            str(x.get("name") or "").strip().lower() == "argon pc-agent" and x.get("enabled")
            for x in (options.get("agents") or [])
        ):
            reasons.append("объединён в единого рабочего агента Argon PC-Agent")
        if view.get("locality_detail") != "local" or view.get("billing") not in ("local",):
            reasons.append("модель не локальная")
        if view.get("usable") is False:
            reasons.append(view.get("refusal") or "модель недоступна")
        fb = raw.get("fallback_model_id")
        if fb is not None and not _model_local(by_model.get(int(fb)), by_prov):
            reasons.append("запасная модель облачная")
        if perms.get("cloud_allowed") is True:
            reasons.append("агенту разрешено облако")
        if not raw:
            reasons.append("агент не найден")
        local = not reasons
        usable_ok = a.get("enabled") and view.get("usable") is not False and bool(raw)
        opted = perms.get("mobile_cloud_ok") is True and bool(usable_ok)
        model = by_model.get(int(raw["model_id"])) if raw.get("model_id") is not None else None
        prov = by_prov.get(int((model or {}).get("provider_id", -1))) or {}
        out.append({"id": int(a["id"]), "name": a.get("name"), "role": a.get("role") or "",
                    "tools": list(raw.get("tools") or []),
                    "model": {"alias": view.get("alias"), "name": view.get("name"),
                              "context_window": view.get("context_window"), "health": view.get("health")},
                    "local": local, "cloud": (not local) and opted, "allowed": local or opted,
                    "provider": prov.get("name") or "", "why_not": reasons})
    out.sort(key=_cloud_rank)
    return out


def pick_default(allowed: list[dict]) -> int | None:
    """Owner 17.10: prefer the local uncensored Gemma chat agent («установи Gemma в мобильный app»), then any local
    uncensored agent (name says «uncensored»), then the local Qwen/Strata operator with tools (computer.observe), then any
    local agent with tools. Only agents that passed the local-only rule qualify: a cloud agent named «uncensored»
    (or «gemma») is never picked."""
    ok = [a for a in allowed if a["local"]]
    for pred in (lambda a: uncensored_label(a["name"]) and "gemma" in (a["name"] or "").lower(),
                 lambda a: uncensored_label(a["name"]),
                 lambda a: "computer.observe" in a["tools"] and any(k in (a["name"] or "").lower() for k in ("оператор", "strata", "argon")),
                 lambda a: bool(a["tools"]), lambda a: True):
        for a in ok:
            if pred(a):
                return a["id"]
    return None


def is_photo_model(m: dict) -> bool:
    return isinstance(m, dict) and m.get("kind") == "photo"


def uncensored_label(label: Any) -> bool:
    s = str(label or "").lower()
    return "uncensored" in s or " uc" in f" {s}" or "без фильтр" in s
