"""V5 admission kernel: the boundary where a proposal may become authority.

The single permanent rule of this module is PROPOSAL != AUTHORIZATION. A
`bossman_shared.objective_spec.ProposalProjection` is dedup material produced by
a pure function; it proves that a deviation was observed, never that work may
run. Authority is resolved here, at admission time, from the CURRENT canonical
stores -- durable objective state, current policy grants, the conflict registry
and the Treasury -- and never from metadata copied into the proposal. Anything a
proposal carries about lifecycle, revision, grants or cost is a claim to be
re-checked, not a fact to be trusted.

Invariants carried:

* Admission re-reads the durable record. The lifecycle, spec digest, revision
  and stop state a proposal was born under are worthless at admission.
* Checks run in a fixed order and fail closed at the first refusal, with a
  machine-readable reason code.
* A durable PENDING intent precedes external ports. Replays cannot buy a second
  reservation or release the winner's conflict claim, including across restart.
* Known refusals compensate acknowledged holds. Lost acknowledgements or failed
  compensation remain PENDING for recovery; unknown funds are never called free.
* READY publication rechecks the current objective version and binds the full
  proposal/effect/verifier payload. A database slot alone is not authority.
* Unknown cost can never become free: a missing or non-finite estimate is
  refused rather than reserved as zero.
* APPROVAL != POST_STATE. `reauthorize_at_effect_boundary` is a second,
  independent authorization taken at the effect boundary, so a revoke or expiry
  landing while work is queued produces zero unauthorized effects.

Deliberate non-goals. No policy engine, no Treasury ledger, no conflict
registry, no scheduler, no dispatcher, no evidence signer and no model call live
here. Those are canonical Bossman services; this module declares narrow ports
they already satisfy and sequences them. It also does not build Mission IR --
`bossman_shared.objective_mission` translates an admitted proposal.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import json
import math
from typing import Any, Protocol, runtime_checkable

from .objective_spec import ObjectiveValidationError, ProposalProjection, project_proposal
from .objective_store import (
    DuplicateProposal,
    DuplicateReservation,
    ObjectiveRuntimeState,
    ObjectiveStore,
    ObjectiveStoreError,
)

# Machine-readable refusal codes. Callers branch on these; prose belongs in
# `AdmissionDecision.detail`, which is never load-bearing.
UNKNOWN_OBJECTIVE = "unknown_objective"
LIFECYCLE_NOT_ACTIVE = "lifecycle_not_active"
OBJECTIVE_EXPIRED = "objective_expired"
OBJECTIVE_STOPPED = "objective_stopped"
STALE_REVISION = "stale_revision"
STALE_SPEC_DIGEST = "stale_spec_digest"
SPEC_UNREADABLE = "spec_unreadable"
PROPOSAL_EXPIRED = "proposal_expired"
OBSERVATION_STALE = "observation_stale"
COOLDOWN_ACTIVE = "cooldown_active"
CONFLICT_HELD = "conflict_held"
POLICY_DENIED = "policy_denied"
CAPABILITY_NOT_GRANTED = "capability_not_granted"
PERMISSION_NOT_GRANTED = "permission_not_granted"
COST_ESTIMATE_UNKNOWN = "cost_estimate_unknown"
BUDGET_DENIED = "budget_denied"
DUPLICATE_RESERVATION = "duplicate_reservation"
ADMITTED = "admitted"
INVALID_CLOCK = "invalid_admission_clock"
PROPOSAL_MISMATCH = "proposal_binding_mismatch"
ADMISSION_STATE_CHANGED = "admission_state_changed"
ADMISSION_RECOVERY_REQUIRED = "admission_recovery_required"

# A policy port may return one of these directly; anything else degrades to the
# generic denial so a port can never invent a reason code the kernel honours.
_POLICY_CODES = frozenset({CAPABILITY_NOT_GRANTED, PERMISSION_NOT_GRANTED, POLICY_DENIED})

ORG_SCOPE = "organization"


class AdmissionError(RuntimeError):
    """Admission could not even be attempted; not a refusal decision."""


class AlreadyProposed(AdmissionError):
    """This proposal identity is already recorded; there is no second proposal."""


# --------------------------------------------------------------------- ports


@runtime_checkable
class PolicyPort(Protocol):
    """Current grants, resolved live. Grants are never read from a proposal."""

    def check_grants(self, owner_id: str, scope_id: str,
                     permission_refs: tuple[str, ...],
                     capabilities: tuple[str, ...]) -> tuple[bool, str]:
        ...


@runtime_checkable
class TreasuryPort(Protocol):
    """Adapter onto the canonical `ResourceTreasury` reserve/commit/release.

    The semantics are the canonical ones and are not reimplemented here: an
    estimate is reserved before delegation, the fact is committed after, and the
    reservation is released on every other outcome.
    """

    def reserve(self, scopes: tuple[str, ...],
                estimate: CostEstimate) -> tuple[bool, str, str]:
        ...

    def release(self, scopes: tuple[str, ...], estimate: CostEstimate) -> None:
        ...

    def commit(self, scopes: tuple[str, ...], estimate: CostEstimate,
               actual: CostEstimate) -> None:
        ...


@runtime_checkable
class ConflictPort(Protocol):
    """Current holder registry for conflict keys; claims are exclusive."""

    def claim(self, conflict_keys: tuple[str, ...], objective_id: str,
              priority: int) -> tuple[bool, str]:
        ...

    def release(self, conflict_keys: tuple[str, ...], objective_id: str) -> None:
        ...


# ----------------------------------------------------------------- contracts


@dataclass(frozen=True, slots=True)
class CostEstimate:
    """A bounded estimate. Absence and non-finiteness are refusals, not zeros."""

    cost_usd: float
    tokens: int
    wall_seconds: float

    def is_known(self) -> bool:
        for value, integral in ((self.cost_usd, False), (self.tokens, True),
                                (self.wall_seconds, False)):
            if type(value) is bool or type(value) not in ((int,) if integral else (int, float)):
                return False
            try:
                if not math.isfinite(value) or value < 0:
                    return False
            except OverflowError:
                return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {"cost_usd": float(self.cost_usd), "tokens": int(self.tokens),
                "wall_seconds": float(self.wall_seconds)}


@dataclass(frozen=True, slots=True, eq=False)
class AdmissionProposal:
    """A recorded proposal. Every field here is a claim, re-checked at admit."""

    proposal_id: str
    objective_id: str
    objective_digest: str
    objective_revision: int
    created_at: float
    valid_until: float
    freshness_deadline: float
    observation_digests: tuple[str, ...]
    trigger: str
    requested_capabilities: tuple[str, ...] = ()
    expected_effects: tuple[dict[str, Any], ...] = ()
    cost_estimate: CostEstimate | None = None
    # Never an authorization, whatever the surrounding service believes.
    admission_allowed: bool = field(default=False, init=False)

    def to_payload(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "objective_id": self.objective_id,
            "objective_digest": self.objective_digest,
            "objective_revision": self.objective_revision,
            "created_at": self.created_at,
            "valid_until": self.valid_until,
            "freshness_deadline": self.freshness_deadline,
            "observation_digests": list(self.observation_digests),
            "trigger": self.trigger,
            "requested_capabilities": list(self.requested_capabilities),
            "expected_effects": [dict(e) for e in self.expected_effects],
            "cost_estimate": None if self.cost_estimate is None else self.cost_estimate.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    """The whole outcome. `admitted` is only ever true with a reservation."""

    admitted: bool
    reason: str
    reservation_id: str | None = None
    mission_intent_id: str | None = None
    objective_id: str = ""
    objective_digest: str = ""
    objective_revision: int = 0
    proposal_id: str = ""
    granted_capabilities: tuple[str, ...] = ()
    authorized_scope_refs: tuple[str, ...] = ()
    treasury_scopes: tuple[str, ...] = ()
    decided_at: float = 0.0
    detail: str = ""
    proposal_digest: str = ""
    owner_id: str = ""
    scope_id: str = ""


def _digest(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False,
                      separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _valid_time(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def proposal_digest(proposal: AdmissionProposal) -> str:
    """Bind ALL effect/verifier/cost content, not just observation identity."""
    return _digest(proposal.to_payload())


def _intent_id(reservation_id: str, proposal: AdmissionProposal) -> str:
    return _digest({"reservation_id": reservation_id,
                    "effects": [dict(e) for e in proposal.expected_effects],
                    "capabilities": list(proposal.requested_capabilities)})


def decision_matches_proposal(decision: AdmissionDecision, proposal: AdmissionProposal) -> bool:
    try:
        return (type(decision) is AdmissionDecision and type(proposal) is AdmissionProposal
                and decision.admitted is True and bool(decision.reservation_id)
                and decision.objective_id == proposal.objective_id
                and decision.objective_digest == proposal.objective_digest
                and decision.objective_revision == proposal.objective_revision
                and decision.proposal_id == proposal.proposal_id
                and decision.granted_capabilities == proposal.requested_capabilities
                and bool(decision.proposal_digest)
                and decision.proposal_digest == proposal_digest(proposal)
                and bool(decision.owner_id) and bool(decision.scope_id)
                and decision.authorized_scope_refs == (
                    f"scope:{decision.scope_id}", f"objective:{decision.objective_id}")
                and decision.treasury_scopes == (ORG_SCOPE,
                    f"scope:{decision.scope_id}", f"objective:{decision.objective_id}")
                and decision.mission_intent_id == _intent_id(decision.reservation_id, proposal)
                and _valid_time(decision.decided_at))
    except (TypeError, ValueError, OverflowError, AttributeError):
        return False


def _binding(decision: AdmissionDecision) -> dict[str, Any]:
    return {"objective_id": decision.objective_id, "objective_digest": decision.objective_digest,
            "objective_revision": decision.objective_revision, "proposal_id": decision.proposal_id,
            "proposal_digest": decision.proposal_digest, "owner_id": decision.owner_id,
            "scope_id": decision.scope_id, "mission_intent_id": decision.mission_intent_id,
            "reservation_id": decision.reservation_id,
            "capabilities": list(decision.granted_capabilities),
            "scope_refs": list(decision.authorized_scope_refs),
            "treasury_scopes": list(decision.treasury_scopes), "decided_at": decision.decided_at}


def _recorded_matches(store: ObjectiveStore, proposal: AdmissionProposal) -> bool:
    try:
        row = store.get_proposal(proposal.proposal_id)
        return (row["objective_id"] == proposal.objective_id
                and row["objective_digest"] == proposal.objective_digest
                and row["objective_revision"] == proposal.objective_revision
                and _digest(row["payload"]) == proposal_digest(proposal))
    except (ObjectiveStoreError, TypeError, ValueError, OverflowError, AttributeError):
        return False


def treasury_scopes(state: ObjectiveRuntimeState) -> tuple[str, ...]:
    """Envelopes every objective touches, outermost first, as Treasury expects."""
    return (ORG_SCOPE, f"scope:{state.scope_id}", f"objective:{state.objective_id}")


def authorized_scope_refs(state: ObjectiveRuntimeState) -> tuple[str, ...]:
    return (f"scope:{state.scope_id}", f"objective:{state.objective_id}")


# ------------------------------------------------------------------ proposal


def build_proposal(store: ObjectiveStore, objective_id: str, observations: list[dict],
                   now: float, trigger: str, *,
                   requested_capabilities: tuple[str, ...] = (),
                   expected_effects: tuple[dict[str, Any], ...] = (),
                   cost_estimate: CostEstimate | None = None) -> AdmissionProposal | None:
    """Project a proposal from the durable record and record it exactly once.

    The spec is the STORED one: a caller-supplied spec would let the proposer
    choose the rules it is judged by. The snapshot comes from the durable row via
    `proposal_snapshot()`, so cumulative usage, enrollment and stop state are
    canonical. `None` means there is nothing to propose -- no deviation, stopped,
    cooling down, quota spent, or not ACTIVE right now.
    """
    state = store.get(objective_id)
    spec = store.get_spec(objective_id)
    projection = project_proposal(spec, observations, now=now,
                                  snapshot=state.proposal_snapshot(), trigger=trigger)
    if projection is None:
        return None
    proposal = AdmissionProposal(
        proposal_id=projection.proposal_id,
        objective_id=state.objective_id,
        objective_digest=projection.objective_digest,
        objective_revision=state.revision,
        created_at=now,
        valid_until=projection.valid_until,
        freshness_deadline=_freshness_deadline(spec, observations, projection),
        observation_digests=projection.observation_digests,
        trigger=trigger,
        requested_capabilities=tuple(requested_capabilities),
        expected_effects=tuple(json.loads(json.dumps(list(expected_effects), allow_nan=False))),
        cost_estimate=cost_estimate,
    )
    try:
        store.insert_proposal_once(
            proposal_id=proposal.proposal_id, objective_id=proposal.objective_id,
            objective_digest=proposal.objective_digest,
            objective_revision=proposal.objective_revision,
            created_at=proposal.created_at, valid_until=proposal.valid_until,
            payload=proposal.to_payload())
    except DuplicateProposal as exc:
        # The identity already exists, so this is the SAME proposal seen twice,
        # not a second admissible one. Surfacing it as an error keeps an event
        # storm from producing two candidates for one deviation.
        raise AlreadyProposed(f"already proposed: {proposal.proposal_id}") from exc
    return proposal


def _freshness_deadline(spec: Any, observations: list[dict],
                        projection: ProposalProjection) -> float:
    """When the observations behind this proposal stop being fresh.

    Kept separate from `valid_until` (which also folds in objective expiry) so
    admission can refuse stale evidence with its own reason code.
    """
    data = spec.to_dict()
    sources = {s["source_ref"]: s for s in data["sources"]}
    used = {p["source_ref"] for p in data["predicates"]}
    deadlines = []
    for obs in observations:
        if type(obs) is not dict or obs.get("source_ref") not in used:
            continue
        observed_at = obs.get("observed_at")
        if type(observed_at) is bool or type(observed_at) not in (int, float):
            continue
        deadlines.append(float(observed_at) + sources[obs["source_ref"]]["max_age_seconds"])
    return min(deadlines) if deadlines else projection.valid_until


# ------------------------------------------------------------------- kernel


class AdmissionKernel:
    """Sequences the current stores into one atomic admit/refuse decision."""

    def __init__(self, policy: PolicyPort, treasury: TreasuryPort,
                 conflicts: ConflictPort) -> None:
        self.policy = policy
        self.treasury = treasury
        self.conflicts = conflicts

    def settle(self, store: ObjectiveStore, reservation_id: str, disposition: str,
               *, objective_id: str | None = None) -> Any:
        """Закрыть допуск: снять бронь И ОТПУСТИТЬ ключи конфликта.

        `admit` возвращается на успешном пути, ДЕРЖА ключи, и это правильно:
        миссия ещё идёт, и никто другой не должен трогать ту же область. Но
        отпускать их было некому — единственный вызов `conflicts.release` стоит
        на пути отказа (ниже, в компенсации). Измерено: после того как миссия
        obj-a завершилась COMMITTED, реестр по-прежнему держит `repo:main` за
        obj-a, releases==0, и obj-b через 5000 секунд с совершенно свежим
        предложением получает `conflict_held` — навсегда. Никакая очерёдность
        это не лечит: `admit` отказывает состарившемуся проигравшему независимо
        от его ранга. Допуск обязан иметь конец, и вот он.

        Ключи берутся из брони, а не из текущей спецификации: ревизия могла
        поменять `conflict_keys` уже после допуска, и отпустить надо ровно то,
        что было захвачено.
        """
        reservation = store.reservation(reservation_id)
        payload = (reservation or {}).get("payload") or {}
        keys = tuple(payload.get("conflict_keys") or ())
        owner = objective_id or (reservation or {}).get("objective_id")
        settled = store.settle_reservation(reservation_id, disposition)
        if keys and owner:
            self.conflicts.release(keys, owner)
        return settled

    def admit(self, store: ObjectiveStore, proposal: AdmissionProposal, *,
              now: float) -> AdmissionDecision:
        if type(proposal) is not AdmissionProposal:
            raise AdmissionError("recorded AdmissionProposal required")
        if (not _valid_time(now) or not _valid_time(proposal.created_at)
                or now < proposal.created_at):
            return self._refuse(proposal, now, INVALID_CLOCK)
        try:
            state = store.get(proposal.objective_id)
        except ObjectiveStoreError:
            # 1. The objective may have been removed or never existed.
            return self._refuse(proposal, now, UNKNOWN_OBJECTIVE)
        base = {
            "objective_id": state.objective_id,
            "objective_digest": state.spec_digest,
            "objective_revision": state.revision,
            "proposal_id": proposal.proposal_id,
        }

        # 2/3. Lifecycle and stop state are re-read, never the values the
        # proposal was born under. Expiry is folded in here: an objective past
        # its horizon is effectively EXPIRED even if the row still says ACTIVE.
        if state.lifecycle != "ACTIVE":
            return self._refuse(proposal, now, LIFECYCLE_NOT_ACTIVE, detail=state.lifecycle, **base)
        if state.stopped:
            return self._refuse(proposal, now, OBJECTIVE_STOPPED, **base)

        # 4. A revision bump is the more specific fact than the digest change it
        # implies, so it is reported first: APPROVAL != POST_STATE. Both are
        # decided from the durable row alone, before the spec body is needed.
        if state.revision != proposal.objective_revision:
            return self._refuse(proposal, now, STALE_REVISION, **base)
        if state.spec_digest != proposal.objective_digest:
            return self._refuse(proposal, now, STALE_SPEC_DIGEST, **base)

        # The stored spec carries the rules for the remaining checks. If it will
        # not revalidate we refuse rather than guess at permissions or keys.
        try:
            spec = store.get_spec(proposal.objective_id).to_dict()
        except ObjectiveValidationError as exc:
            return self._refuse(proposal, now, SPEC_UNREADABLE, detail=str(exc), **base)
        # Expiry is effectively a lifecycle fact: an objective past its horizon
        # is EXPIRED however recently the row said ACTIVE.
        if now >= spec["expires_at"]:
            return self._refuse(proposal, now, OBJECTIVE_EXPIRED, **base)

        # Cost/binding validation precedes using caller-controlled horizons.
        estimate = proposal.cost_estimate
        if type(estimate) is not CostEstimate or not estimate.is_known():
            return self._refuse(proposal, now, COST_ESTIMATE_UNKNOWN, **base)
        if not _recorded_matches(store, proposal):
            return self._refuse(proposal, now, PROPOSAL_MISMATCH, **base)

        # 5/6. Proposal horizon, then the freshness of the evidence under it.
        if now >= proposal.valid_until:
            return self._refuse(proposal, now, PROPOSAL_EXPIRED, **base)
        if now >= proposal.freshness_deadline:
            return self._refuse(proposal, now, OBSERVATION_STALE, **base)

        # 7. Cooldown. A proposal's own recorded timestamp is not a cooldown
        # against itself; only a NEWER proposal inside the window is.
        last = state.last_proposal_at
        if (last is not None and last != proposal.created_at
                and now - last < spec["cooldown_seconds"]):
            return self._refuse(proposal, now, COOLDOWN_ACTIVE, **base)

        # Detach nested effect/verifier mappings from callers and port callbacks.
        original = proposal
        content_digest = proposal_digest(proposal)
        proposal = replace(proposal, expected_effects=tuple(
            json.loads(json.dumps(list(proposal.expected_effects), allow_nan=False))))
        keys, scopes = tuple(spec["conflict_keys"]), treasury_scopes(state)
        reservation_id = _digest({"proposal_id": proposal.proposal_id,
                                  "objective_digest": proposal.objective_digest,
                                  "objective_revision": proposal.objective_revision})
        decision = AdmissionDecision(
            True, ADMITTED, reservation_id=reservation_id,
            mission_intent_id=_intent_id(reservation_id, proposal),
            granted_capabilities=proposal.requested_capabilities,
            authorized_scope_refs=authorized_scope_refs(state), treasury_scopes=scopes,
            decided_at=now, proposal_digest=content_digest,
            owner_id=state.owner_id, scope_id=state.scope_id, **base)
        payload = {"estimate": estimate.to_dict(), "scopes": list(scopes),
                   "conflict_keys": list(keys),
                   "phase": "PENDING", "binding": _binding(decision)}
        # Win the durable slot FIRST. A duplicate never calls or releases ports
        # belonging to the winning admission, even across separate processes.
        try:
            store.claim_admission(reservation_id=reservation_id, objective_id=state.objective_id,
                                  proposal_id=proposal.proposal_id, created_at=now,
                                  expected_version=state.version,
                                  proposal_payload=proposal.to_payload(), payload=payload)
        except DuplicateReservation:
            return self._refuse(proposal, now, DUPLICATE_RESERVATION, **base)
        except ObjectiveStoreError:
            return self._refuse(proposal, now, ADMISSION_STATE_CHANGED, **base)

        claimed = reserved = False
        uncertain = False
        reason, detail, phase = POLICY_DENIED, "", "claim"
        try:
            claimed, detail = self.conflicts.claim(keys, state.objective_id, int(spec["priority"]))
            if claimed is not True:
                reason = CONFLICT_HELD
            else:
                phase = "policy"
                allowed, detail = self.policy.check_grants(
                    state.owner_id, state.scope_id, tuple(spec["permission_refs"]),
                    proposal.requested_capabilities)
                if allowed is not True:
                    reason = detail if detail in _POLICY_CODES else POLICY_DENIED
                else:
                    phase = "reserve"
                    reserved, treasury_ref, detail = self.treasury.reserve(scopes, estimate)
                    if reserved is not True:
                        reason = BUDGET_DENIED
                    else:
                        phase = "complete"
                        if (proposal_digest(original) != content_digest
                                or not _recorded_matches(store, proposal)):
                            reason = PROPOSAL_MISMATCH
                        else:
                            store.complete_admission(
                                reservation_id, expected_version=state.version,
                                payload={**payload, "phase": "READY", "treasury_ref": treasury_ref})
                            return decision
        except Exception:
            # A port may have taken a hold then failed to acknowledge it. Do not
            # invent a refund or retry: retain a PENDING recovery record.
            uncertain = phase in {"claim", "reserve"}
            reason = ADMISSION_RECOVERY_REQUIRED if uncertain else ADMISSION_STATE_CHANGED
            detail = "admission dependency failed"

        for held, release, args in (
            (reserved, self.treasury.release, (scopes, estimate)),
            (claimed, self.conflicts.release, (keys, state.objective_id)),
        ):
            if held:
                try:
                    release(*args)
                except Exception:
                    uncertain = True
        if not uncertain:
            try:
                store.settle_reservation(reservation_id, "RELEASED")
            except ObjectiveStoreError:
                uncertain = True
        if uncertain:
            reason = ADMISSION_RECOVERY_REQUIRED
        return self._refuse(proposal, now, reason, detail=detail, **base)

    @staticmethod
    def _refuse(proposal: AdmissionProposal, now: float, reason: str, *,
                detail: str = "", **base: Any) -> AdmissionDecision:
        base.setdefault("objective_id", proposal.objective_id)
        base.setdefault("proposal_id", proposal.proposal_id)
        return AdmissionDecision(False, reason, decided_at=now if _valid_time(now) else 0.0,
                                 detail=detail, **base)


def reauthorize_at_effect_boundary(store: ObjectiveStore, decision: AdmissionDecision,
                                   proposal: AdmissionProposal, *, now: float,
                                   policy: PolicyPort | None = None) -> bool:
    """Fail-closed effect guard using durable binding and CURRENT grants.

    Missing policy is not an implicit allow. This guard does not execute tools;
    dispatch still needs the canonical executor's fencing/authorization guard.
    """
    if (not _valid_time(now) or not decision_matches_proposal(decision, proposal)
            or now < decision.decided_at or policy is None):
        return False
    try:
        state = store.get(proposal.objective_id)
        spec = store.get_spec(proposal.objective_id).to_dict()
        if (state.lifecycle != "ACTIVE" or state.stopped or now >= spec["expires_at"]
                or state.revision != decision.objective_revision
                or state.spec_digest != decision.objective_digest
                or state.owner_id != decision.owner_id or state.scope_id != decision.scope_id
                or now >= proposal.valid_until or now >= proposal.freshness_deadline
                or not _recorded_matches(store, proposal)):
            return False
        records = store.open_reservations(proposal.objective_id)
        reservation = next((r for r in records if r["reservation_id"] == decision.reservation_id), None)
        if (reservation is None or reservation["proposal_id"] != proposal.proposal_id
                or reservation["payload"].get("phase") != "READY"
                or reservation["payload"].get("binding") != _binding(decision)):
            return False
        allowed, _ = policy.check_grants(
            state.owner_id, state.scope_id, tuple(spec["permission_refs"]),
            decision.granted_capabilities)
        # A callback may race lifecycle changes or mutate a nested proposal.
        current = store.get(proposal.objective_id)
        return (allowed is True and current == state
                and decision_matches_proposal(decision, proposal)
                and reservation in store.open_reservations(proposal.objective_id))
    except Exception:
        # Missing/corrupt storage or failed policy is no authority to dispatch.
        return False
