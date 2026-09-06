"""Disabled, read-only placement preflight; never an execution authorization.

Reads the same durable node/lease tables as FleetDigitalTwin, in one SQLite
read transaction, without invoking watchdog, placement, approval or transport.
FleetScheduler remains the capability/privacy/resource policy authority.
Reservation-aware admission currently exists only inside LeaseManager.acquire;
any live lease therefore defers admission to that canonical atomic operation.
No speculative reservation subtraction or second admission engine lives here.
"""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .models import NodeState, PlacementRequirement
from .scheduler import FleetScheduler
from .store import FleetStore


@dataclass(frozen=True)
class PreflightSnapshot:
    observed_at: float
    nodes: tuple[tuple[str, str], ...] = ()
    leases: tuple[str, ...] = ()
    unavailable: bool = False


@dataclass(frozen=True)
class PreflightBlocker:
    category: str
    code: str
    knowledge: str = "KNOWN"


@dataclass(frozen=True)
class NodePreflight:
    node_id: str
    heartbeat_age_s: float | None
    blockers: tuple[PreflightBlocker, ...]

    @property
    def eligible(self) -> bool:
        return not self.blockers


@dataclass(frozen=True)
class PreflightReport:
    snapshot_age_s: float | None
    nodes: tuple[NodePreflight, ...]
    blockers: tuple[PreflightBlocker, ...] = ()

    @property
    def candidate_node_ids(self) -> tuple[str, ...]:
        return () if self.blockers else tuple(n.node_id for n in self.nodes if n.eligible)

    @property
    def status(self) -> str:
        if self.candidate_node_ids:
            return "CANDIDATES_AVAILABLE"
        all_blockers = self.blockers + tuple(b for n in self.nodes for b in n.blockers)
        return "UNKNOWN" if any(b.knowledge == "UNKNOWN" for b in all_blockers) else "BLOCKED"

    @property
    def dispatch_authorized(self) -> bool:
        return False


_REQUIRED = frozenset(NodeState("schema").to_dict())
_COLLECTIONS = ("capabilities", "pools", "models", "warm_models", "artifacts")
_LIMITATIONS = "Placement advice only: dispatch must revalidate policy, freshness, leases, budgets and effects."


def _number(value: object, *, positive: bool = False) -> bool:
    try:
        return (type(value) in (int, float) and math.isfinite(value)
                and (value > 0 if positive else value >= 0))
    except OverflowError:
        return False


def _unknown(category: str, code: str) -> PreflightBlocker:
    return PreflightBlocker(category, code, "UNKNOWN")


def _category(reason: str) -> str:
    if reason.startswith(("private_", "secrets_", "node_not_cleared", "unknown_privacy", "cloud_", "internal_task_")):
        return "privacy"
    if reason.startswith(("insufficient_", "at_max_concurrency", "overloaded")):
        return "resources"
    return "capability"


def _valid_requirement(req: PlacementRequirement) -> bool:
    if not isinstance(req, PlacementRequirement):
        return False
    if not all(_number(v) for v in (req.min_ram_gb, req.min_gpu_memory_gb, req.max_load)):
        return False
    if req.max_load > 1 or type(req.artifact_bytes) is not int or req.artifact_bytes < 0:
        return False
    if type(req.contains_secrets) is not bool or not isinstance(req.privacy, str):
        return False
    if not isinstance(req.prefer_node, str):
        return False
    return all(isinstance(getattr(req, k), tuple)
               and all(isinstance(v, str) and v for v in getattr(req, k))
               for k in ("capabilities", "pools", "required_models", "allowed_os", "artifacts", "anti_affinity_domains"))


def _node(raw: str, node_id: str) -> NodeState:
    d = json.loads(raw)
    if not isinstance(d, dict) or not _REQUIRED <= d.keys() or d["node_id"] != node_id or not node_id:
        raise ValueError("missing or inconsistent node data")
    for key in _COLLECTIONS:
        if not isinstance(d[key], list) or not all(isinstance(v, str) and v for v in d[key]):
            raise ValueError("invalid capability inventory")
    for key in ("ram_gb", "gpu_memory_gb", "ram_used_gb", "gpu_memory_used_gb", "load"):
        if not _number(d[key]):
            raise ValueError("invalid resource observation")
    if d["load"] > 1 or d["ram_used_gb"] > d["ram_gb"] or d["gpu_memory_used_gb"] > d["gpu_memory_gb"]:
        raise ValueError("inconsistent resource observation")
    if type(d["unified_memory"]) is not bool:
        raise ValueError("unknown memory topology")
    if any(type(d[k]) is not int or d[k] < 0 for k in ("max_concurrency", "active_work")):
        raise ValueError("invalid concurrency observation")
    if not all(isinstance(d[k], str) for k in ("os_name", "failure_domain", "hostname", "gpu_name")):
        raise ValueError("invalid node metadata")
    return NodeState.from_dict(d)


class FleetPreflight:
    """In-process trusted callers only; snapshots are observations, not proofs.

    No runtime registers this adapter. A caller must explicitly use it after
    integration gates. It does not attest disk space, app health, model footprint,
    credentials, budgets or external state absent from PlacementRequirement.
    Model placement without a declared memory demand remains unknown. Other
    zero demands retain the scheduler's meaning (no requested reservation).
    """

    limitations = _LIMITATIONS

    def __init__(self, store: FleetStore, *, scheduler: FleetScheduler | None = None,
                 max_age_s: float = 90.0) -> None:
        if not _number(max_age_s, positive=True):
            raise ValueError("max_age_s must be finite and positive")
        self.store = store
        self.scheduler = scheduler or FleetScheduler()
        self.max_age_s = max_age_s

    def capture(self, *, now: float) -> PreflightSnapshot:
        if not _number(now, positive=True):
            return PreflightSnapshot(now, unavailable=True)
        try:
            # mode=ro never creates a DB; query_only denies writes even if a
            # future reader accidentally attempts one. Close explicitly.
            uri = Path(self.store.path).resolve().as_uri() + "?mode=ro"
            con = sqlite3.connect(uri, uri=True, timeout=1, isolation_level=None)
            try:
                con.row_factory = sqlite3.Row
                con.execute("PRAGMA query_only=ON")
                con.execute("BEGIN")
                nodes = tuple((r["node_id"], r["payload"]) for r in con.execute(
                    "SELECT node_id,payload FROM fleet_nodes ORDER BY node_id"))
                leases = tuple(json.dumps(dict(r), allow_nan=False) for r in con.execute(
                    "SELECT l.*,r.host_gb,r.gpu_gb FROM fleet_leases l "
                    "LEFT JOIN fleet_memory_reservations r ON l.lease_id=r.lease_id ORDER BY l.lease_id"))
                return PreflightSnapshot(now, nodes, leases)
            finally:
                con.close()
        except (sqlite3.Error, OSError, ValueError, TypeError):
            return PreflightSnapshot(now, unavailable=True)

    def inspect(self, req: PlacementRequirement, *, now: float) -> PreflightReport:
        return self.evaluate(self.capture(now=now), req, now=now)

    def evaluate(self, snapshot: PreflightSnapshot, req: PlacementRequirement, *, now: float) -> PreflightReport:
        """Evaluate a trusted captured snapshot; fail closed on age or corruption."""
        if not _valid_requirement(req):
            return PreflightReport(None, (), (PreflightBlocker("request", "invalid_requirement"),))
        if not _number(now, positive=True) or not _number(snapshot.observed_at, positive=True):
            return PreflightReport(None, (), (_unknown("freshness", "invalid_snapshot_time"),))
        age = now - snapshot.observed_at
        if snapshot.unavailable or age < 0 or age > self.max_age_s:
            code = "snapshot_unavailable" if snapshot.unavailable else "snapshot_future" if age < 0 else "snapshot_stale"
            return PreflightReport(age, (), (_unknown("freshness", code),))
        if not snapshot.nodes:
            return PreflightReport(age, (), (_unknown("capability", "node_inventory_missing"),))
        # Reservations are inspected solely for known conflicts / missing data.
        # The existing atomic admission remains responsible for counting them.
        leased: set[str] = set()
        try:
            for encoded in snapshot.leases:
                lease = json.loads(encoded)
                if (not _number(lease["acquired_ts"], positive=True)
                        or not _number(lease["expires_ts"], positive=True)
                        or lease["acquired_ts"] > snapshot.observed_at
                        or lease["expires_ts"] <= lease["acquired_ts"]):
                    raise ValueError("invalid lease timestamp")
                if lease["expires_ts"] > now:
                    if not isinstance(lease["node_id"], str):
                        raise ValueError("invalid lease node")
                    leased.add(lease["node_id"])
        except (KeyError, TypeError, ValueError):
            return PreflightReport(age, (), (_unknown("resources", "lease_snapshot_invalid"),))
        out = []
        seen = set()
        for node_id, raw in snapshot.nodes:
            if node_id in seen:
                return PreflightReport(age, (), (_unknown("capability", "duplicate_node_identity"),))
            seen.add(node_id)
            blockers: list[PreflightBlocker] = []
            hb_age = None
            try:
                node = _node(raw, node_id)
                if (not _number(node.last_heartbeat_ts, positive=True)
                        or not _number(node.registered_ts, positive=True)
                        or node.registered_ts > node.last_heartbeat_ts
                        or node.last_heartbeat_ts > snapshot.observed_at):
                    blockers.append(_unknown("freshness", "node_time_invalid"))
                else:
                    hb_age = now - node.last_heartbeat_ts
                    if hb_age > self.max_age_s:
                        blockers.append(_unknown("freshness", "heartbeat_stale"))
                blockers.extend(PreflightBlocker(_category(r), r)
                                for r in self.scheduler.reject_reasons(node, req))
                if req.required_models and not (req.min_ram_gb or req.min_gpu_memory_gb):
                    blockers.append(_unknown("resources", "model_memory_demand_missing"))
                if node_id in leased:
                    blockers.append(_unknown("resources", "live_lease_requires_atomic_admission"))
            except (ValueError, TypeError, KeyError, AttributeError):
                blockers.append(_unknown("capability", "node_snapshot_invalid"))
            out.append(NodePreflight(node_id, hb_age, tuple(blockers)))
        return PreflightReport(age, tuple(out))
