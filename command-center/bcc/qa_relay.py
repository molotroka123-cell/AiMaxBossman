"""B2 — a cloud QA agent must be able to exercise the local system safely.

Cloud QA could not test the local UI at all. `127.0.0.1` and `bossman.local`
are refused by the browser tool's SSRF policy, and rightly so: an agent that
can fetch loopback URLs on the owner's machine can read every local service.
Tasks 29 and 30 honestly reported "a public URL is needed" and stopped.

Making the SSRF policy softer would trade a real defence for a test
convenience. This module takes the other route: the cloud side never reaches
in. It asks for a NAMED, allowlisted QA capability; the LOCAL side decides
whether to run it, runs it itself, and returns sanitized structured evidence.

    cloud QA agent -> signed job -> local bridge -> allowlisted action
                                                 -> sanitized evidence -> cloud

Properties, each enforced here and covered by a negative control:

  * Capabilities are a closed registry of named handlers. There is no
    "run this command", no path parameter, and no way to name a handler that
    is not registered. The cloud side chooses FROM a menu; it never writes one.
  * Every job is HMAC-signed with a secret held in the owner's vault. An
    unsigned, wrongly-signed or tampered job is refused before anything runs.
  * Every job carries a single-use nonce and an expiry. A replayed job is
    refused even with a perfect signature, and a job may not outlive
    `MAX_TTL_SECONDS`.
  * Evidence is a structured dict, size-capped, and passes through redaction
    plus home/user path scrubbing before it leaves the machine. Free-form text
    and file bodies are never returned.
  * A rate limit bounds how much work the cloud side can ask for per window.
  * The owner can disable the bridge instantly; while it is off, a perfectly
    valid signed job is still refused.
  * Every accepted and every rejected job is recorded with its outcome.

Transport is deliberately NOT decided here. The contract is a pure function of
(secret, job) plus a local execution step, so it works identically over an
owner-initiated outbound poll, a manual paste, or a queue file. Only the
question of WHERE the queue lives is external to this repository.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

#: A job may not authorize work further into the future than this, whatever it
#: claims. A long-lived signed job is a credential.
MAX_TTL_SECONDS = 600
#: Evidence larger than this is truncated with a marker rather than sent. The
#: cloud side asked for a verdict, not for a copy of the machine.
MAX_EVIDENCE_BYTES = 64 * 1024
#: Rate limit: jobs accepted per window.
RATE_LIMIT_JOBS = 30
RATE_LIMIT_WINDOW_SECONDS = 60
#: One QA action must not be able to hold the bridge open.
MAX_ACTION_SECONDS = 30
#: Consumed nonces kept in memory; older ones cannot be replayed anyway because
#: their jobs have expired.
NONCE_RETENTION_SECONDS = 2 * MAX_TTL_SECONDS

SECRET_SETTING_KEY = "qa_relay.secret"
ENABLED_SETTING_KEY = "qa_relay.enabled"

OK = "ok"
REJECTED_DISABLED = "rejected_disabled"
REJECTED_SIGNATURE = "rejected_signature"
REJECTED_REPLAY = "rejected_replay"
REJECTED_EXPIRED = "rejected_expired"
REJECTED_CAPABILITY = "rejected_capability"
REJECTED_PARAMS = "rejected_params"
REJECTED_RATE = "rejected_rate_limited"
FAILED = "failed"
TIMED_OUT = "timed_out"


def _now() -> float:
    return time.time()


def canonical(job: dict[str, Any]) -> bytes:
    """Bytes that get signed. Sorted keys and no whitespace so the cloud side
    and the local side cannot disagree about the same job."""
    body = {k: v for k, v in job.items() if k != "sig"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str).encode("utf-8")


def sign(secret: str, job: dict[str, Any]) -> str:
    return hmac.new(secret.encode("utf-8"), canonical(job), hashlib.sha256).hexdigest()


def verify(secret: str, job: dict[str, Any]) -> bool:
    provided = str(job.get("sig") or "")
    if not provided or not secret:
        return False
    return hmac.compare_digest(provided, sign(secret, job))


def new_secret() -> str:
    """A fresh bridge secret. Never logged, never returned in evidence — the
    only place it is shown is the owner's own response to creating it."""
    import secrets as _secrets
    return _secrets.token_urlsafe(32)


def build_job(secret: str, capability: str, params: dict[str, Any] | None = None,
              *, ttl_seconds: int = 120, nonce: str | None = None,
              issued_at: float | None = None) -> dict[str, Any]:
    """Construct a signed job. Used by the cloud side and by the tests; the
    local side only ever verifies."""
    import secrets as _secrets
    at = _now() if issued_at is None else issued_at
    ttl = max(1, min(int(ttl_seconds), MAX_TTL_SECONDS))
    job = {
        "job_id": _secrets.token_hex(8),
        "nonce": nonce or _secrets.token_urlsafe(16),
        "capability": capability,
        "params": dict(params or {}),
        "issued_at": round(at, 3),
        "expires_at": round(at + ttl, 3),
    }
    job["sig"] = sign(secret, job)
    return job


# ------------------------------------------------------------- sanitization

#: Absolute paths leak the owner's username and layout. Evidence is a verdict,
#: not a filesystem map.
_HOME_RE = re.compile(r"(?:/home/|/Users/|[A-Za-z]:[\\/]Users[\\/])[^/\\\s\"']+",
                      re.IGNORECASE)
_WINPATH_RE = re.compile(r"[A-Za-z]:\\[^\s\"']*")
_UNIXPATH_RE = re.compile(r"(?<![\w.])/(?:home|Users|root|etc|var|opt|srv)(?:/[^\s\"']*)?")


def scrub_text(value: str) -> str:
    out = _HOME_RE.sub("<HOME>", value)
    out = _WINPATH_RE.sub("<PATH>", out)
    out = _UNIXPATH_RE.sub("<PATH>", out)
    return out


def sanitize(value: Any, *, depth: int = 0) -> Any:
    """Redact secrets by key name and value, then scrub private paths.

    Reuses `plugin_security.redact` rather than re-deriving what a secret looks
    like: a second, more permissive opinion is how a token escapes."""
    from .plugin_security import redact
    if depth == 0:
        value = redact(value)
    if isinstance(value, dict):
        return {str(k): sanitize(v, depth=depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(v, depth=depth + 1) for v in value]
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return scrub_text(str(value))


def cap_size(evidence: Any) -> tuple[Any, bool]:
    """Bound the payload. Returns (evidence, truncated)."""
    blob = json.dumps(evidence, ensure_ascii=False, default=str)
    if len(blob.encode("utf-8")) <= MAX_EVIDENCE_BYTES:
        return evidence, False
    return {"truncated": True, "reason": "evidence exceeded the relay size limit",
            "limit_bytes": MAX_EVIDENCE_BYTES,
            "preview": blob[:2000]}, True


# -------------------------------------------------------------- capabilities

Handler = Callable[[Any, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class Capability:
    """One thing the cloud side may ask for, and nothing more.

    `params` is a closed schema: a key the capability did not declare is a
    rejected job, not an ignored field. That is what keeps a path, a command or
    an id from being smuggled through an unvalidated extra."""
    name: str
    description: str
    handler: Handler
    params: dict[str, type] = field(default_factory=dict)
    required: frozenset[str] = frozenset()

    def validate(self, given: dict[str, Any]) -> str:
        for key in given:
            if key not in self.params:
                return f"параметр {key!r} не объявлен возможностью {self.name}"
        for key in self.required:
            if key not in given:
                return f"обязательный параметр {key!r} отсутствует"
        for key, value in given.items():
            expected = self.params[key]
            if not isinstance(value, expected):
                return (f"параметр {key!r} должен быть {expected.__name__}, "
                        f"а получен {type(value).__name__}")
        return ""


class Registry:
    """The menu. A capability that is not registered cannot be named."""

    def __init__(self) -> None:
        self._caps: dict[str, Capability] = {}

    def register(self, cap: Capability) -> Capability:
        self._caps[cap.name] = cap
        return cap

    def get(self, name: str) -> Capability | None:
        return self._caps.get(name)

    def names(self) -> list[str]:
        return sorted(self._caps)

    def catalog(self) -> list[dict[str, Any]]:
        return [{"name": c.name, "description": c.description,
                 "params": {k: v.__name__ for k, v in c.params.items()},
                 "required": sorted(c.required)}
                for c in (self._caps[n] for n in self.names())]


REGISTRY = Registry()


# ------------------------------------------------------------------- bridge

@dataclass
class Outcome:
    status: str
    job_id: str = ""
    capability: str = ""
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    truncated: bool = False
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status == OK

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "job_id": self.job_id,
                "capability": self.capability, "detail": self.detail,
                "evidence": self.evidence, "truncated": self.truncated,
                "duration_ms": self.duration_ms}


class Bridge:
    """The local half. Holds the replay guard, the rate limit and the audit."""

    def __init__(self, registry: Registry | None = None) -> None:
        self.registry = registry or REGISTRY
        self._seen: dict[str, float] = {}
        self._accepted: list[float] = []
        self.audit: list[dict[str, Any]] = []

    # -- replay / rate ------------------------------------------------------

    def _prune(self, now: float) -> None:
        cutoff = now - NONCE_RETENTION_SECONDS
        self._seen = {k: v for k, v in self._seen.items() if v > cutoff}
        window = now - RATE_LIMIT_WINDOW_SECONDS
        self._accepted = [t for t in self._accepted if t > window]

    def _replayed(self, job: dict[str, Any], now: float) -> bool:
        key = f"{job.get('job_id')}:{job.get('nonce')}"
        if key in self._seen:
            return True
        self._seen[key] = now
        return False

    # -- audit --------------------------------------------------------------

    def _record(self, outcome: Outcome, job: dict[str, Any]) -> Outcome:
        self.audit.append({
            "at": datetime.now(timezone.utc).isoformat(),
            "job_id": str(job.get("job_id") or "")[:32],
            "capability": str(job.get("capability") or "")[:64],
            "status": outcome.status,
            "detail": outcome.detail[:300],
            "duration_ms": outcome.duration_ms,
        })
        del self.audit[:-500]
        return outcome

    # -- execution ----------------------------------------------------------

    async def handle(self, svc, job: Any, *, secret: str, enabled: bool,
                     now: float | None = None) -> Outcome:
        """Verify and, only then, run. The order matters: nothing about the job
        is trusted until the signature holds, and nothing runs at all while the
        owner has the bridge switched off."""
        at = _now() if now is None else now
        self._prune(at)
        if not isinstance(job, dict):
            return self._record(Outcome(REJECTED_SIGNATURE,
                                        detail="задание должно быть объектом"), {})
        job_id = str(job.get("job_id") or "")[:32]
        capability = str(job.get("capability") or "")[:64]
        if not enabled:
            # Checked before the signature on purpose: while the bridge is off,
            # a perfectly valid job is still refused, and the owner's switch is
            # not a race against a correctly signed request.
            return self._record(Outcome(REJECTED_DISABLED, job_id, capability,
                                        "мост QA выключен владельцем"), job)
        if not secret or not verify(secret, job):
            return self._record(Outcome(REJECTED_SIGNATURE, job_id, capability,
                                        "подпись задания не совпала"), job)
        expires = job.get("expires_at")
        issued = job.get("issued_at")
        if not isinstance(expires, (int, float)) or not isinstance(issued, (int, float)):
            return self._record(Outcome(REJECTED_EXPIRED, job_id, capability,
                                        "задание без корректного срока действия"), job)
        if expires <= at:
            return self._record(Outcome(REJECTED_EXPIRED, job_id, capability,
                                        "срок действия задания истёк"), job)
        if expires - issued > MAX_TTL_SECONDS:
            # A signed job that lives for a day is a credential, not a request.
            return self._record(Outcome(REJECTED_EXPIRED, job_id, capability,
                                        f"срок жизни задания больше {MAX_TTL_SECONDS} с"), job)
        if self._replayed(job, at):
            return self._record(Outcome(REJECTED_REPLAY, job_id, capability,
                                        "задание с таким nonce уже исполнялось"), job)
        cap = self.registry.get(capability)
        if cap is None:
            return self._record(Outcome(REJECTED_CAPABILITY, job_id, capability,
                                        f"возможность {capability!r} не разрешена"), job)
        params = job.get("params")
        if not isinstance(params, dict):
            return self._record(Outcome(REJECTED_PARAMS, job_id, capability,
                                        "params должен быть объектом"), job)
        problem = cap.validate(params)
        if problem:
            return self._record(Outcome(REJECTED_PARAMS, job_id, capability, problem), job)
        if len(self._accepted) >= RATE_LIMIT_JOBS:
            return self._record(Outcome(REJECTED_RATE, job_id, capability,
                                        f"больше {RATE_LIMIT_JOBS} заданий за "
                                        f"{RATE_LIMIT_WINDOW_SECONDS} с"), job)
        self._accepted.append(at)
        return self._record(await self._run(svc, cap, params, job_id), job)

    async def _run(self, svc, cap: Capability, params: dict[str, Any],
                   job_id: str) -> Outcome:
        import asyncio
        started = time.perf_counter()
        try:
            raw = await asyncio.wait_for(cap.handler(svc, params),
                                         timeout=MAX_ACTION_SECONDS)
        except asyncio.TimeoutError:
            return Outcome(TIMED_OUT, job_id, cap.name,
                           f"действие не уложилось в {MAX_ACTION_SECONDS} с",
                           duration_ms=int((time.perf_counter() - started) * 1000))
        except Exception as exc:  # noqa: BLE001 — тип ошибки наружу, детали в логах
            return Outcome(FAILED, job_id, cap.name,
                           f"{type(exc).__name__}",
                           duration_ms=int((time.perf_counter() - started) * 1000))
        duration = int((time.perf_counter() - started) * 1000)
        if not isinstance(raw, dict):
            return Outcome(FAILED, job_id, cap.name,
                           "обработчик вернул не структурированную улику",
                           duration_ms=duration)
        evidence, truncated = cap_size(sanitize(raw))
        return Outcome(OK, job_id, cap.name, "", evidence, truncated, duration)


# ------------------------------------------------- the first QA capabilities
# Read-only smoke checks for the three systems cloud QA could not reach. Each
# returns counts and states, never file bodies, never owner content.

async def _video_studio_smoke(svc, params: dict[str, Any]) -> dict[str, Any]:
    from .video_studio.media import capabilities as vs_capabilities
    from .features.video_studio import service as vs_service
    caps = vs_capabilities()
    projects = await vs_service(_Req(svc)).store.list(archived=False)
    return {"system": "video_studio",
            "capabilities": {k: v for k, v in caps.items() if isinstance(v, (bool, str, int))},
            "project_count": len(projects),
            "project_names": [str(p.get("name") or "")[:60] for p in projects[:10]]}


async def _web_designer_smoke(svc, params: dict[str, Any]) -> dict[str, Any]:
    from .features import web_designer as wd
    catalog = wd.gen.templates_catalog()
    projects = await wd.list_projects(_Req(svc))
    items = projects.get("items") if isinstance(projects, dict) else projects
    return {"system": "web_designer",
            "template_count": len(catalog),
            "templates": [str(t.get("id") or t.get("name") or "")[:40] for t in catalog[:20]],
            "project_count": len(items or [])}


async def _apps_smoke(svc, params: dict[str, Any]) -> dict[str, Any]:
    from .features.apps import collect
    from .features import apps_control as ctl
    apps = await collect()
    policy = await ctl.policy(svc)
    return {"system": "apps",
            "app_count": len(apps),
            "app_ids": [str(a.get("id") or "")[:40] for a in apps],
            "live": sum(1 for a in apps if a.get("status") == "LIVE"),
            "control_enabled": bool(policy.get("enabled")),
            "control_source": str(policy.get("source") or "")}


async def _health_smoke(svc, params: dict[str, Any]) -> dict[str, Any]:
    """Deliberately coarse: component up/down, no versions, no paths, no
    configuration. QA needs to know the system is answering, not how it is
    built."""
    from .review_escalation import audit as deadlock_audit
    deadlocks = await deadlock_audit(svc)
    return {"system": "health",
            "waiting_approval": deadlocks["waiting_approval"],
            "deadlocked": deadlocks["deadlocked"],
            "database": bool(await svc.db.ping())}


class _Req:
    """Minimal stand-in for the FastAPI request the feature handlers expect.

    The relay calls the SAME functions the HTTP endpoints call, rather than
    re-implementing them: a second implementation of "list the projects" would
    drift from the real one and QA would be testing the copy."""

    def __init__(self, svc) -> None:
        self.app = type("_App", (), {"state": type("_S", (), {"svc": svc})()})()


REGISTRY.register(Capability(
    "video_studio.smoke", "Video Studio: возможности и число проектов",
    _video_studio_smoke))
REGISTRY.register(Capability(
    "web_designer.smoke", "Web Designer: каталог шаблонов и число проектов",
    _web_designer_smoke))
REGISTRY.register(Capability(
    "apps.smoke", "Apps: список приложений, живые процессы, политика управления",
    _apps_smoke))
REGISTRY.register(Capability(
    "health.smoke", "Состояние ядра: БД и очередь решений", _health_smoke))
