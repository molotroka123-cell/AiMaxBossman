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
import os
from pathlib import Path
import re
from typing import Any

import sqlalchemy as sa

from .db import (approval_leases as leases_t, approvals as approvals_t,
                 tool_calls as tool_calls_t, utcnow)

#: Hard ceilings. A lease is a convenience, never an open door: an owner who
#: needs more than this is really granting the agent a permission, and should
#: do that explicitly through the permission model instead.
MAX_LEASE_USES = 200
MAX_AUTO_FULL_USES = 500
MAX_LEASE_TTL_SECONDS = 4 * 3600

READ, WRITE, PRIVILEGED, AUTO_FULL = "read", "write", "privileged", "auto_full"

#: Tools whose every effect is its own owner decision (rc19 CU-ONESHOT). A lease
#: would pre-authorize up to MAX_LEASE_USES desktop actions the owner never saw;
#: the computer.act handler refuses lease-driven calls anyway, so offering or
#: granting one would only mislead the owner about what they are consenting to.
NON_LEASABLE_TOOLS = frozenset({"computer.act"})
#: Security audit 31.10 (1b): `git push`, `pip/npm/uv/poetry install`, `docker compose`,
#: `sudo`, `curl|sh`, and `network=True` must NEVER be covered by a time-bounded write lease.
NON_LEASABLE_EFFECT_CLASSES = frozenset({PRIVILEGED})


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
        if self.effect_class == READ:
            label = "только чтение"
        elif self.effect_class == PRIVILEGED:
            label = "привилегированное действие (без аренды)"
        elif self.effect_class == AUTO_FULL:
            label = "Auto Full (папка)"
        else:
            label = "изменение"
        return f"{self.tool} ({label}{where}) в рамках задачи {self.task_id}"


def effect_class(tool: str, args: dict | None) -> str:
    """READ, WRITE, or PRIVILEGED for this exact call, using the executor's own classifier.

    Deliberately delegates to `action_contract._looks_like_mutation` and the
    finalizer's redirect/git checks rather than re-deriving "is this safe":
    a second, more permissive opinion about what counts as a write is exactly
    how a read lease would end up covering `echo x > file`.
    Privileged operations (`git push`, `pip install`, `sudo`, `docker compose`,
    `curl|sh`, `network=True`) return `PRIVILEGED` so a write lease never covers them."""
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
    from .features.tools_terminal import _piped_remote_url, extra_ask_reason
    if extra_ask_reason(command) or _piped_remote_url(command) or bool((args or {}).get("network")):
        return PRIVILEGED
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
                by: str = "owner", session=None) -> dict:
    """Insert a bounded lease; the HTTP path supplies its decision transaction.

    A supplied session is never committed and never emits an event here. Its
    caller owns pending -> approved and publishes only after committing both
    records. Standalone trusted callers retain the previous fixture/internal
    API; when an approval id is provided it must be live, approved, scoped to
    the task, and not already associated with any lease (including spent ones).
    """
    if scope.tool in NON_LEASABLE_TOOLS or scope.effect_class in NON_LEASABLE_EFFECT_CLASSES:
        raise PermissionError(f"{scope.tool} ({scope.effect_class}): each action needs its own owner approval; no lease")
    uses = max(1, min(int(max_uses), MAX_LEASE_USES))
    ttl = max(1, min(int(ttl_seconds), MAX_LEASE_TTL_SECONDS))

    async def insert(s):
        if approval.get("id") is not None:
            # A real UPDATE serializes grants against this approval on SQLite
            # and PostgreSQL. The subsequent SELECT observes a committed earlier
            # grant; a process-local mutex or a read-before-write cannot do so.
            locked = await s.execute(sa.update(approvals_t).where(
                approvals_t.c.id == approval["id"],
                approvals_t.c.status == "approved",
                approvals_t.c.task_id == scope.task_id).values(status="approved"))
            if not locked.rowcount:
                raise PermissionError("approval is not approved for this task")
            existing = (await s.execute(sa.select(leases_t.c.id).where(
                leases_t.c.approval_id == approval["id"]).limit(1))).first()
            if existing is not None:
                raise PermissionError("approval already granted a lease")
        now = utcnow()
        res = await s.execute(sa.insert(leases_t).values(
            approval_id=approval.get("id"), task_id=scope.task_id, agent_id=scope.agent_id,
            tool=scope.tool, effect_class=scope.effect_class, scope_key=scope.scope_key,
            max_uses=uses, used=0, expires_at=now + timedelta(seconds=ttl),
            status="active", granted_by=by, created_at=now))
        lease_id = int(res.inserted_primary_key[0])
        row = (await s.execute(sa.select(leases_t).where(leases_t.c.id == lease_id))).first()
        return dict(row._mapping)

    if session is not None:
        return await insert(session)
    async with svc.db.session() as s:
        lease = await insert(s)
        await s.commit()
    await svc.bus.emit("approval.lease_granted", lease_id=lease["id"], tool=scope.tool,
                       effect_class=scope.effect_class, scope_key=scope.scope_key,
                       task_id=scope.task_id, agent_id=scope.agent_id,
                       max_uses=uses, ttl_seconds=ttl, by=by)
    return lease


_ABS_WIN_OR_POSIX_PATH_RE = re.compile(
    r"""(?P<path>[A-Za-z]:[\\/][^\s"'<>|*?]+|(?<![A-Za-z0-9_.:/])/[A-Za-z0-9_.-]+/[^\s"'<>|*?]+)"""
)
_HOME_EXPANSION_RE = re.compile(r"(?i)(?:%USERPROFILE%|\$env:USERPROFILE|\$HOME|(?:^|[\s\"'=])~(?:[\\/]|$))")
_SAFE_EXEC_PREFIXES = (
    os.path.normcase(r"C:\Windows\System32"),
    os.path.normcase(r"C:\Program Files\Git"),
)


def validate_auto_full_folder(folder: str | Path) -> Path:
    """Resolve and validate the owner's selected folder for Auto Full."""
    raw = str(folder or "").strip()
    if not raw or raw == "scratch":
        raise ValueError("нужно указать существующую рабочую папку для Auto Full")
    try:
        resolved = Path(raw).expanduser().resolve()
    except OSError as exc:
        raise ValueError(f"некорректный путь папки: {exc}") from exc
    if not resolved.is_dir():
        raise ValueError(f"папка не существует: {resolved}")
    if resolved.parent == resolved:
        raise PermissionError("корень диска нельзя выбрать для Auto Full")
    home = Path.home().resolve()
    if resolved == home or resolved == home.parent:
        raise PermissionError("домашний каталог целиком нельзя выбрать для Auto Full — выберите конкретную рабочую папку")
    norm = os.path.normcase(str(resolved))
    for sys_root in (r"c:\windows", r"c:\program files", r"c:\program files (x86)", r"c:\programdata", "/etc", "/usr", "/var", "/root"):
        if norm == os.path.normcase(sys_root) or norm.startswith(os.path.normcase(sys_root) + os.sep):
            raise PermissionError(f"системный каталог запрещён для Auto Full: {resolved}")
    # Owner 31.10: the first allowed terminal root on the owner PC is the Python installation (Python312\Lib). Installed
    # programs and the running interpreter are never an Auto Full work folder.
    import sys as _sys
    installs = {Path(_sys.base_prefix).resolve(), Path(_sys.prefix).resolve(), home / "AppData" / "Local" / "Programs"}
    for inst in installs:
        try:
            resolved.relative_to(inst)
        except ValueError:
            continue
        raise PermissionError(f"папка установленной программы/Python запрещена для Auto Full: {resolved}")
    from .features.tools_terminal import hard_deny_reason
    deny = hard_deny_reason(str(resolved))
    if deny:
        raise PermissionError(f"каталог запрещён политикой безопасности ({deny})")
    return resolved


def _call_within_auto_full_folder(folder: Path, args: dict | None, workspace: str | None = None) -> bool:
    """True only when `terminal.run` stays strictly inside `folder` and is not privileged/denied."""
    from .features.tools_terminal import _piped_remote_url, extra_ask_reason, hard_deny_reason
    from .v2.terminal_control import within
    args = args or {}
    command = str(args.get("command") or "").strip()
    if not command or "***REDACTED***" in command:
        return False
    if hard_deny_reason(command) or extra_ask_reason(command) or _piped_remote_url(command) or bool(args.get("network")):
        return False
    if _HOME_EXPANSION_RE.search(command):
        return False
    try:
        folder_resolved = folder.resolve()
    except OSError:
        return False
    raw_cwd = str(args.get("cwd") or workspace or "").strip()
    if raw_cwd == "scratch":
        return False
    if raw_cwd:
        try:
            cwd = Path(raw_cwd).expanduser().resolve()
        except OSError:
            return False
        if not within(cwd, [folder_resolved]):
            return False
    else:
        cwd = folder_resolved
    # Check any explicit absolute paths in command
    for m in _ABS_WIN_OR_POSIX_PATH_RE.finditer(command):
        candidate_str = m.group("path").rstrip(".,;:)]}'\"")
        if candidate_str.lower() in ("/dev/null", "/dev/stdout", "/dev/stderr"):
            continue
        try:
            cand = Path(candidate_str).resolve()
        except OSError:
            return False
        if within(cand, [folder_resolved]):
            continue
        norm_cand = os.path.normcase(str(cand))
        py_dir = os.path.normcase(str(Path(os.sys.executable).resolve().parent))
        if norm_cand.startswith(py_dir) or any(norm_cand.startswith(p) for p in _SAFE_EXEC_PREFIXES):
            continue
        return False
    # Check any parent traversal (`..`) tokens against cwd
    for tok in re.split(r"[\s\"';&|()<>]+", command):
        if ".." in tok:
            cleaned = tok.split("=", 1)[-1].strip("\"'")
            try:
                target = (cwd / cleaned).resolve()
            except OSError:
                return False
            if not within(target, [folder_resolved]):
                return False
    return True


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
        # Аренда живёт, пока живо решение, которое её выдало: отозванное или
        # отклонённое одобрение не покрывает вызовы, даже если строку аренды
        # забыли погасить. approved/consumed — нормальная жизнь выданного «да».
        sa.or_(leases_t.c.approval_id.is_(None), sa.exists().where(
            approvals_t.c.id == leases_t.c.approval_id,
            approvals_t.c.status.in_(("approved", "consumed")))),
    )


async def find_active(svc, scope: Scope) -> dict | None:
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(leases_t).where(_match_clause(scope)).order_by(
            leases_t.c.id.desc()).limit(1))).first()
    return dict(row._mapping) if row is not None else None


async def consume(svc, scope: Scope, *, args: dict | None = None,
                  workspace: str | None = None) -> dict | None:
    """Spend one use of a matching lease (or active folder-scoped Auto Full), or None if none applies.

    The CAS on `used < max_uses` inside the UPDATE is what makes two concurrent
    calls unable to spend the same use; re-reading and then writing would not."""
    if scope.tool in NON_LEASABLE_TOOLS or scope.effect_class in NON_LEASABLE_EFFECT_CLASSES:
        return None
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(leases_t.c.id).where(_match_clause(scope)).order_by(
            leases_t.c.id.desc()).limit(1))).first()
        lease_id = int(row[0]) if row is not None else None
        if lease_id is None and scope.tool == "terminal.run" and scope.effect_class in (READ, WRITE) and args is not None:
            af_rows = (await s.execute(sa.select(leases_t).where(
                leases_t.c.status == "active",
                leases_t.c.tool == "terminal.run",
                leases_t.c.effect_class == AUTO_FULL,
                leases_t.c.used < leases_t.c.max_uses,
                leases_t.c.expires_at > utcnow(),
                sa.or_(leases_t.c.task_id.is_(None), leases_t.c.task_id == scope.task_id),
                sa.or_(leases_t.c.agent_id.is_(None), leases_t.c.agent_id == scope.agent_id),
            ).order_by(leases_t.c.id.desc()))).fetchall()
            for cand in af_rows:
                cand_map = dict(cand._mapping)
                folder_str = str(cand_map.get("scope_key") or "").strip()
                if folder_str and _call_within_auto_full_folder(Path(folder_str), args, workspace):
                    lease_id = int(cand_map["id"])
                    break
        if lease_id is None:
            return None
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
                       effect_class=lease.get("effect_class") or scope.effect_class,
                       task_id=scope.task_id,
                       used=lease.get("used"), max_uses=lease.get("max_uses"))
    return lease


async def active_auto_full_folders(svc) -> list[Path]:
    """Currently active Auto Full folders so `tools_terminal._roots` permits execution inside them
    only while the lease is active and unrevoked."""
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(leases_t.c.scope_key).where(
            leases_t.c.status == "active",
            leases_t.c.tool == "terminal.run",
            leases_t.c.effect_class == AUTO_FULL,
            leases_t.c.used < leases_t.c.max_uses,
            leases_t.c.expires_at > utcnow()).order_by(leases_t.c.id.desc()))).fetchall()
    out: list[Path] = []
    for (folder_str,) in rows:
        if folder_str:
            try:
                p = Path(str(folder_str)).resolve()
                if p.is_dir() and p not in out:
                    out.append(p)
            except OSError:
                continue
    return out


async def grant_auto_full(svc, *, folder: str | Path, ttl_seconds: int = 3600,
                          max_uses: int = 200, task_id: int | None = None,
                          agent_id: int | None = None, by: str = "owner") -> dict:
    """Owner explicitly enables Auto Full for a chosen folder (`scope_key`) with a mandatory expiry."""
    resolved = validate_auto_full_folder(folder)
    uses = max(1, min(int(max_uses), MAX_AUTO_FULL_USES))
    ttl = max(1, min(int(ttl_seconds), MAX_LEASE_TTL_SECONDS))
    now = utcnow()
    async with svc.db.session() as s:
        # Revoke any previous active Auto Full on the same scope/task so there is one clear card state
        revoke_where = [
            leases_t.c.status == "active",
            leases_t.c.tool == "terminal.run",
            leases_t.c.effect_class == AUTO_FULL,
        ]
        if task_id is not None:
            revoke_where.append(leases_t.c.task_id == task_id)
        await s.execute(sa.update(leases_t).where(*revoke_where).values(status="revoked"))
        res = await s.execute(sa.insert(leases_t).values(
            approval_id=None, task_id=task_id, agent_id=agent_id,
            tool="terminal.run", effect_class=AUTO_FULL, scope_key=str(resolved),
            max_uses=uses, used=0, expires_at=now + timedelta(seconds=ttl),
            status="active", granted_by=by, created_at=now))
        lease_id = int(res.inserted_primary_key[0])
        row = (await s.execute(sa.select(leases_t).where(leases_t.c.id == lease_id))).first()
        await s.commit()
        lease = dict(row._mapping)
    await svc.bus.emit("approval.auto_full_granted", lease_id=lease["id"],
                       folder=str(resolved), task_id=task_id, agent_id=agent_id,
                       max_uses=uses, ttl_seconds=ttl, by=by)
    return {**lease, "folder": str(resolved)}


async def revoke_auto_full(svc, *, lease_id: int | None = None, by: str = "owner") -> dict:
    """Owner OFF button: immediately revokes active Auto Full lease(s)."""
    async with svc.db.session() as s:
        where = [leases_t.c.status == "active", leases_t.c.effect_class == AUTO_FULL]
        if lease_id is not None:
            where.append(leases_t.c.id == int(lease_id))
        changed = await s.execute(sa.update(leases_t).where(*where).values(status="revoked"))
        await s.commit()
    count = int(changed.rowcount or 0)
    if count:
        await svc.bus.emit("approval.auto_full_revoked", lease_id=lease_id, count=count, by=by)
    return {"ok": True, "revoked_count": count, "lease_id": lease_id, "by": by}


async def auto_full_status(svc, *, task_id: int | None = None, limit_log: int = 40) -> dict:
    """Owner Card payload: selected folder, expiry/remaining seconds, action log, and active status."""
    from .plugin_security import redact_text as _ps_redact_text
    await expire_stale(svc)
    now = utcnow()
    async with svc.db.session() as s:
        stmt = sa.select(leases_t).where(leases_t.c.effect_class == AUTO_FULL).order_by(leases_t.c.id.desc()).limit(20)
        if task_id is not None:
            stmt = stmt.where(sa.or_(leases_t.c.task_id.is_(None), leases_t.c.task_id == task_id))
        rows = [dict(r._mapping) for r in (await s.execute(stmt)).fetchall()]
        active = [r for r in rows if r.get("status") == "active" and r.get("expires_at") and r["expires_at"] > now]
        current = active[0] if active else None
        target_lease = current or (rows[0] if rows else None)
        action_log: list[dict] = []
        if target_lease is not None:
            lease_ids = [r["id"] for r in active] if active else [target_lease["id"]]
            tc_rows = (await s.execute(sa.select(tool_calls_t).where(
                tool_calls_t.c.lease_id.in_(lease_ids)).order_by(
                tool_calls_t.c.id.desc()).limit(max(1, min(int(limit_log), 200))))).fetchall()
            for tc in tc_rows:
                m = dict(tc._mapping)
                args_dict = m.get("args") if isinstance(m.get("args"), dict) else {}
                cmd = _ps_redact_text(str(args_dict.get("command") or ""))
                cwd = str(args_dict.get("cwd") or "")
                action_log.append({
                    "id": m.get("id"),
                    "lease_id": m.get("lease_id"),
                    "task_id": m.get("task_id"),
                    "run_id": m.get("run_id"),
                    "tool": m.get("tool"),
                    "command": cmd[:400],
                    "cwd": cwd,
                    "status": m.get("status"),
                    "result_preview": _ps_redact_text(str(m.get("result_preview") or ""))[:240],
                    "created_at": m.get("created_at").isoformat() if hasattr(m.get("created_at"), "isoformat") else str(m.get("created_at") or ""),
                })

    def _serialize_lease(r: dict | None) -> dict | None:
        if r is None:
            return None
        exp = r.get("expires_at")
        rem = max(0, int((exp - now).total_seconds())) if exp and hasattr(exp, "__sub__") else 0
        return {
            "id": r["id"],
            "folder": r.get("scope_key") or "",
            "scope_key": r.get("scope_key") or "",
            "effect_class": r.get("effect_class"),
            "status": r.get("status"),
            "used": r.get("used", 0),
            "max_uses": r.get("max_uses", 0),
            "task_id": r.get("task_id"),
            "agent_id": r.get("agent_id"),
            "granted_by": r.get("granted_by"),
            "created_at": r["created_at"].isoformat() if hasattr(r.get("created_at"), "isoformat") else str(r.get("created_at") or ""),
            "expires_at": exp.isoformat() if hasattr(exp, "isoformat") else str(exp or ""),
            "remaining_seconds": rem if r.get("status") == "active" else 0,
        }

    return {
        "active": bool(current),
        "current": _serialize_lease(current),
        "last_lease": _serialize_lease(target_lease),
        "leases": [_serialize_lease(r) for r in active],
        "action_log": action_log,
    }


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
    if scope.tool in NON_LEASABLE_TOOLS or scope.effect_class in NON_LEASABLE_EFFECT_CLASSES:
        return ("\nОдобрение одноразовое: привилегированное или настольное действие; "
                "аренда на время для него не выдаётся.")
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
