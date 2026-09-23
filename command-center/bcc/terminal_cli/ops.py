"""Operations shared by `exec`, `-p` and `chat`: resolve agent/model, submit a
task idempotently, describe the connection. Everything goes through the API."""
from __future__ import annotations

import ipaddress
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from . import CLIENT_VERSION
from .api_client import BossmanError, Client
from .console import sanitize
from .records import model_kind, record

MAX_PROMPT_CHARS = 64_000


def new_request_id() -> str:
    return "cli-" + time.strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(6)


def locality(provider: dict | None, model: dict | None) -> str | None:
    """local / cloud from the provider address (and the model's kind). Unknown
    stays None — «не знаем» is not «локально»."""
    if model and str(model.get("kind") or "") == "cloud":
        return "cloud"
    url = (provider or {}).get("base_url") or ""
    if not url:
        return "cloud" if (provider or {}).get("kind") == "anthropic" else None
    host = (urlsplit(url).hostname or "").strip("[]").lower()
    if host in ("localhost",):
        return "local"
    try:
        return "local" if ipaddress.ip_address(host).is_loopback else "cloud"
    except ValueError:
        return "cloud" if host else None


@dataclass
class Catalog:
    agents: list[dict] = field(default_factory=list)
    models: list[dict] = field(default_factory=list)
    providers: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, client: Client) -> "Catalog":
        return cls(agents=client.get("/api/agents") or [], models=client.get("/api/models") or [],
                   providers=client.get("/api/providers") or [])

    def model(self, model_id: Any) -> dict | None:
        return next((m for m in self.models if m.get("id") == model_id), None)

    def provider(self, provider_id: Any) -> dict | None:
        return next((p for p in self.providers if p.get("id") == provider_id), None)

    def find_model(self, ref: str) -> dict | None:
        ref = str(ref).strip()
        for m in self.models:
            if str(m.get("id")) == ref or m.get("alias") == ref or m.get("name") == ref:
                return m
        low = ref.lower()
        hits = [m for m in self.models if low in str(m.get("alias") or "").lower()
                or low in str(m.get("name") or "").lower()]
        return hits[0] if len(hits) == 1 else None

    def find_agent(self, ref: str) -> dict | None:
        ref = str(ref).strip()
        for a in self.agents:
            if str(a.get("id")) == ref or str(a.get("name") or "") == ref:
                return a
        low = ref.lower()
        hits = [a for a in self.agents if str(a.get("name") or "").lower() == low]
        return hits[0] if len(hits) == 1 else None

    def describe_agent(self, agent: dict | None) -> dict:
        if not agent:
            return {"agent": None, "model": None}
        model = self.model(agent.get("model_id"))
        provider = self.provider((model or {}).get("provider_id"))
        name = (model or {}).get("alias") or (model or {}).get("name")
        return {"agent": {"id": agent.get("id"), "name": sanitize(agent.get("name"))},
                "model": ({"id": model.get("id"), "alias": sanitize(name),
                           "name": sanitize(model.get("name")),
                           "provider_kind": (provider or {}).get("kind"),
                           "locality": locality(provider, model),
                           "context_window": model.get("context_window"),
                           "model_kind": model_kind(model.get("name"))} if model else None)}


def resolve_agent(client: Client, cat: Catalog, *, prompt: str, agent_ref: str | None,
                  model_ref: str | None) -> dict:
    """The executor, chosen by the backend's own preflight (same selection as
    task creation). --model narrows to agents that already use that model; it
    never rewrites an agent from a headless call."""
    agent = None
    if agent_ref:
        agent = cat.find_agent(agent_ref)
        if agent is None:
            raise BossmanError(f"агент «{sanitize(agent_ref)}» не найден", kind="not_found",
                               hint="`bossman list agents`")
    if model_ref:
        model = cat.find_model(model_ref)
        if model is None:
            raise BossmanError(f"модель «{sanitize(model_ref)}» не найдена", kind="not_found",
                               hint="`bossman list models`")
        if agent is not None and agent.get("model_id") != model.get("id"):
            raise BossmanError(f"у агента «{sanitize(agent.get('name'))}» другая модель",
                               kind="usage", hint="в чате: /models use <модель> меняет модель агента "
                                                  "(явное действие владельца)")
        if agent is None:
            users = [a for a in cat.agents if a.get("model_id") == model.get("id") and a.get("enabled")]
            if not users:
                raise BossmanError(f"ни один включённый агент не использует модель "
                                   f"«{sanitize(model.get('alias') or model.get('name'))}»", kind="usage",
                                   hint="выберите модель агенту в чате: /agent <имя>, затем /models use <модель>")
            agent = users[0]
    pre = client.post("/api/tasks/preflight",
                      {"prompt": prompt, "agent_id": agent.get("id") if agent else None})
    if not pre.get("ok"):
        raise BossmanError(pre.get("reason") or "исполнитель недоступен", kind="blocked",
                           code=pre.get("code"), hint=pre.get("hint"))
    chosen = cat.find_agent(str(pre["agent"]["id"])) or pre["agent"]
    return chosen


@dataclass
class Submitted:
    task: dict
    agent: dict
    replayed: bool
    request_id: str


def create_draft(client: Client, *, prompt: str, title: str, agent: dict,
                 request_id: str) -> Submitted:
    if len(prompt) > MAX_PROMPT_CHARS:
        raise BossmanError(f"слишком длинный запрос: {len(prompt)} > {MAX_PROMPT_CHARS} символов",
                           kind="usage")
    body = {"prompt": prompt, "title": (title or prompt)[:300], "agent_id": agent.get("id"),
            "run_now": False, "client_request_id": request_id}
    out = client.post("/api/tasks", body)
    return Submitted(task=out["task"], agent=agent, replayed=bool(out.get("replayed")),
                     request_id=request_id)


def start_run(client: Client, sub: Submitted) -> dict | None:
    """Start the draft ONCE. A replayed submit whose task already ran is only
    followed, never started again (a lost connection must not mean two runs)."""
    task_id = sub.task["id"]
    if sub.replayed:
        detail = client.get(f"/api/tasks/{task_id}")
        if detail.get("runs"):
            return None
    return client.post(f"/api/tasks/{task_id}/run")


def init_record(client: Client, cat: Catalog, agent: dict | None, *, cwd: str,
                session_id: str | None = None, request_id: str | None = None) -> dict:
    ident = client.target.identity or {}
    return record("system", subtype="init", schema="bossman.events.v1",
                  client={"name": "bossman-terminal", "version": CLIENT_VERSION},
                  bossman={"url": client.target.url, "app": ident.get("app"),
                           "version": ident.get("version"), "build_sha": ident.get("build_sha"),
                           "source_identity": ident.get("source_identity"),
                           "started_at": ident.get("started_at"),
                           "data_dir": str(client.target.data_dir)},
                  cwd=cwd, session_id=session_id, request_id=request_id,
                  **cat.describe_agent(agent))


def env_cwd(arg: str | None) -> str:
    return os.path.abspath(arg or os.getcwd())
