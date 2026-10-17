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


def local_agents(options: dict, agents: list[dict], models: list[dict], providers: list[dict]) -> list[dict]:
    by_agent = {int(a["id"]): a for a in agents if isinstance(a, dict) and "id" in a}
    by_model = {int(m["id"]): m for m in models if isinstance(m, dict) and "id" in m}
    by_prov = {int(p["id"]): p for p in providers if isinstance(p, dict) and "id" in p}
    out = []
    for a in options.get("agents") or []:
        view = a.get("model") or {}
        raw = by_agent.get(int(a.get("id", -1))) or {}
        reasons = []
        if not a.get("enabled"):
            reasons.append("выключен")
        if view.get("locality_detail") != "local" or view.get("billing") not in ("local",):
            reasons.append("модель не локальная")
        if view.get("usable") is False:
            reasons.append(view.get("refusal") or "модель недоступна")
        fb = raw.get("fallback_model_id")
        if fb is not None and not _model_local(by_model.get(int(fb)), by_prov):
            reasons.append("запасная модель облачная")
        if (raw.get("permissions") or {}).get("cloud_allowed") is True:
            reasons.append("агенту разрешено облако")
        if not raw:
            reasons.append("агент не найден")
        out.append({"id": int(a["id"]), "name": a.get("name"), "role": a.get("role") or "",
                    "tools": list(raw.get("tools") or []),
                    "model": {"alias": view.get("alias"), "name": view.get("name"),
                              "context_window": view.get("context_window"), "health": view.get("health")},
                    "local": not reasons, "why_not": reasons})
    return out


def pick_default(allowed: list[dict]) -> int | None:
    """Prefer the local Qwen operator with tools (computer.observe), then any local agent with tools."""
    ok = [a for a in allowed if a["local"]]
    for pred in (lambda a: "computer.observe" in a["tools"] and "оператор" in (a["name"] or "").lower(),
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
