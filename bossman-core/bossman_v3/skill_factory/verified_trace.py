"""Disabled-by-integration journal -> experimental skill adapter.

The trusted host supplies the CURRENT contract, attempt IDs and project root;
these arguments are not model output and confer no execution authorization.
Only bounded POSIX file.write / file.sha256 traces are currently supported.
HMAC proves journal integrity, not an effect: reopen the real file independently.
Unknown adapters, legacy bindings and platforms without safe dirfd reads block.
No journal writes, promotion, replay, model call or LearningGuard bypass occurs.
Future promotion must use existing LearningGuard holdout/security/owner gates.
"""
from __future__ import annotations

from ..memory.anchor import JournalAnchorPort

import copy
import hashlib
import math
import os
import re
import stat
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from bossman_shared.action_receipt import ActionReceipt, request_digest
from bossman_v3 import evidence
from bossman_v3.contracts import SideEffectClass, TypedAction
from bossman_v3.memory.journal import TaskJournal, digest, journal_path
from bossman_v3.organization.bridges import V3ExecutionBridge, step_from_dict, step_to_dict
from bossman_v3.organization.contracts import DelegationContract
from .factory import SkillCandidate, SkillFactory, TraceStep


class UntrustedTrace(ValueError):
    """Reconcile or provide supported current evidence; never guess success."""


@dataclass(frozen=True)
class TraceProvenance:
    task_id: str
    contract_digest: str
    plan_digest: str
    # References only: neither these nor historical scopes are grants.
    applicability: tuple[tuple[str, str], ...]
    required_scope_refs: tuple[tuple[str, ...], ...]
    source_steps: tuple[tuple[str, str, str, str], ...]  # step/action/attempt/effect
    guard_refs: tuple[tuple[str, str], ...]
    schema_version: int = 1


@dataclass(frozen=True)
class JournalSkill:
    candidate: SkillCandidate
    provenance: TraceProvenance
    requires_fresh_authorization: bool = True
    requires_parameter_binding: bool = True


_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,159}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_APPLICABILITY = {"owner_ref", "project_ref", "environment_ref", "implementation_version"}
_MAX_BYTES = 4 * 1024 * 1024


def _reference(value: object) -> str:
    # Never copy free-form metadata, prompt text, credentials, URLs or grants.
    from bossman.obs import redact
    if not isinstance(value, str) or not _REF.fullmatch(value) or redact(value) != value:
        raise UntrustedTrace("invalid applicability or scope reference")
    if "://" in value:
        raise UntrustedTrace("URL is not an applicability reference")
    return value


def _time(value: object) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            raise ValueError
        return dt
    except (ValueError, TypeError) as exc:
        raise UntrustedTrace("missing or invalid trace time") from exc


def _read_digest(root: Path, target: object) -> str:
    """Containment before reads; no symlink traversal or blocking special files."""
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        raise UntrustedTrace("safe local readback unavailable on this platform")
    if not isinstance(target, str) or not target or "\\" in target or "\x00" in target:
        raise UntrustedTrace("invalid relative artifact path")
    parts = target.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise UntrustedTrace("artifact path must remain within the project")
    if len(parts) > 64:
        raise UntrustedTrace("artifact path exceeds directory depth limit")
    opened = []

    def open_walk():
        # Keep directory descriptors live until both walks finish so moved or
        # unlinked ancestors cannot have their inode numbers recycled meanwhile.
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        opened.append(fd)
        identities = []
        for part in parts[:-1]:
            info = os.fstat(fd)
            identities.append((info.st_dev, info.st_ino))
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            opened.append(fd)
        info = os.fstat(fd)
        identities.append((info.st_dev, info.st_ino))
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        opened.append(fd)
        return fd, identities

    def version(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
                info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    try:
        fd, ancestors = open_walk()
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > _MAX_BYTES:
            raise UntrustedTrace("artifact must be a bounded regular file without hardlinks")
        h = hashlib.sha256()
        size = 0
        while block := os.read(fd, min(65536, _MAX_BYTES + 1 - size)):
            size += len(block)
            if size > _MAX_BYTES:
                raise UntrustedTrace("artifact exceeded readback limit")
            h.update(block)
        if version(before) != version(os.fstat(fd)):
            raise UntrustedTrace("artifact changed during readback")
        # An unchanged open file is insufficient: its parent/root pathname may
        # now resolve to a replacement tree. Rewalk from the configured root and
        # require identical directory and final file identities without reading
        # any bytes from the replacement path.
        current_fd, current_ancestors = open_walk()
        if ancestors != current_ancestors or version(before) != version(os.fstat(current_fd)):
            raise UntrustedTrace("artifact path changed during readback")
        return h.hexdigest()
    except OSError as exc:
        raise UntrustedTrace("artifact readback unavailable") from exc
    finally:
        for fd in reversed(opened):
            os.close(fd)


def candidate_from_journal(*, name: str, journal_root: Path, project_root: Path,
                           current_contract: DelegationContract,
                           current_attempts: Mapping[str, str],
                           max_age_seconds: float = 300,
                           journal_anchor: JournalAnchorPort | None = None) -> JournalSkill:
    """Return parameterized experimental data, never an executable replay grant.

    current_attempts must come from trusted current execution state, not from the
    submitted trace. Same-key privileged Python can forge HMACs; it is outside
    this data boundary. Independent readback still rejects invented effects.
    A current file hash proves the result state, not who historically wrote it.
    """
    name = _reference(name)
    if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, (float, int)) or not math.isfinite(max_age_seconds) or not 0 < max_age_seconds <= 300:
        raise UntrustedTrace("invalid trace freshness bound")
    # Frozen working copies prevent nested caller mutation from rewriting the
    # expected effect halfway through verification. Check originals again below.
    snapshot = copy.deepcopy(current_contract.to_dict())
    contract = DelegationContract.from_dict(snapshot)
    attempts = dict(current_attempts)
    original_contract_state = digest(snapshot)
    if digest(contract.to_dict()) != original_contract_state:
        raise UntrustedTrace("current contract cannot be snapshotted without changes")
    bound_contract_digest = contract.digest()
    if contract.problems():
        raise UntrustedTrace("invalid current contract")
    applicability = contract.metadata.get("skill_applicability")
    if not isinstance(applicability, dict) or set(applicability) != _APPLICABILITY:
        raise UntrustedTrace("explicit scoped environment/version references required")
    refs = tuple(sorted((k, _reference(v)) for k, v in applicability.items()))
    task_id = V3ExecutionBridge.journal_id(contract)
    _reference(task_id)
    path = journal_path(journal_root, task_id)
    # A writer left active/ambiguous state: do not mine it as a completed trace.
    if path.with_suffix(".lock").exists():
        raise UntrustedTrace("journal writer requires reconciliation")
    j = TaskJournal.load(task_id=task_id, root=journal_root, anchor=journal_anchor)
    initial_disk_digest = j._disk_digest
    plan = [step_from_dict(raw) for raw in contract.steps]
    manifest = [step_to_dict(step) for step in plan]
    if not plan or j.plan_digest != digest(manifest):
        raise UntrustedTrace("current plan digest mismatch")
    binding = {"mission_id": contract.mission_id, "work_id": contract.work_id,
               "contract_digest": bound_contract_digest}
    if j.execution_binding != binding or len(j.finished_signed()) != len(plan) or len(j.steps) != len(plan):
        raise UntrustedTrace("trace incomplete or execution binding mismatch")
    if set(attempts) != {p.step_id for p in plan}:
        raise UntrustedTrace("current attempt set mismatch")
    # A fully completed subplan is insufficient if the current contract still
    # requires another artifact or a different value.
    for requirement in contract.evidence_required:
        if requirement.kind != "file.sha256" or not requirement.target or set(requirement.expect) != {"sha256"}:
            raise UntrustedTrace("unsupported contract evidence obligation")
        if not any(isinstance(p.action.args.get("expect"), dict)
                   and p.action.args["expect"].get("kind") == requirement.kind
                   and p.action.args["expect"].get("target") == requirement.target
                   and p.action.args["expect"].get("sha256") == requirement.expect["sha256"] for p in plan):
            raise UntrustedTrace("unfulfilled contract evidence obligation")
    now = datetime.now(timezone.utc)
    trace, sources, scopes, guards = [], [], [], []
    input_schema, output_schema = {}, {}
    for index, (p, js, m) in enumerate(zip(plan, j.steps, manifest), start=1):
        if js.step_id != p.step_id or js.signer != evidence.JOURNAL_SIGNER or js.action_digest != digest(m) or js.execution_binding != binding:
            raise UntrustedTrace("signed step/current plan mismatch")
        if js.in_flight or not js.attempt_id or js.attempt_id != attempts[p.step_id] or not js.effect_key:
            raise UntrustedTrace("stale or ambiguous execution attempt")
        a = p.action
        if a.action_type != "file.write" or a.side_effect != SideEffectClass.IDEMPOTENT_WRITE:
            raise UntrustedTrace("unsupported learnable action (raw shell is never learnable)")
        if set(a.args) != {"path", "content", "expect"} or not isinstance(a.args["content"], str):
            raise UntrustedTrace("unsupported action arguments or injected metadata")
        expect = a.args["expect"]
        if not isinstance(expect, dict) or set(expect) != {"kind", "target", "sha256"} or expect["kind"] != "file.sha256" or expect["target"] != a.args["path"] or not isinstance(expect["sha256"], str) or not _HASH.fullmatch(expect["sha256"]):
            raise UntrustedTrace("unsupported independent effect obligation")
        if len(a.args["content"].encode()) > _MAX_BYTES or hashlib.sha256(a.args["content"].encode()).hexdigest() != expect["sha256"]:
            raise UntrustedTrace("action content and expected digest disagree")
        body = dict(js.receipt or {})
        r = ActionReceipt.from_dict(body)
        if (r.task_id != task_id or r.step_id != p.step_id or r.tool != a.action_type or r.capability != a.action_type
                or r.run_id != task_id or r.effect_type != a.side_effect.value
                or r.request_digest != request_digest(a.action_type, a.args)
                or r.idempotency_key != (a.idempotency_key or f"{task_id}/{p.step_id}")
                or r.executor_status != "executed" or not r.verified()
                or body.get("expect") != expect or body.get("verification_passed") is not True
                or not body.get("effect_id") or body.get("effect_id") != r.executor_metadata.get("effect_id")):
            raise UntrustedTrace("canonical receipt/current effect mismatch")
        start, finish, observed, signed = map(_time, (r.started_at, r.finished_at, r.observed_at, js.issued_at))
        if not start <= finish <= observed <= signed <= now or observed <= start or (now - observed).total_seconds() > max_age_seconds:
            raise UntrustedTrace("stale or future trace")
        if _read_digest(project_root, expect["target"]) != expect["sha256"]:
            raise UntrustedTrace("independent post-state mismatch")
        scopes.append(tuple(_reference(s) for s in a.scopes))
        sources.append((_reference(p.step_id), js.action_digest, _reference(js.attempt_id), _reference(js.effect_key)))
        guards.append((_reference(p.step_id), _reference(p.guard) if p.guard else ""))
        # Omit all payload text, targets, receipts, approvals, notes and metadata.
        # Parameter placeholders require rebinding and holdout evaluation first.
        params = {key: f"artifact_{key}_{index}" for key in ("path", "content", "sha256")}
        input_schema.update({value: "string" for value in params.values()})
        output_schema[f"artifact_{index}"] = "file.sha256"
        placeholders = {key: "${" + value + "}" for key, value in params.items()}
        template = replace(a, args={"path": placeholders["path"], "content": placeholders["content"],
                                   "expect": {"kind": "file.sha256", "target": placeholders["path"], "sha256": placeholders["sha256"]}},
                           scopes=(), idempotency_key=None, source="verified_trace.experimental")
        trace.append(TraceStep(template, True))
    # Detect concurrent journal replacement even while independent readback ran.
    fresh = TaskJournal.load(task_id=task_id, root=journal_root, anchor=journal_anchor)
    if fresh._disk_digest != initial_disk_digest or path.with_suffix(".lock").exists():
        raise UntrustedTrace("journal changed during candidate extraction")
    if digest(current_contract.to_dict()) != original_contract_state or dict(current_attempts) != attempts:
        raise UntrustedTrace("current execution changed during candidate extraction")
    candidate = SkillFactory().from_verified_trace(name, trace,
        input_schema=input_schema, output_schema=output_schema)
    return JournalSkill(candidate, TraceProvenance(task_id, bound_contract_digest, j.plan_digest,
                        refs, tuple(scopes), tuple(sources), tuple(guards)))
