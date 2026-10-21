"""Free-cloud fallback for «строго по методичке» (owner request 21.10: «Если локальная модель в Джефф недоступна, пусть
отвечает облачная, но тоже по методичке»).

Order: Jeff's LOCAL model answers first. Only when it is UNAVAILABLE — a timeout, no connection, HTTP 5xx/503, Ollama
"model not found / failed to load", or no local route at all (not loaded, GPU busy) — and the owner's switch
``cloud_fallback`` (jeff-kb state, default ON) is on, the SAME strict request goes to a FREE cloud model of Bossman's
provider registry. A normal local answer — «В методичке этого нет.» included — never goes to the cloud.

What the cloud gets: the strict rules, the RETRIEVED sections (never the whole manual) and the question — the messages
``jeff_kb.strict_messages`` builds; never a participant's memory, persona or history, never the owner's notes. The
reply passes the same identity guard, citation check and «В методичке этого нет.» rule as a local one (``jeff_kb``).

Which models (``eligible``): rows of Bossman's registry (bcc.db) that are PROVABLY free — price 0/0 known AND an
OpenRouter ``:free`` id on openrouter.ai or a free-tier preset model (``caps.free_tier``) on that preset's own host —
not banned (Liquid/LFM), not local, not an Ollama ``:cloud`` offload, a chat model, outside its health cooldown and not
measured broken (``model_health``, the same rule as ``select_fallback``). Every call goes through Bossman's governed
adapter (free-only gate, long_run caps + STOP for a priced family, Fable cap, egress / BOSSMAN_LOCAL_ONLY guard),
built exactly like ``Registry.adapter_for``. ``BOSSMAN_ALLOW_PAID_CLOUD`` does not widen this list.

Limits: an owner STOP (``<data>/autonomy/STOP`` or ``<data>/computer/STOP``) or BOSSMAN_LOCAL_ONLY -> no cloud; a daily
request cap (state ``cloud_daily_max``, default 100) saves the free tiers; a budget / policy / payment refusal of the
governed adapter ends the fallback for this answer; a reply that reports ``usage.cost > 0`` is discarded and closes the
fallback for the rest of the day. Nothing is retried past ``MAX_ATTEMPTS`` models.

Logs (``jeff_kb.audit``): counts, sizes, a sha256 prefix of what was sent, provider and model names, reason codes.
Never the manual text, a section title or the question. Provider error text is never stored (it may echo the request):
health records get the classification only.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol
from urllib.parse import urlsplit

from . import jeff_kb as kb

SCHEMA = "bossman.pit.jeff-kb-cloud/1"
USAGE_FILE = "cloud-usage.json"

DEFAULT_LOCAL_TIMEOUT_S = 60
MIN_LOCAL_TIMEOUT_S = 10
MAX_LOCAL_TIMEOUT_S = 300
DEFAULT_DAILY_MAX = 100
MAX_DAILY_MAX = 1000

MAX_ATTEMPTS = 3                  # free models tried for one answer, at most
ATTEMPT_TIMEOUT_S = 60.0
CHECK_BUDGET_S = 120.0            # owner «Проверка»: 60 s local + <= 120 s cloud stays under the phone's 240 s
CHAT_BUDGET_S = 75.0              # a Jeff chat reply
MAX_TOKENS = 900

NO_LINK_RU = ("Нет связи с моделями: локальная модель Jeff сейчас недоступна, а облачный запасной вариант не "
              "ответил. Ответа по методичке сейчас нет — попробуйте чуть позже.")

LOCAL_REASONS_RU = {
    "timeout": "не ответила вовремя",
    "connection": "нет соединения с локальной моделью",
    "http_5xx": "сервер локальной модели вернул ошибку",
    "model_missing": "модель не загружена",
    "no_local_route": "локальная модель недоступна (занята или не загружена)",
}
CLOUD_REASONS_RU = {
    "off": "облачный запасной вариант выключен",
    "stop": "действует СТОП владельца",
    "local_only": "включён режим «только локально»",
    "daily_cap": "дневной лимит облачных ответов исчерпан",
    "cost_violation": "облачный ответ оказался платным — запасной вариант закрыт до завтра",
    "no_consent": "участник не разрешал обработку в облаке",
    "no_candidates": "в реестре Bossman нет доступной бесплатной облачной модели",
    "refused": "облачный маршрут отклонён правилами бюджета Bossman",
    "failed": "бесплатные облачные модели не ответили",
}


class ModelsUnavailable(Exception):
    """Neither the local model nor a free cloud model answered (the owner sees «Нет связи с моделями»)."""

    def __init__(self, local_reason: str, cloud_reason: str):
        self.local_reason, self.cloud_reason = local_reason, cloud_reason
        super().__init__(message(local_reason, cloud_reason))


def message(local_reason: str, cloud_reason: str) -> str:
    local = LOCAL_REASONS_RU.get(local_reason, "локальная модель недоступна")
    cloud = CLOUD_REASONS_RU.get(cloud_reason, CLOUD_REASONS_RU["failed"])
    return f"Нет связи с моделями: локальная — {local}; облако — {cloud}. Ответа по методичке нет."


# ---------------------------------------------------------------------------------------------- local availability
_MODEL_MISSING = re.compile(
    r"failed to load|model\s+['\"]?[^\s'\"]{0,120}['\"]?\s+not found|not found, try pulling|model is not loaded|"
    r"model not loaded|unable to load model|llama runner process has terminated|no such model", re.I)


def availability_reason(exc: BaseException) -> str:
    """Why the local model is UNAVAILABLE ('' = it is not an availability failure and the cloud is NOT asked).

    timeout | connection | http_5xx | model_missing. An empty answer, a protocol error, a 4xx refusal or an answer that
    simply says «нет в методичке» are not availability failures."""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    try:
        import httpx
        if isinstance(exc, httpx.TimeoutException):
            return "timeout"
        if isinstance(exc, httpx.TransportError):
            return "connection"
    except ImportError:  # pragma: no cover - httpx is a dependency
        pass
    if getattr(exc, "timed_out", False) is True:
        return "timeout"
    code = getattr(exc, "status_code", None)
    text = str(exc)
    if _MODEL_MISSING.search(text) or (code == 404 and "model" in text.lower()):
        return "model_missing"
    if isinstance(code, int) and not isinstance(code, bool) and 500 <= code <= 599:
        return "http_5xx"
    if getattr(exc, "kind", None) == "network":
        return "connection"
    if isinstance(exc, ConnectionError):
        return "connection"
    return ""


# ---------------------------------------------------------------------------------------------- eligibility
@dataclass(frozen=True)
class Candidate:
    id: int
    name: str
    provider: str
    host: str
    provider_row: dict = field(compare=False, repr=False, default_factory=dict)
    model_row: dict = field(compare=False, repr=False, default_factory=dict)


def _zero(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0


def _json_field(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes)) and value:
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def free_refusal(provider: dict, model: dict) -> str:
    """'' when this registry row is a PROVABLY free cloud chat model for the manual fallback, else a reason code."""
    from bcc.provider_governance import (_free_preset_hosts, banned_model_refusal, free_only_refusal,
                                         is_cloud_offload)
    from bcc.providers import is_local_url
    from bcc.features.free_providers import is_chat_model
    name = str(model.get("name") or "")
    base = str(provider.get("base_url") or "")
    caps = _json_field(model.get("caps"))
    model = {**model, "caps": caps}
    if not name or banned_model_refusal(model):
        return "banned"
    parts = urlsplit(base)
    host = (parts.hostname or "").lower()
    if not host or is_local_url(base) or is_cloud_offload(provider, model):
        return "local"
    if parts.scheme != "https":
        return "not_https"
    if not (bool(model.get("pricing_known")) and _zero(model.get("price_in")) and _zero(model.get("price_out"))):
        return "not_free"
    if caps.get("paid"):
        return "not_free"
    proven = ((host == "openrouter.ai" or host.endswith(".openrouter.ai")) and name.endswith(":free")) or (
        bool(caps.get("free_tier")) and host in _free_preset_hosts())
    if not proven:
        return "not_proven_free"
    if free_only_refusal(provider, model):
        return "not_free"
    if not is_chat_model(name):
        return "not_chat"
    return ""


def eligible(models: Iterable[dict], providers: dict[int, dict], *, now: datetime | None = None,
             skip_ids: Iterable[int] = ()) -> list[Candidate]:
    """Provably free cloud chat models, best measured health first (healthy, then unmeasured); in-cooldown and measured
    broken models are left out like ``model_health.select_fallback`` does."""
    from bcc import model_health as mh
    now = now or datetime.now(timezone.utc)
    skip = set(skip_ids)
    ranked: list[tuple[tuple, Candidate]] = []
    for row in models:
        try:
            mid = int(row["id"])
        except (KeyError, TypeError, ValueError):
            continue
        provider = providers.get(row.get("provider_id")) or {}
        if mid in skip or not provider or str(row.get("status") or "") == "offline":
            continue
        if free_refusal(provider, row):
            continue
        record = mh.HealthRecord.from_dict(_json_field(row.get("health")) or None)
        tier = record.rank_key(now)
        if tier[0] > 1:
            continue
        host = (urlsplit(str(provider.get("base_url") or "")).hostname or "").lower()
        cand = Candidate(id=mid, name=str(row["name"]), provider=str(provider.get("name") or host)[:80], host=host,
                         provider_row=dict(provider), model_row={**row, "caps": _json_field(row.get("caps"))})
        ranked.append(((*tier, mid), cand))
    return [cand for _key, cand in sorted(ranked, key=lambda pair: pair[0])]


def governed_adapter(provider: dict, model: dict, inner: Any) -> Any:
    """The same wrapping as ``bcc.registry.Registry.adapter_for``: Fable cap, free-only refusal, GovernedAdapter."""
    from bcc.fable_cap import capped
    from bcc.provider_governance import FreeOnlyAdapter, GovernedAdapter, free_only_refusal
    inner = capped(inner, provider, model)
    refusal = free_only_refusal(provider, model)
    if refusal:
        inner = FreeOnlyAdapter(inner, refusal)
    return GovernedAdapter(inner, provider, model)


# ---------------------------------------------------------------------------------------------- routes
class CloudRoute(Protocol):
    async def candidates(self) -> list[Candidate]: ...

    async def chat(self, cand: Candidate, messages: list[dict], *, max_tokens: int, timeout: float) -> Any: ...

    async def note(self, cand: Candidate, exc: BaseException | None, latency_ms: int, text: str = "") -> None: ...


def _health_detail(exc: BaseException) -> str:
    code = getattr(exc, "status_code", None)
    return f"jeff-kb fallback: {type(exc).__name__}" + (f" HTTP {code}" if isinstance(code, int) else "")


class RegistryRoute:
    """Command Center process: Bossman's own ``Registry`` (svc.registry) — candidates, governed adapters, health."""

    def __init__(self, registry: Any):
        self.registry = registry

    async def candidates(self) -> list[Candidate]:
        models = await self.registry.list_models()
        providers = {p["id"]: p for p in await self.registry.list_providers()}
        return eligible(models, providers)

    async def chat(self, cand: Candidate, messages: list[dict], *, max_tokens: int, timeout: float) -> Any:
        adapter, row = await self.registry.adapter_for(cand.id)
        return await asyncio.wait_for(adapter.chat(row["name"], messages, max_tokens=max_tokens, temperature=0.1,
                                                   timeout=timeout), timeout=timeout + 5.0)

    async def note(self, cand: Candidate, exc: BaseException | None, latency_ms: int, text: str = "") -> None:
        from bcc import model_health as mh
        if exc is None:
            status, detail = mh.classify_answer(text)
            await self.registry.record_model_health(cand.id, status, detail, latency_ms=latency_ms)
            return
        status, _detail = mh.classify_exception(exc)
        await self.registry.record_model_health(cand.id, status, _health_detail(exc),
                                                retry_after_s=getattr(exc, "retry_after", None))


AdapterBuilder = Callable[[str, str, str], Any]


class BccDbRoute:
    """Jeff's own process: a READ-ONLY look at Bossman's registry (``<data>/bcc.db``), the same way
    ``config.resolve_bcc_free_route`` reads it for the generation cloud. The provider key is decrypted in memory with
    Bossman's vault for the one call and never logged or kept. Failures cool a model in memory (this process); the
    measured health in bcc.db is only read."""

    def __init__(self, data_dir: Path | str, *, adapter_builder: AdapterBuilder | None = None):
        self.data_dir = Path(data_dir)
        self.adapter_builder = adapter_builder
        self._cool: dict[int, float] = {}

    def _db(self) -> Path:
        return self.data_dir / "bcc.db"

    def _read(self) -> tuple[list[dict], dict[int, dict]]:
        db = self._db()
        if not db.is_file():
            return [], {}
        con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=5.0)
        con.row_factory = sqlite3.Row
        try:
            cols = {r[1] for r in con.execute("pragma table_info(models)")}
            want = [c for c in ("id", "provider_id", "name", "alias", "kind", "caps", "price_in", "price_out",
                                "pricing_known", "status", "health") if c in cols]
            models = [dict(r) for r in con.execute(f"select {', '.join(want)} from models")]
            providers = {int(r["id"]): dict(r) for r in con.execute("select id, name, kind, base_url from providers")}
        finally:
            con.close()
        return models, providers

    async def candidates(self) -> list[Candidate]:
        try:
            models, providers = await asyncio.to_thread(self._read)
        except (sqlite3.Error, OSError):
            return []
        now = time.monotonic()
        cooled = {mid for mid, until in self._cool.items() if until > now}
        return eligible(models, providers, skip_ids=cooled)

    def _key(self, provider_id: int) -> str:
        from bcc.secrets import Vault
        con = sqlite3.connect(self._db().as_uri() + "?mode=ro", uri=True, timeout=5.0)
        try:
            row = con.execute("select api_key_enc from providers where id=?", (provider_id,)).fetchone()
        finally:
            con.close()
        return (Vault(self.data_dir).decrypt(row[0]) or "") if row and row[0] else ""

    async def chat(self, cand: Candidate, messages: list[dict], *, max_tokens: int, timeout: float) -> Any:
        from bcc.providers import build_adapter
        provider = cand.provider_row
        key = await asyncio.to_thread(self._key, int(provider["id"]))
        build = self.adapter_builder or (lambda kind, base, api_key: build_adapter(kind, base, api_key or None))
        adapter = governed_adapter(provider, cand.model_row, build(str(provider.get("kind") or "openai_compat"),
                                                                    str(provider.get("base_url") or ""), key))
        try:
            return await asyncio.wait_for(adapter.chat(cand.name, messages, max_tokens=max_tokens, temperature=0.1,
                                                       timeout=timeout), timeout=timeout + 5.0)
        finally:
            close = getattr(adapter, "close", None)
            if callable(close):
                try:
                    await close()
                except Exception:  # noqa: BLE001 - closing a client never changes the answer
                    pass

    async def note(self, cand: Candidate, exc: BaseException | None, latency_ms: int, text: str = "") -> None:
        if exc is None:
            self._cool.pop(cand.id, None)
            return
        from bcc import model_health as mh
        status, _ = mh.classify_exception(exc)
        retry = getattr(exc, "retry_after", None)
        seconds = float(retry) if isinstance(retry, (int, float)) and retry > 0 else (
            mh.cooldown_for(status, 1).total_seconds() or float(mh.BASE_COOLDOWN_SECONDS))
        self._cool[cand.id] = time.monotonic() + min(seconds, float(mh.MAX_COOLDOWN_SECONDS))


# ---------------------------------------------------------------------------------------------- gate / daily cap
def local_timeout_s(state: dict) -> float:
    """The short local timeout that decides «local is unavailable» (owner-configurable, 10..300 s, default 60)."""
    value = state.get("local_timeout_s", DEFAULT_LOCAL_TIMEOUT_S)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float(DEFAULT_LOCAL_TIMEOUT_S)
    return float(min(max(value, MIN_LOCAL_TIMEOUT_S), MAX_LOCAL_TIMEOUT_S))


def stop_reason(data_dir: Path | str | None) -> str:
    """'' or why an owner STOP forbids cloud calls now: ``<data>/autonomy/STOP``, ``<data>/computer/STOP`` and the
    autonomy root named by BOSSMAN_AUTONOMY_ROOT (if any)."""
    from bcc.autonomy import stop as _stop
    if not data_dir:
        return ""
    data = Path(data_dir)
    why = _stop.stop_reason(data / "autonomy", data)
    if why:
        return why
    root = os.environ.get("BOSSMAN_AUTONOMY_ROOT", "").strip()
    return _stop.stop_reason(Path(root).expanduser()) if root else ""


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _usage_path(data_dir: Path | str) -> Path:
    return kb.kb_dir(data_dir) / USAGE_FILE


def usage(data_dir: Path | str) -> dict:
    """Today's fallback usage: {"day", "calls", "violation"} (a new day starts from zero)."""
    try:
        raw = json.loads(_usage_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict) or raw.get("day") != _today():
        return {"day": _today(), "calls": 0, "violation": False}
    calls = raw.get("calls")
    return {"day": _today(), "calls": calls if isinstance(calls, int) and not isinstance(calls, bool) else 0,
            "violation": raw.get("violation") is True}


def _write_usage(data_dir: Path | str, row: dict) -> None:
    from bcc.autonomy.journal import atomic_write_bytes
    atomic_write_bytes(_usage_path(data_dir), json.dumps({**row, "schema": SCHEMA}, sort_keys=True).encode("utf-8"))


def _locked(data_dir: Path | str):
    from bcc.autonomy.journal import file_lock
    return file_lock(kb.kb_dir(data_dir) / ".cloud-usage.lock", timeout=10.0)


def daily_max(state: dict) -> int:
    value = state.get("cloud_daily_max", DEFAULT_DAILY_MAX)
    if isinstance(value, bool) or not isinstance(value, int):
        return DEFAULT_DAILY_MAX
    return min(max(value, 0), MAX_DAILY_MAX)


def gate(data_dir: Path | str, state: dict, *, stop_dir: Path | str | None = None) -> str:
    """'' when a cloud fallback request may be sent now, else the reason code (nothing is counted here)."""
    from bossman_shared.privacy import local_only_switch
    if not state.get("cloud_fallback", True):
        return "off"
    if stop_reason(stop_dir or data_dir):
        return "stop"
    if local_only_switch():
        return "local_only"
    today = usage(data_dir)
    if today["violation"]:
        return "cost_violation"
    if today["calls"] >= daily_max(state):
        return "daily_cap"
    return ""


def reserve_call(data_dir: Path | str, state: dict) -> str:
    """Count ONE cloud request against today's cap atomically (cross-process lock); '' = it may be sent."""
    try:
        with _locked(data_dir):
            today = usage(data_dir)
            if today["violation"]:
                return "cost_violation"
            if today["calls"] >= daily_max(state):
                return "daily_cap"
            _write_usage(data_dir, {**today, "calls": today["calls"] + 1})
    except (OSError, TimeoutError):
        return "daily_cap"                      # cannot count = cannot prove the cap: fail closed
    return ""


def note_cost_violation(data_dir: Path | str) -> None:
    try:
        with _locked(data_dir):
            _write_usage(data_dir, {**usage(data_dir), "violation": True})
    except (OSError, TimeoutError):
        pass


# ---------------------------------------------------------------------------------------------- the fallback call
@dataclass
class CloudOutcome:
    ok: bool
    text: str = ""
    model: str = ""
    provider: str = ""
    reason: str = ""              # why there is no cloud answer (CLOUD_REASONS_RU key)
    attempts: int = 0
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0


def _refused(exc: BaseException) -> bool:
    """A refusal by Bossman's own rules (budget, policy, egress, payment): no other model is tried for this answer."""
    from .model_route import is_payment_required
    return (isinstance(exc, PermissionError) or getattr(exc, "kind", None) in ("budget", "policy")
            or is_payment_required(exc))


def _reported_cost(result: Any) -> float:
    meta = getattr(result, "provider_meta", None) or {}
    usage_row = meta.get("usage") if isinstance(meta, dict) else None
    cost = usage_row.get("cost") if isinstance(usage_row, dict) else None
    return float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else 0.0


def sent_digest(messages: list[dict]) -> tuple[int, str]:
    """(characters, sha256 prefix) of what a cloud request carries — for the audit log instead of the text."""
    blob = json.dumps(messages, ensure_ascii=False, sort_keys=True)
    return sum(len(str(m.get("content", ""))) for m in messages), hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


async def ask_cloud(route: CloudRoute, messages: list[dict], *, data_dir: Path | str, state: dict | None = None,
                    local_reason: str, budget_s: float, stop_dir: Path | str | None = None,
                    surface: str = "chat") -> CloudOutcome:
    """One strict answer from a free cloud model, or an honest ``CloudOutcome(ok=False, reason=...)``.

    ``messages`` must already be the minimal strict request (rules + retrieved sections + question)."""
    state = kb.read_state(data_dir) if state is None else state
    chars, digest = sent_digest(messages)
    base = {"surface": surface, "local_reason": local_reason, "sent_chars": chars, "sha256": digest}

    def audit(event: str, **extra: Any) -> None:
        kb.audit(data_dir, event, **base, **extra)

    why = gate(data_dir, state, stop_dir=stop_dir)
    if why:
        audit("cloud_skipped", reason=why)
        return CloudOutcome(False, reason=why)
    try:
        cands = await route.candidates()
    except Exception:  # noqa: BLE001 - an unreadable registry means "no cloud model", never an invented answer
        cands = []
    if not cands:
        audit("cloud_skipped", reason="no_candidates")
        return CloudOutcome(False, reason="no_candidates")
    deadline = time.monotonic() + max(1.0, float(budget_s))
    reason, attempts = "failed", 0
    for cand in cands[:MAX_ATTEMPTS]:
        remaining = deadline - time.monotonic()
        if remaining < 2.0:
            break
        why = reserve_call(data_dir, state)
        if why:
            reason = why
            break
        attempts += 1
        started = time.monotonic()
        try:
            result = await route.chat(cand, messages, max_tokens=MAX_TOKENS,
                                      timeout=min(ATTEMPT_TIMEOUT_S, remaining))
        except Exception as exc:  # noqa: BLE001 - the next free model, honestly counted; provider text is not kept
            ms = int((time.monotonic() - started) * 1000)
            try:
                await route.note(cand, exc, ms)
            except Exception:  # noqa: BLE001 - health bookkeeping never changes the answer
                pass
            audit("cloud_error", provider=cand.provider, model=cand.name, latency_ms=ms,
                  status=int(getattr(exc, "status_code", 0) or 0) if isinstance(
                      getattr(exc, "status_code", None), int) else 0)
            if _refused(exc):
                reason = "refused"
                break
            continue
        ms = int((time.monotonic() - started) * 1000)
        if _reported_cost(result) > 0:
            note_cost_violation(data_dir)
            audit("cloud_cost_violation", provider=cand.provider, model=cand.name)
            reason = "cost_violation"
            break
        text = str(getattr(result, "text", "") or "").strip()
        try:
            await route.note(cand, None, ms, text)
        except Exception:  # noqa: BLE001
            pass
        if not text:
            audit("cloud_error", provider=cand.provider, model=cand.name, latency_ms=ms, empty=True)
            continue
        audit("cloud_answer", provider=cand.provider, model=cand.name, latency_ms=ms, attempts=attempts,
              answer_chars=len(text))
        return CloudOutcome(True, text=text, model=cand.name, provider=cand.provider, attempts=attempts,
                            latency_ms=ms, tokens_in=int(getattr(result, "tokens_in", 0) or 0),
                            tokens_out=int(getattr(result, "tokens_out", 0) or 0))
    audit("cloud_failed", reason=reason, attempts=attempts)
    return CloudOutcome(False, reason=reason, attempts=attempts)


# ---------------------------------------------------------------------------------------------- provenance
_SAFE_LABEL = re.compile(r"[^A-Za-z0-9._:/\- ]")


def label(value: str, limit: int = 60) -> str:
    return _SAFE_LABEL.sub("", str(value or ""))[:limit].strip()


def owner_suffix(engine: dict) -> str:
    """The short provenance line under a strict CLOUD answer in the owner's own chat (never for a participant)."""
    model = label(engine.get("model") or "")
    return "☁️ облако · " + model if model else "☁️ облако"


def provenance(engine: str, *, provider: str = "", model: str = "", fallback_reason: str = "") -> dict:
    out = {"engine": engine, "provider": label(provider, 80) or None, "model": label(model, 120) or None}
    if fallback_reason:
        out["fallback_reason"] = fallback_reason
    return out
