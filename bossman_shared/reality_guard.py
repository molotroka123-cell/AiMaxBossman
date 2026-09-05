"""Always-on participation latch; independent of the optional Reality package.

Host bootstrap is the only caller of install/enroll. This is not a model tool.
The fixed state directory must be outside agent mounts and protected by host ACLs.
Disabling admission never disables checks on existing participants.
"""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path

STATE_ROOT = Path.home() / ".bossman" / "reality"
_hosts = {}
_fleet_fence = contextvars.ContextVar("reality_fleet_fence", default=None)


class RealityBlocked(RuntimeError):
    """Requires host reconciliation, never an ordinary automatic retry."""


def _key(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _marker(scope, task_id):
    return STATE_ROOT / "participants" / (_key([scope, str(task_id)]) + ".json")


def install(profile, host):
    """Trusted bootstrap must reinstall the SAME policy/observers after restart."""
    _hosts[profile] = host


def enroll(scope, task_id, run_id, proposal, *, trusted_ir, profile, plan=None):
    """Accept only a full host-approved IR, never planner risk/privacy assertions.

    IDs include the application's database/tenant namespace in production hosts.
    No new run identity can replace an already participating task.
    """
    if os.environ.get("BOSSMAN_REALITY_ENABLED", "0") != "1":
        raise RealityBlocked("Reality admission is disabled")
    from .reality.contracts import RealityCompiler, digest
    mission = RealityCompiler().compile(proposal)
    approved = RealityCompiler().compile(trusted_ir)
    if mission != approved or mission.run_id != str(run_id):
        raise RealityBlocked("host contract or run binding mismatch")
    host = _hosts.get(profile)
    if host is None:
        raise RealityBlocked("host profile unavailable")
    host.validate(mission)
    body = {"scope": scope, "task": str(task_id), "run": str(run_id),
            "mission": mission.id, "fingerprint": mission.fingerprint,
            "profile": profile, "plan": digest(plan) if plan is not None else None}
    path = _marker(scope, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # O_EXCL: a competing admission cannot overwrite the persistent latch.
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if json.loads(path.read_text(encoding="utf-8")) != body:
            raise RealityBlocked("participation identity is immutable") from None
    else:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(body, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
    # A crash after the latch and before IR persistence fails closed on resume.
    host.register(mission)
    return mission


def lookup(scope, task_id, run_id, *, actor=None, plan=None):
    path = _marker(scope, task_id)
    if not path.exists():
        return None
    try:
        from .reality.contracts import digest
        body = json.loads(path.read_text(encoding="utf-8"))
        if (body["scope"], body["task"], body["run"]) != (scope, str(task_id), str(run_id)):
            raise RealityBlocked("participating task cannot change run identity")
        host = _hosts[body["profile"]]
        mission = host.load(body["mission"])
        if mission.fingerprint != body["fingerprint"] or mission.run_id != str(run_id):
            raise RealityBlocked("persisted IR mismatch")
        if actor is not None and mission.executor != str(actor):
            raise RealityBlocked("executor principal changed")
        if plan is not None and body["plan"] != digest(plan):
            raise RealityBlocked("compound plan changed")
        host.validate(mission)
        return Session(host, mission)
    except Exception as exc:
        raise RealityBlocked("Reality participation requires intact IR and host profile") from exc


class Session:
    def __init__(self, host, mission):
        self.host, self.mission = host, mission

    def claim(self, action, args):
        from .reality.contracts import digest
        # All dispatches must match one declared effect. Ambiguity is rejected.
        matches = [e for e in self.mission.effects
                   if e.action == action and e.args_digest == digest(args)]
        if len(matches) != 1:
            raise RealityBlocked("undeclared or ambiguous dispatch")
        effect = matches[0]
        self.host.validate(self.mission)
        self.host.route_allowed(effect.action)
        fence = self.host.call(lambda rt: rt.store.claim(self.mission, effect.id, self.mission.executor))
        self.check_fence(effect, fence)
        return effect, fence

    def check_fence(self, effect, fence):
        external = _fleet_fence.get()
        if external is not None and external() is not True:
            raise RealityBlocked("Fleet lease lost; escrow retained")
        if self.host.fence_check(self.mission, effect, self.mission.executor, fence) is not True:
            raise RealityBlocked("host fence lost; escrow retained")

    def confirm(self, effect, fence):
        from .reality.contracts import digest
        self.check_fence(effect, fence)
        self.host.validate(self.mission)
        def confirm(rt):
            obligation = self.mission.obligation(effect.obligation_id)
            def observe(target):
                return self.host.observed(obligation, rt.observers[obligation.verifier](target))
            receipt = rt.authority.observe(self.mission, obligation.id, observe,
                dispatch_binding=digest([self.mission.fingerprint, effect.id, fence]))
            self.check_fence(effect, fence)
            rt.store.confirm(self.mission, effect.id, self.mission.executor, fence, receipt, rt.authority)
        self.host.call(confirm)

    def complete(self):
        external = _fleet_fence.get()
        if external is not None and external() is not True:
            raise RealityBlocked("Fleet lease lost before completion")
        return self.host.call(lambda rt: rt.complete(self.mission))


async def dispatch(scope, task_id, run_id, actor, action, args, invoke, *, fence_check=None):
    session = await asyncio.to_thread(lookup, scope, task_id, run_id, actor=actor)
    if session is None:
        return await invoke()
    try:
        effect, fence = await asyncio.to_thread(session.claim, action, args)
        if fence_check is not None:
            await fence_check()
        # Cancellation / adapter error leaves escrow intact, with no refund/retry.
        result = await invoke()
        if getattr(result, "error", False):
            raise RealityBlocked("tool returned an error; escrow requires reconciliation")
        if fence_check is not None:
            await fence_check()
        await asyncio.to_thread(session.confirm, effect, fence)
        return result
    except Exception as exc:
        raise RealityBlocked("Reality dispatch requires host reconciliation") from exc


def dispatch_sync(session, action, args, invoke):
    if session is None:
        return invoke()
    effect, fence = session.claim(action, args)
    result = invoke()
    if hasattr(result, "verification") and not result.verification.passed:
        raise RealityBlocked("existing verifier rejected effect; escrow retained")
    session.confirm(effect, fence)
    return result


def require_complete(scope, task_id, run_id, *, actor=None):
    session = lookup(scope, task_id, run_id, actor=actor)
    if session is not None:
        session.complete()


async def completion_hook(task, run_id, answer):
    def check():
        try:
            session = lookup("bcc", task["id"], run_id, actor=task.get("agent_id"))
            if session is None:
                return {"verdict": "NOT_APPLICABLE"}
            from .reality.runtime import make_completion_hook
            return session.host.call(lambda rt: asyncio.run(
                make_completion_hook(rt, lambda task, run: session.mission)(task, run_id, answer)))
        except Exception:
            return {"verdict": "FAIL", "requeue": False, "reasons": "reality_proof_incomplete"}
    return await asyncio.to_thread(check)


@contextmanager
def fleet_fence(check):
    token = _fleet_fence.set(check)
    try:
        yield
    finally:
        _fleet_fence.reset(token)


def block_unmetered_model(scope, task_id, run_id):
    """Initial rollout is tool-only. No paid/provider egress without integration.

    Existing global ledgers remain untouched. Blocking at the common model entry
    also blocks fallback, compaction and PUBLIC facts with private dependencies.
    """
    if lookup(scope, task_id, run_id) is not None:
        raise RealityBlocked("Reality provider routing is not admitted in local tool-only mode")
