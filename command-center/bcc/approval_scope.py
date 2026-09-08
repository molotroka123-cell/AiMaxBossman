"""§7 — one owner decision per authority scope, not one per keystroke.

The 2026-09-07 acceptance session recorded 161 owner confirmations, ~121 of
them while correcting a documentation file. The cause is structural, not a
tuning problem: `approval_digest` binds an approval to the exact normalized
arguments, which is precisely right for anti-replay, but it means every new
`ls`, `pwd`, `dir` or `cat` in the same authorized activity is a brand-new
question. The owner is answering the same question about the same authority
over and over, which is how real approval gates stop being read.

This module reduces the COUNT of questions without widening what any single
answer authorizes. Three mechanisms, in increasing order of explicitness:

1. `dedup` — the identical still-pending question is never asked twice.
   Same digest, same run, still pending: reuse the row. This grants nothing;
   it only stops the queue filling with duplicates of one unanswered ask.

2. `suppress` — an identical digest already REJECTED in this run is refused
   without re-asking. "No" is an answer; re-asking until the owner says yes is
   the amplification working in the unsafe direction.

3. `lease` — the owner explicitly grants a bounded authority SCOPE:
   (tool, effect class, mode, agent, task) with a mandatory expiry and use
   count, revocable at any moment. In-scope calls consume a use instead of
   asking again.

Non-negotiables, all enforced below and covered by negative controls in
tests/test_approval_scope.py:

  * A lease exists only because the owner asked for one. Deciding an approval
    without `lease` behaves exactly as before this module existed.
  * A lease can never cover a DENY. Policy denial is not approvable, so it is
    not leasable either.
  * A lease is bound to the effect class computed by the SAME classifier the
    executor uses at run time. A read lease never covers a write — including
    a command that only becomes a write through a redirect or a git
    subcommand.
  * Both bounds are mandatory and finite: `max_uses >= 1`, `ttl_seconds >= 1`,
    each capped. There is no unlimited or eternal lease.
  * Consumption is a compare-and-set on `used < max_uses` plus an unexpired,
    active row — two callers cannot spend the same use.
  * Every consumption is recorded on the tool call (`lease_id`) and emitted on
    the bus, so "fewer approvals" stays distinguishable from "stopped asking".
  * Revocation is immediate and one-way.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import sqlalchemy as sa

from .db import (approval_leases as leases_t, approvals as approvals_t,
                 tool_calls as tool_calls_t, utcnow)

#: Hard ceilings. A lease is a convenience, never an open door: an owner who
#: needs more than this is really granting the agent a permission, and should
#: do that explicitly through the permission model instead.
MAX_LEASE_USES = 200
MAX_LEASE_TTL_SECONDS = 4 * 3600

READ, WRITE = "read", "write"


@dataclass(frozen=True, slots=True)
class Scope:
    """What one owner decision may cover. Every field narrows; none widens."""
    tool: str
    effect_class: str
    scope_key: str = ""
    agent_id: int | None = None
    task_id: int | None = None

    def describe(self) -> str:
        where = f", режим {self.scope_key}" if self.scope_key else ""
        return (f"{self.tool} ({'только чтение' if self.effect_class == READ else 'изменение'}"
                f"{where}) в рамках задачи {self.task_id}")


def effect_class(tool: str, args: dict | None) -> str:
    """READ or WRITE for this exact call, using the executor's own classifier.

    Deliberately delegates to `action_contract._looks_like_mutation` and the
    finalizer's redirect/git checks rather than re-deriving "is this safe":
    a second, more permissive opinion about what counts as a write is exactly
    how a read lease would end up covering `echo x > file`."""
    if tool != "terminal.run":
        # Only terminal.run has a per-argument read/write split in this codebase.
        # Everything else is classified by its own spec at the call site, and an
        # unrecognised tool is a write — the safe direction.
        from .tools import REGISTRY
        spec = REGISTRY.get(tool)
        if spec is not None and spec.category == "read" and getattr(spec, "idempotent", True):
            return READ
        return WRITE
    command = str((args or {}).get("command") or "")
    if not command or "***REDACTED***" in command:
        # The audit copy of the arguments is redacted by key name and by known
        # secret values. A command whose text was scrubbed cannot be classified,
        # and an unclassifiable command is a write — never the permissive guess.
        return WRITE
    from .finalize import _effectful
    # `_effectful` already fuses `_looks_like_mutation` with the redirect and
    # write-capable-git rules. Reusing it keeps one definition of "write".
    return WRITE if _effectful({"tool": "terminal.run", "args": {"command": command}}) else READ


def scope_key(tool: str, args: dict | None) -> str:
    """The extra dimension the owner sees and the lease is bound to."""
    if tool == "terminal.run":
        return str((args or {}).get("mode") or "")
    return ""


def scope_for(tool: str, args: dict | None, *, agent: dict | None,
              task: dict | None) -> Scope:
    return Scope(tool=tool, effect_class=effect_class(tool, args),
                 scope_key=scope_key(tool, args),
                 agent_id=(agent or {}).get("id"), task_id=(task or {}).get("id"))


# ------------------------------------------------------------------ dedup

async def find_reusable(svc, *, args_hash: str, run_id: int) -> dict | None:
    """An identical question already asked in this run and still unanswered.

    Grants nothing: the returned row is still `pending`. It only prevents the
    owner's queue from filling with copies of one question they have not
    answered yet, which is what made the storm unreadable."""
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(approvals_t).join(
            tool_calls_t, tool_calls_t.c.approval_id == approvals_t.c.id).where(
            tool_calls_t.c.run_id == run_id,
            tool_calls_t.c.status == "pending_approval",
            approvals_t.c.status == "pending",
            tool_calls_t.c.args_hash == args_hash).order_by(
            approvals_t.c.id.desc()).limit(1))).first()
    return dict(row._mapping) if row is not None else None


async def previously_rejected(svc, *, args_hash: str, run_id: int) -> bool:
    """Has the owner already said no to this exact call in this run?

    Asking again after a refusal is the amplification pointed the wrong way:
    it converts one "no" into pressure to eventually answer "yes"."""
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(tool_calls_t.c.id).join(
            approvals_t, approvals_t.c.id == tool_calls_t.c.approval_id).where(
            tool_calls_t.c.run_id == run_id,
            tool_calls_t.c.args_hash == args_hash,
            approvals_t.c.status.in_(("rejected", "revoked"))).limit(1))).first()
    return row is not None


# ------------------------------------------------------------------ leases

async def grant(svc, *, approval: dict, scope: Scope, max_uses: int, ttl_seconds: int,
                by: str = "owner") -> dict:
    """Create a lease from an approval the owner has just granted.

    Both bounds are clamped rather than trusted: a caller (including a
    compromised UI) cannot mint an eternal or unlimited authority."""
    uses = max(1, min(int(max_uses), MAX_LEASE_USES))
    ttl = max(1, min(int(ttl_seconds), MAX_LEASE_TTL_SECONDS))
    async with svc.db.session() as s:
        res = await s.execute(sa.insert(leases_t).values(
            approval_id=approval.get("id"), task_id=scope.task_id, agent_id=scope.agent_id,
            tool=scope.tool, effect_class=scope.effect_class, scope_key=scope.scope_key,
            max_uses=uses, used=0, expires_at=utcnow() + timedelta(seconds=ttl),
            status="active", granted_by=by, created_at=utcnow()))
        lease_id = int(res.inserted_primary_key[0])
        await s.commit()
        row = (await s.execute(sa.select(leases_t).where(leases_t.c.id == lease_id))).first()
    lease = dict(row._mapping)
    await svc.bus.emit("approval.lease_granted", lease_id=lease_id, tool=scope.tool,
                       effect_class=scope.effect_class, scope_key=scope.scope_key,
                       task_id=scope.task_id, agent_id=scope.agent_id,
                       max_uses=uses, ttl_seconds=ttl, by=by)
    return lease


def _match_clause(scope: Scope):
    """Every dimension must match exactly. A NULL task/agent lease is not a
    wildcard — it simply does not match a scoped call, because the owner who
    granted it was looking at a specific agent and task."""
    return sa.and_(
        leases_t.c.status == "active",
        leases_t.c.tool == scope.tool,
        leases_t.c.effect_class == scope.effect_class,
        sa.func.coalesce(leases_t.c.scope_key, "") == (scope.scope_key or ""),
        leases_t.c.task_id == scope.task_id,
        leases_t.c.agent_id == scope.agent_id,
        leases_t.c.used < leases_t.c.max_uses,
        leases_t.c.expires_at > utcnow(),
    )


async def find_active(svc, scope: Scope) -> dict | None:
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(leases_t).where(_match_clause(scope)).order_by(
            leases_t.c.id.desc()).limit(1))).first()
    return dict(row._mapping) if row is not None else None


async def consume(svc, scope: Scope) -> dict | None:
    """Spend one use of a matching lease, or None if none applies.

    The CAS on `used < max_uses` inside the UPDATE is what makes two concurrent
    calls unable to spend the same use; re-reading and then writing would not."""
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(leases_t.c.id).where(_match_clause(scope)).order_by(
            leases_t.c.id.desc()).limit(1))).first()
        if row is None:
            return None
        lease_id = int(row[0])
        changed = await s.execute(sa.update(leases_t).where(
            leases_t.c.id == lease_id,
            leases_t.c.status == "active",
            leases_t.c.used < leases_t.c.max_uses,
            leases_t.c.expires_at > utcnow()).values(used=leases_t.c.used + 1))
        if not changed.rowcount:
            await s.rollback()
            return None
        await s.execute(sa.update(leases_t).where(
            leases_t.c.id == lease_id,
            leases_t.c.used >= leases_t.c.max_uses).values(status="exhausted"))
        await s.commit()
        lease = dict((await s.execute(sa.select(leases_t).where(
            leases_t.c.id == lease_id))).first()._mapping)
    await svc.bus.emit("approval.lease_used", lease_id=lease_id, tool=scope.tool,
                       effect_class=scope.effect_class, task_id=scope.task_id,
                       used=lease.get("used"), max_uses=lease.get("max_uses"))
    return lease


async def revoke(svc, lease_id: int, by: str = "owner") -> dict | None:
    """Immediate and one-way: an authority the owner has withdrawn must not be
    spendable by a call already in flight."""
    async with svc.db.session() as s:
        changed = await s.execute(sa.update(leases_t).where(
            leases_t.c.id == lease_id, leases_t.c.status == "active").values(
            status="revoked"))
        await s.commit()
        row = (await s.execute(sa.select(leases_t).where(leases_t.c.id == lease_id))).first()
    if row is None:
        return None
    if changed.rowcount:
        await svc.bus.emit("approval.lease_revoked", lease_id=lease_id, by=by)
    return dict(row._mapping)


async def revoke_for_task(svc, task_id: int, by: str = "owner") -> int:
    """Every authority granted inside one mission dies with it."""
    async with svc.db.session() as s:
        changed = await s.execute(sa.update(leases_t).where(
            leases_t.c.task_id == task_id, leases_t.c.status == "active").values(
            status="revoked"))
        await s.commit()
    if changed.rowcount:
        await svc.bus.emit("approval.lease_revoked", task_id=task_id, count=changed.rowcount, by=by)
    return int(changed.rowcount)


async def expire_stale(svc) -> int:
    """Mark elapsed leases so the owner's list shows the truth. Purely
    cosmetic for safety — `_match_clause` already refuses an expired row."""
    async with svc.db.session() as s:
        changed = await s.execute(sa.update(leases_t).where(
            leases_t.c.status == "active", leases_t.c.expires_at <= utcnow()).values(
            status="expired"))
        await s.commit()
    return int(changed.rowcount)


async def listing(svc, *, task_id: int | None = None, active_only: bool = True) -> list[dict]:
    await expire_stale(svc)
    stmt = sa.select(leases_t).order_by(leases_t.c.id.desc()).limit(200)
    if active_only:
        stmt = stmt.where(leases_t.c.status == "active")
    if task_id is not None:
        stmt = stmt.where(leases_t.c.task_id == task_id)
    async with svc.db.session() as s:
        return [dict(r._mapping) for r in (await s.execute(stmt)).fetchall()]


def lease_offer(scope: Scope) -> str:
    """The sentence appended to an approval preview so the owner knows a scoped
    answer is available. Naming the exact scope is the point: an owner who
    cannot see what a lease would cover cannot consent to it."""
    return ("\nМожно ответить один раз на всю область: "
            + scope.describe()
            + f" — не более {MAX_LEASE_USES} вызовов и не дольше "
              f"{MAX_LEASE_TTL_SECONDS // 3600} ч, отзыв в любой момент "
              "(поле lease при подтверждении).")


# ------------------------------------------------------------------ budget

#: A mission that has asked this many times is not collaborating with its
#: owner, it is wearing them down. Configurable per task via
#: `meta.approval_budget`; the default is generous enough for real work and
#: small enough that 161 questions can never happen again unnoticed.
DEFAULT_APPROVAL_BUDGET = 25


async def spent(svc, task_id: int) -> int:
    async with svc.db.session() as s:
        return int((await s.execute(sa.select(sa.func.count()).select_from(approvals_t).where(
            approvals_t.c.task_id == task_id,
            approvals_t.c.kind == "tool"))).scalar() or 0)


def budget_of(task: dict | None) -> int:
    meta = (task or {}).get("meta") or {}
    try:
        value = int(meta.get("approval_budget", DEFAULT_APPROVAL_BUDGET))
    except (TypeError, ValueError):
        return DEFAULT_APPROVAL_BUDGET
    return value if value >= 0 else DEFAULT_APPROVAL_BUDGET


async def budget_exceeded(svc, task: dict | None) -> tuple[bool, int, int]:
    """(exceeded, spent, budget). Exceeding stops the task; it never
    auto-approves. A budget that silently granted the next request would be an
    authority expansion wearing a limit's clothes."""
    task_id = (task or {}).get("id")
    if task_id is None:
        return False, 0, 0
    budget = budget_of(task)
    used = await spent(svc, int(task_id))
    return used >= budget, used, budget


async def metrics(svc, task_id: int) -> dict[str, Any]:
    """`approvals_per_successful_mission` and what the leases saved."""
    async with svc.db.session() as s:
        asked = int((await s.execute(sa.select(sa.func.count()).select_from(approvals_t).where(
            approvals_t.c.task_id == task_id, approvals_t.c.kind == "tool"))).scalar() or 0)
        leased = int((await s.execute(sa.select(sa.func.coalesce(
            sa.func.sum(leases_t.c.used), 0)).where(
            leases_t.c.task_id == task_id))).scalar() or 0)
        deduped = int((await s.execute(sa.select(sa.func.count()).select_from(tool_calls_t).where(
            tool_calls_t.c.task_id == task_id,
            tool_calls_t.c.lease_id.isnot(None)))).scalar() or 0)
    return {"task_id": task_id, "approvals_asked": asked, "lease_uses": leased,
            "calls_covered_by_lease": deduped,
            "approvals_avoided": max(0, leased)}
