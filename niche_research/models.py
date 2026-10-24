"""Pluggable model clients and the router.

Order: local Ollama (only a model that is already loaded by default — a GPU job may be running) -> free cloud
(OpenRouter ``:free`` Nemotron Ultra, Gemini Flash preview via the OpenAI-compatible endpoint) -> optional paid
provider. Paid calls are refused unless ``paid_usd_limit`` > 0 in the data-dir config; the limit is a per-day USD
cap clamped to $2 (owner approval 24.10 for 7API GLM 5.3 Flash). Every call — ok or failed — is written to the
spend log with provider, model, tokens and cost (0 for free/local). API keys are read from the key file in code
and never printed, logged or put into error text.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol

from niche_research import USER_AGENT
from niche_research.config import Config, read_keys


class ModelError(RuntimeError):
    pass


class PaidSpendRefused(ModelError):
    pass


class NoModelAvailable(ModelError):
    pass


@dataclass
class ModelReply:
    text: str
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    paid: bool

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


class ModelClient(Protocol):
    provider: str
    model: str
    paid: bool

    def available(self) -> bool: ...

    def complete(self, system: str, prompt: str, max_tokens: int) -> ModelReply: ...


def _estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // 4)


def _post_json(url: str, body: dict, headers: dict, timeout: float) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": USER_AGENT, **headers})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class OllamaClient:
    provider = "ollama"
    paid = False

    def __init__(self, base_url: str, preferred: str = "", only_loaded: bool = True, timeout: float = 300):
        self.base_url = base_url.rstrip("/")
        self.preferred = preferred
        self.only_loaded = only_loaded
        self.timeout = timeout
        self.model = preferred or ""
        self._checked: bool | None = None

    def _get(self, path: str) -> dict:
        req = urllib.request.Request(self.base_url + path, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def available(self) -> bool:
        if self._checked is not None:
            return self._checked
        try:
            installed = [m.get("name") for m in self._get("/api/tags").get("models", [])]
            candidates = installed
            if self.only_loaded:
                candidates = [m.get("name") for m in self._get("/api/ps").get("models", [])]
            if self.preferred and self.preferred in candidates:
                self.model = self.preferred
            elif candidates and not self.preferred:
                self.model = candidates[0]
            else:
                self.model = ""
            self._checked = bool(self.model)
        except (OSError, ValueError):
            self._checked = False
        return self._checked

    def complete(self, system: str, prompt: str, max_tokens: int) -> ModelReply:
        try:
            out = _post_json(self.base_url + "/api/chat", {
                "model": self.model, "stream": False,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                "options": {"num_predict": max_tokens}}, {}, self.timeout)
        except urllib.error.HTTPError as exc:
            raise ModelError(f"ollama HTTP {exc.code}") from None
        except OSError as exc:
            raise ModelError(f"ollama unreachable: {type(exc).__name__}") from None
        text = (out.get("message") or {}).get("content") or ""
        if not text.strip():
            raise ModelError("ollama empty answer")
        return ModelReply(text, self.provider, self.model, int(out.get("prompt_eval_count") or _estimate_tokens(prompt)),
                          int(out.get("eval_count") or _estimate_tokens(text)), 0.0, False)


class OpenAICompatClient:
    def __init__(self, provider: str, base_url: str, model: str, api_key: str, *, paid: bool = False,
                 usd_per_mtok_in: float = 0.0, usd_per_mtok_out: float = 0.0, timeout: float = 180):
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._key = api_key
        self.paid = paid
        self.usd_in = usd_per_mtok_in
        self.usd_out = usd_per_mtok_out
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self._key)

    def estimate_cost(self, prompt: str, max_tokens: int) -> float:
        return (_estimate_tokens(prompt) * self.usd_in + max_tokens * self.usd_out) / 1e6

    def _redact(self, text: str) -> str:
        return text.replace(self._key, "***") if self._key else text

    def complete(self, system: str, prompt: str, max_tokens: int) -> ModelReply:
        headers = {"Authorization": f"Bearer {self._key}"}
        if self.provider == "openrouter":
            headers["X-Title"] = "BossmanNicheResearch"
        try:
            out = _post_json(self.base_url + "/chat/completions", {
                "model": self.model, "max_tokens": max_tokens, "temperature": 0.3,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]},
                headers, self.timeout)
        except urllib.error.HTTPError as exc:
            raise ModelError(self._redact(f"{self.provider} HTTP {exc.code}")) from None
        except OSError as exc:
            raise ModelError(self._redact(f"{self.provider} unreachable: {type(exc).__name__}")) from None
        except ValueError:
            raise ModelError(f"{self.provider} returned non-JSON") from None
        choices = out.get("choices") or []
        text = ((choices[0].get("message") or {}).get("content") if choices else "") or ""
        if not text.strip():
            raise ModelError(f"{self.provider} empty answer")
        usage = out.get("usage") or {}
        t_in = int(usage.get("prompt_tokens") or _estimate_tokens(system + prompt))
        t_out = int(usage.get("completion_tokens") or _estimate_tokens(text))
        cost = (t_in * self.usd_in + t_out * self.usd_out) / 1e6 if self.paid else 0.0
        return ModelReply(text, self.provider, self.model, t_in, t_out, cost, self.paid)


class FakeModel:
    """Deterministic test client: ``responder(system, prompt) -> str``."""

    def __init__(self, responder: Callable[[str, str], str], provider: str = "fake", model: str = "fake-1",
                 paid: bool = False, usd_per_mtok: float = 0.0, fail_times: int = 0):
        self.responder = responder
        self.provider = provider
        self.model = model
        self.paid = paid
        self.usd = usd_per_mtok
        self.calls = 0
        self.fail_times = fail_times

    def available(self) -> bool:
        return True

    def estimate_cost(self, prompt: str, max_tokens: int) -> float:
        return (_estimate_tokens(prompt) + max_tokens) * self.usd / 1e6

    def complete(self, system: str, prompt: str, max_tokens: int) -> ModelReply:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ModelError(f"{self.provider} simulated failure")
        text = self.responder(system, prompt)
        t_in, t_out = _estimate_tokens(system + prompt), _estimate_tokens(text)
        cost = (t_in + t_out) * self.usd / 1e6 if self.paid else 0.0
        return ModelReply(text, self.provider, self.model, t_in, t_out, cost, self.paid)


def utc_day_start(now: float) -> float:
    d = datetime.fromtimestamp(now, tz=timezone.utc)
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()


class ModelRouter:
    """Tries clients in order; enforces the paid cap and the free-call budget; logs every call."""

    def __init__(self, store, config: Config, clients: list, clock: Callable[[], float] = time.time):
        self.store = store
        self.config = config
        self.clients = list(clients)
        self.clock = clock

    def paid_allowed(self, client, prompt: str, max_tokens: int) -> tuple[bool, str]:
        limit = self.config.paid_daily_limit
        if limit <= 0:
            return False, "paid_usd_limit is 0 (default): paid calls refused"
        spent = self.store.paid_spend(since=utc_day_start(self.clock()))
        est = client.estimate_cost(prompt, max_tokens) if hasattr(client, "estimate_cost") else 0.0
        if spent + est > limit:
            return False, f"paid daily cap reached: spent {spent:.4f} + est {est:.4f} > {limit:.2f} USD"
        return True, ""

    def free_budget_left(self) -> bool:
        cap = int(self.config.get("free_calls_per_day", 150) or 0)
        return self.store.free_calls_since(utc_day_start(self.clock())) < cap

    def complete(self, system: str, prompt: str, *, task_id: int | None = None, role: str | None = None,
                 exclude: set[str] | None = None) -> ModelReply:
        max_tokens = int(self.config.get("max_output_tokens", 1800))
        errors: list[str] = []
        refused_paid = False
        for client in self.clients:
            label = f"{client.provider}:{client.model}"
            if exclude and label in exclude:
                continue
            try:
                if not client.available():
                    continue
            except Exception:  # noqa: BLE001 - availability probes never break the run
                continue
            label = f"{client.provider}:{client.model}"
            if exclude and label in exclude:
                continue
            if client.paid:
                ok, why = self.paid_allowed(client, prompt, max_tokens)
                if not ok:
                    refused_paid = True
                    errors.append(why)
                    self.store.log("paid_refused", {"provider": client.provider, "why": why}, now=self.clock())
                    continue
            elif client.provider not in ("ollama", "fake") and not self.free_budget_left():
                errors.append(f"{client.provider}: free daily call budget used")
                continue
            try:
                reply = client.complete(system, prompt, max_tokens)
            except ModelError as exc:
                errors.append(str(exc))
                self.store.log_spend(provider=client.provider, model=client.model, tokens_in=0, tokens_out=0,
                                     cost_usd=0.0, paid=client.paid, ok=False, task_id=task_id, role=role,
                                     note=str(exc), now=self.clock())
                self.store.log_error("model", str(exc), task_id=task_id, now=self.clock())
                continue
            self.store.log_spend(provider=reply.provider, model=reply.model, tokens_in=reply.tokens_in,
                                 tokens_out=reply.tokens_out, cost_usd=reply.cost_usd, paid=reply.paid, ok=True,
                                 task_id=task_id, role=role, now=self.clock())
            return reply
        if refused_paid:
            raise PaidSpendRefused("; ".join(errors))
        raise NoModelAvailable("; ".join(errors) or "no model client available")


def build_default_clients(config: Config, keys: dict[str, str] | None = None) -> list:
    keys = read_keys() if keys is None else keys
    clients: list = [OllamaClient(config["ollama_url"], config.get("ollama_model", ""),
                                  bool(config.get("ollama_only_loaded", True)))]
    if keys.get("OPENROUTER_API_KEY"):
        clients.append(OpenAICompatClient("openrouter", "https://openrouter.ai/api/v1", config["openrouter_model"],
                                          keys["OPENROUTER_API_KEY"]))
    if keys.get("GEMINI_API_KEY"):
        clients.append(OpenAICompatClient("gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
                                          config["gemini_model"], keys["GEMINI_API_KEY"]))
    paid = config.get("paid_provider") or {}
    if config.paid_daily_limit > 0 and keys.get(paid.get("key_env", "")):
        clients.append(OpenAICompatClient(paid.get("name", "paid"), paid["base_url"], paid["model"],
                                          keys[paid["key_env"]], paid=True,
                                          usd_per_mtok_in=float(paid.get("usd_per_mtok_in", 0.028)),
                                          usd_per_mtok_out=float(paid.get("usd_per_mtok_out", 0.028))))
    return clients
