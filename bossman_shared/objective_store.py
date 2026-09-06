"""Durable V5 objective runtime state: persistence, lifecycle and CAS.

This is the N1 integration layer beneath `bossman_shared.objective_spec`. The
spec module is a pure projection with no authority; this module is the durable
record the service resolves authority *from*. It stores exactly what admission
must re-read at every boundary: current lifecycle, current spec digest and
revision, cumulative usage, stop state, last observation/proposal and the last
verified evidence reference.

Deliberate non-goals. No policy engine, no Treasury ledger, no mission
dispatcher, no evidence signer and no model call live here. Those are canonical
Bossman services; this store holds objective bookkeeping and hands admission the
compare-and-swap and once-only primitives it needs to be atomic.

Invariants carried by the schema rather than by caller discipline:

* Import is DRAFT. `create` refuses any other lifecycle.
* Revocation is sticky and expiry never resurrects: transitions go through
  `bossman_shared.objective_spec.transition_lifecycle`.
* A revision may not change owner or scope identity, and cumulative usage is
  never reset by a revision: standing work would otherwise buy a fresh budget
  by editing the spec.
* Condition (SATISFIED/DEVIATED/UNKNOWN) is separate from lifecycle, and
  SATISFIED is only writable together with a fresh verified evidence reference.
  Bookkeeping rows are not world-state proof.
* Proposal insertion and reservation are once-only at the database, not in
  Python: a crash between check and write cannot admit the same proposal twice.

`path=":memory:"` is refused. Each SQLite in-memory connection is its own
database, so an in-memory store cannot demonstrate the restart durability this
layer exists to provide.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import math
from pathlib import Path
import sqlite3

from bossman_shared.sqlite_connection import OwnedConnection
from typing import Any, Mapping

from .objective_spec import (
    LIFECYCLES,
    ObjectiveSpec,
    ObjectiveValidationError,
    transition_lifecycle,
)

SCHEMA_VERSION = 1
CONDITIONS = frozenset({"SATISFIED", "DEVIATED", "UNKNOWN"})

SCHEMA = """
CREATE TABLE IF NOT EXISTS v5_schema (
  key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS v5_objectives (
  objective_id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  scope_id TEXT NOT NULL,
  spec_json TEXT NOT NULL,
  spec_digest TEXT NOT NULL,
  revision INTEGER NOT NULL,
  lifecycle TEXT NOT NULL,
  condition TEXT NOT NULL,
  enrolled_sources TEXT NOT NULL DEFAULT '[]',
  observations_used INTEGER NOT NULL DEFAULT 0,
  missions_used INTEGER NOT NULL DEFAULT 0,
  wall_seconds_used REAL NOT NULL DEFAULT 0.0,
  cost_usd_used REAL NOT NULL DEFAULT 0.0,
  last_observation_at REAL,
  last_proposal_at REAL,
  last_verified_evidence_ref TEXT,
  stopped INTEGER NOT NULL DEFAULT 0,
  version INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS v5_proposals (
  proposal_id TEXT PRIMARY KEY,
  objective_id TEXT NOT NULL,
  objective_digest TEXT NOT NULL,
  objective_revision INTEGER NOT NULL,
  created_at REAL NOT NULL,
  valid_until REAL NOT NULL,
  payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS v5_proposals_objective ON v5_proposals(objective_id);
CREATE TABLE IF NOT EXISTS v5_reservations (
  reservation_id TEXT PRIMARY KEY,
  objective_id TEXT NOT NULL,
  proposal_id TEXT NOT NULL,
  created_at REAL NOT NULL,
  state TEXT NOT NULL,
  payload TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS v5_reservation_per_proposal ON v5_reservations(proposal_id);
CREATE TABLE IF NOT EXISTS v5_spec_history (
  objective_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  spec_digest TEXT NOT NULL,
  spec_json TEXT NOT NULL,
  superseded_at REAL,
  PRIMARY KEY (objective_id, revision));
CREATE TABLE IF NOT EXISTS v5_journal (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  objective_id TEXT NOT NULL,
  at REAL NOT NULL,
  event TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS v5_journal_objective ON v5_journal(objective_id);
"""


class ObjectiveStoreError(RuntimeError):
    """The durable record refused the write; the caller has no authority."""


class CompareAndSwapError(ObjectiveStoreError):
    """The caller's snapshot is stale; re-read and re-decide, never overwrite."""


class DuplicateProposal(ObjectiveStoreError):
    """This exact proposal identity was already recorded."""


class DuplicateReservation(ObjectiveStoreError):
    """This proposal already holds a reservation; a second one would double-spend."""


@dataclass(frozen=True, slots=True)
class ObjectiveRuntimeState:
    """Durable projection of one objective. Never an authorization."""

    objective_id: str
    owner_id: str
    scope_id: str
    spec_digest: str
    revision: int
    lifecycle: str
    condition: str
    enrolled_sources: tuple[str, ...] = ()
    observations_used: int = 0
    missions_used: int = 0
    wall_seconds_used: float = 0.0
    cost_usd_used: float = 0.0
    last_observation_at: float | None = None
    last_proposal_at: float | None = None
    last_verified_evidence_ref: str | None = None
    stopped: bool = False
    version: int = 0

    def proposal_snapshot(self) -> dict[str, Any]:
        """The snapshot shape `objective_spec.project_proposal` requires.

        Built from the durable row, never from proposal-supplied metadata: the
        projection must be fed canonical state or its dedup material is a lie.
        """
        return {
            "owner_id": self.owner_id,
            "scope_id": self.scope_id,
            "objective_digest": self.spec_digest,
            "lifecycle": self.lifecycle,
            "enrolled_sources": list(self.enrolled_sources),
            "observations_used": self.observations_used,
            "missions_used": self.missions_used,
            "wall_seconds_used": self.wall_seconds_used,
            "cost_usd_used": self.cost_usd_used,
            "last_proposal_at": self.last_proposal_at,
            "stopped": self.stopped,
        }


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _trusted_spec(spec_json: str, spec_digest: str) -> ObjectiveSpec:
    """Rehydrate a spec this store validated on write, via the contract itself.

    The chain rule is not re-run: predecessors are deliberately not retained, so
    re-running it on read would make every revised objective unreadable. The
    recorded digest stands in for it, and the contract module owns that rule
    rather than this one reaching past its constructor.
    """
    try:
        return ObjectiveSpec.from_trusted_json(spec_json, digest=spec_digest)
    except ObjectiveValidationError as exc:
        raise ObjectiveStoreError(f"stored objective is not readable: {exc}") from exc


def _row_state(row: sqlite3.Row) -> ObjectiveRuntimeState:
    return ObjectiveRuntimeState(
        objective_id=row["objective_id"],
        owner_id=row["owner_id"],
        scope_id=row["scope_id"],
        spec_digest=row["spec_digest"],
        revision=row["revision"],
        lifecycle=row["lifecycle"],
        condition=row["condition"],
        enrolled_sources=tuple(json.loads(row["enrolled_sources"])),
        observations_used=row["observations_used"],
        missions_used=row["missions_used"],
        wall_seconds_used=row["wall_seconds_used"],
        cost_usd_used=row["cost_usd_used"],
        last_observation_at=row["last_observation_at"],
        last_proposal_at=row["last_proposal_at"],
        last_verified_evidence_ref=row["last_verified_evidence_ref"],
        stopped=bool(row["stopped"]),
        version=row["version"],
    )


class ObjectiveStore:
    """One durable database of standing objectives for a Bossman installation."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError(
                "use a file path: sqlite :memory: is per-connection and cannot survive restart")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(SCHEMA)
            row = con.execute("SELECT value FROM v5_schema WHERE key='version'").fetchone()
            if row is None:
                con.execute("INSERT INTO v5_schema(key,value) VALUES('version',?)",
                            (str(SCHEMA_VERSION),))
            elif int(row["value"]) > SCHEMA_VERSION:
                raise ObjectiveStoreError(
                    f"objective store written by a newer schema ({row['value']}); "
                    "roll the runtime forward rather than downgrading the record")

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30, isolation_level="IMMEDIATE", factory=OwnedConnection)
        try:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA foreign_keys=ON")
        except BaseException:
            con.close()
            raise
        return con

    # ------------------------------------------------------------ objectives

    def create(self, spec: ObjectiveSpec, *, lifecycle: str = "DRAFT") -> ObjectiveRuntimeState:
        """Import one objective. Import is DRAFT; activation is a separate act.

        Enrollment starts empty: an imported objective observes nothing until an
        owner explicitly enrolls each source. No implicit account or directory
        enrollment, ever.
        """
        if type(spec) is not ObjectiveSpec:
            raise ObjectiveStoreError("validated ObjectiveSpec required")
        if lifecycle != "DRAFT":
            raise ObjectiveStoreError("import is DRAFT; activation is a separate owner action")
        data = spec.to_dict()
        state = ObjectiveRuntimeState(
            objective_id=data["objective_id"], owner_id=data["owner_id"],
            scope_id=data["scope_id"], spec_digest=spec.digest, revision=data["revision"],
            lifecycle="DRAFT", condition="UNKNOWN", version=1)
        try:
            with self._connect() as con:
                con.execute(
                    "INSERT INTO v5_objectives(objective_id,owner_id,scope_id,spec_json,spec_digest,"
                    "revision,lifecycle,condition,enrolled_sources,version) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (state.objective_id, state.owner_id, state.scope_id, spec.to_json(),
                     state.spec_digest, state.revision, "DRAFT", "UNKNOWN", "[]", 1))
                self._log(con, state.objective_id, "imported", spec.digest)
        except sqlite3.IntegrityError as exc:
            raise ObjectiveStoreError(f"objective {state.objective_id} already exists") from exc
        return state

    def get(self, objective_id: str) -> ObjectiveRuntimeState:
        with self._connect() as con:
            row = con.execute("SELECT * FROM v5_objectives WHERE objective_id=?",
                              (objective_id,)).fetchone()
        if row is None:
            raise ObjectiveStoreError(f"unknown objective {objective_id}")
        return _row_state(row)

    def get_spec(self, objective_id: str) -> ObjectiveSpec:
        """Return the trusted stored spec, revalidated on the way out."""
        with self._connect() as con:
            row = con.execute("SELECT spec_json,spec_digest FROM v5_objectives WHERE objective_id=?",
                              (objective_id,)).fetchone()
        if row is None:
            raise ObjectiveStoreError(f"unknown objective {objective_id}")
        return _trusted_spec(row["spec_json"], row["spec_digest"])

    def list_objectives(self, *, owner_id: str | None = None,
                        lifecycle: str | None = None) -> list[ObjectiveRuntimeState]:
        sql = "SELECT * FROM v5_objectives"
        clauses, args = [], []
        if owner_id is not None:
            clauses.append("owner_id=?")
            args.append(owner_id)
        if lifecycle is not None:
            if lifecycle not in LIFECYCLES:
                raise ObjectiveStoreError("unsupported lifecycle")
            clauses.append("lifecycle=?")
            args.append(lifecycle)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        with self._connect() as con:
            rows = con.execute(sql + " ORDER BY objective_id", tuple(args)).fetchall()
        return [_row_state(r) for r in rows]

    # ------------------------------------------------------------- lifecycle

    def transition(self, objective_id: str, requested: str, *, now: float,
                   owner_id: str, expected_version: int) -> ObjectiveRuntimeState:
        """Move lifecycle under CAS, validated by the pure transition rules.

        `owner_id` must be the *authenticated* caller resolved upstream; passing
        an identifier here does not authenticate anyone. Leaving ACTIVE always
        clears the condition to UNKNOWN: a stored SATISFIED from before a pause
        is not evidence about the world after it.
        """
        with self._connect() as con:
            row = con.execute("SELECT * FROM v5_objectives WHERE objective_id=?",
                              (objective_id,)).fetchone()
            if row is None:
                raise ObjectiveStoreError(f"unknown objective {objective_id}")
            state = _row_state(row)
            if state.version != expected_version:
                raise CompareAndSwapError("stale objective state; re-read before deciding")
            spec = _trusted_spec(row["spec_json"], row["spec_digest"])
            resolved = transition_lifecycle(spec, state.lifecycle, requested, now=now,
                                            owner_id=owner_id, scope_id=state.scope_id)
            condition = state.condition if resolved == "ACTIVE" else "UNKNOWN"
            evidence = state.last_verified_evidence_ref if resolved == "ACTIVE" else None
            self._swapped(con.execute(
                "UPDATE v5_objectives SET lifecycle=?,condition=?,last_verified_evidence_ref=?,"
                "version=version+1 WHERE objective_id=? AND version=?",
                (resolved, condition, evidence, objective_id, expected_version)))
            self._log(con, objective_id, "lifecycle", f"{state.lifecycle}->{resolved}")
        return replace(state, lifecycle=resolved, condition=condition,
                       last_verified_evidence_ref=evidence, version=expected_version + 1)

    def enroll_sources(self, objective_id: str, sources: tuple[str, ...], *,
                       owner_id: str, expected_version: int) -> ObjectiveRuntimeState:
        """Record the owner's explicit source enrollment.

        Only source refs the spec itself declares may be enrolled. Enrollment is
        a replace, so an owner can withdraw a source; an unenrolled source can
        never contribute to the condition.
        """
        if type(sources) is not tuple or any(type(s) is not str for s in sources):
            raise ObjectiveStoreError("explicit enrollment tuple of source refs required")
        with self._connect() as con:
            row = con.execute("SELECT * FROM v5_objectives WHERE objective_id=?",
                              (objective_id,)).fetchone()
            if row is None:
                raise ObjectiveStoreError(f"unknown objective {objective_id}")
            state = _row_state(row)
            if state.version != expected_version:
                raise CompareAndSwapError("stale objective state; re-read before deciding")
            if owner_id != state.owner_id:
                raise ObjectiveStoreError("enrollment identity mismatch")
            declared = {s["source_ref"] for s in json.loads(row["spec_json"])["sources"]}
            unknown = sorted(set(sources) - declared)
            if unknown:
                raise ObjectiveStoreError(f"source not declared by objective: {', '.join(unknown)}")
            ordered = tuple(sorted(set(sources)))
            self._swapped(con.execute(
                "UPDATE v5_objectives SET enrolled_sources=?,version=version+1 "
                "WHERE objective_id=? AND version=?",
                (_dumps(list(ordered)), objective_id, expected_version)))
            self._log(con, objective_id, "enrollment", ",".join(ordered))
        return replace(state, enrolled_sources=ordered, version=expected_version + 1)

    def spec_history(self, objective_id: str) -> list[dict[str, Any]]:
        """Вытесненные редакции цели, от новой к старой.

        Пусто не означает «ревизий не было»: цель, прожившая апгрейд до этой
        таблицы, свои прежние тела не сохранила. Журнал (`journal`) в таком
        случае всё равно помнит сам факт ревизии, и вкладка обязана показывать
        именно это, а не выдавать отсутствие истории за отсутствие изменений.
        """
        with self._connect() as con:
            rows = con.execute(
                "SELECT revision,spec_digest,spec_json,superseded_at FROM v5_spec_history "
                "WHERE objective_id=? ORDER BY revision DESC", (objective_id,)).fetchall()
        return [{"revision": r["revision"], "spec_digest": r["spec_digest"],
                 "superseded_at": r["superseded_at"], "spec": json.loads(r["spec_json"])}
                for r in rows]

    def revise(self, objective_id: str, spec: ObjectiveSpec, *, owner_id: str,
               expected_version: int, now: float | None = None) -> ObjectiveRuntimeState:
        """Bind a new revision to the trusted stored predecessor.

        Cumulative usage is carried forward untouched, and the condition drops
        to UNKNOWN: observations that matched the old digest say nothing about
        the new one. Enrollment is narrowed to sources the new spec still
        declares rather than silently re-enrolling anything.
        """
        if type(spec) is not ObjectiveSpec:
            raise ObjectiveStoreError("validated ObjectiveSpec required")
        with self._connect() as con:
            row = con.execute("SELECT * FROM v5_objectives WHERE objective_id=?",
                              (objective_id,)).fetchone()
            if row is None:
                raise ObjectiveStoreError(f"unknown objective {objective_id}")
            state = _row_state(row)
            if state.version != expected_version:
                raise CompareAndSwapError("stale objective state; re-read before deciding")
            if state.lifecycle == "REVOKED":
                raise ObjectiveStoreError("revocation is sticky; a revision cannot resurrect")
            if owner_id != state.owner_id:
                raise ObjectiveStoreError("revision identity mismatch")
            previous = _trusted_spec(row["spec_json"], row["spec_digest"])
            try:
                bound = ObjectiveSpec.from_dict(spec.to_dict(), previous=previous)
            except ObjectiveValidationError as exc:
                raise ObjectiveStoreError(f"revision rejected: {exc}") from exc
            data = bound.to_dict()
            if data["objective_id"] != state.objective_id:
                raise ObjectiveStoreError("revision changes objective identity")
            declared = {s["source_ref"] for s in data["sources"]}
            kept = tuple(s for s in state.enrolled_sources if s in declared)
            self._swapped(con.execute(
                "UPDATE v5_objectives SET spec_json=?,spec_digest=?,revision=?,condition='UNKNOWN',"
                "last_verified_evidence_ref=NULL,enrolled_sources=?,version=version+1 "
                "WHERE objective_id=? AND version=?",
                (bound.to_json(), bound.digest, data["revision"], _dumps(list(kept)),
                 objective_id, expected_version)))
            # Вкладка «Ревизии» обязана показывать, ЧТО изменилось, а не только
            # что что-то менялось. До этого `revise` затирал spec_json, и
            # предыдущая редакция исчезала: журнал помнил «1->2», но не тело.
            # Записывается ВЫТЕСНЯЕМАЯ редакция; текущая всегда в v5_objectives.
            con.execute(
                "INSERT OR IGNORE INTO v5_spec_history(objective_id,revision,spec_digest,"
                "spec_json,superseded_at) VALUES(?,?,?,?,?)",
                (objective_id, state.revision, state.spec_digest, row["spec_json"], now))
            self._log(con, objective_id, "revised", f"{state.revision}->{data['revision']}")
        return replace(state, spec_digest=bound.digest, revision=data["revision"],
                       condition="UNKNOWN", last_verified_evidence_ref=None,
                       enrolled_sources=kept, version=expected_version + 1)

    def set_stopped(self, objective_id: str, stopped: bool, *, reason: str,
                    expected_version: int) -> ObjectiveRuntimeState:
        """Resolve the owner's stop conditions in the canonical record.

        Stop state is a fact the service computes, not a model's reading of the
        objective text. Once set it blocks admission until explicitly cleared.
        """
        if type(stopped) is not bool:
            raise ObjectiveStoreError("stop state must be boolean")
        with self._connect() as con:
            state = self._cas_read(con, objective_id, expected_version)
            self._swapped(con.execute("UPDATE v5_objectives SET stopped=?,version=version+1 "
                                      "WHERE objective_id=? AND version=?",
                                      (1 if stopped else 0, objective_id, expected_version)))
            self._log(con, objective_id, "stop", f"{stopped}:{reason}")
        return replace(state, stopped=stopped, version=expected_version + 1)

    # ------------------------------------------------------- condition/usage

    def record_observation(self, objective_id: str, *, observed_at: float, count: int,
                           expected_version: int) -> ObjectiveRuntimeState:
        """Charge an observation batch against the cumulative quota."""
        if type(count) is not int or count < 0:
            raise ObjectiveStoreError("observation count must be a nonnegative integer")
        if (type(observed_at) not in (int, float) or not math.isfinite(observed_at)
                or observed_at < 0):
            raise ObjectiveStoreError("observation time must be a nonnegative finite number")
        with self._connect() as con:
            state = self._cas_read(con, objective_id, expected_version)
            self._swapped(con.execute(
                "UPDATE v5_objectives SET observations_used=observations_used+?,"
                "last_observation_at=?,version=version+1 WHERE objective_id=? AND version=?",
                (count, float(observed_at), objective_id, expected_version)))
        return replace(state, observations_used=state.observations_used + count,
                       last_observation_at=observed_at, version=expected_version + 1)

    def set_condition(self, objective_id: str, condition: str, *,
                      evidence_ref: str | None, expected_version: int) -> ObjectiveRuntimeState:
        """Write the objective's health, gated on evidence for SATISFIED.

        SATISFIED without a verified evidence reference is refused at the store,
        not merely discouraged upstream: green must never be settable by model
        prose, and a bookkeeping row is not proof about the world.
        """
        if condition not in CONDITIONS:
            raise ObjectiveStoreError("unsupported condition")
        if condition == "SATISFIED" and not (type(evidence_ref) is str and evidence_ref.strip()):
            raise ObjectiveStoreError("SATISFIED requires a verified evidence reference")
        with self._connect() as con:
            state = self._cas_read(con, objective_id, expected_version)
            self._swapped(con.execute(
                "UPDATE v5_objectives SET condition=?,last_verified_evidence_ref=?,"
                "version=version+1 WHERE objective_id=? AND version=?",
                (condition, evidence_ref, objective_id, expected_version)))
            self._log(con, objective_id, "condition", condition)
        return replace(state, condition=condition, last_verified_evidence_ref=evidence_ref,
                       version=expected_version + 1)

    def record_mission_usage(self, objective_id: str, *, missions: int, wall_seconds: float,
                             cost_usd: float, expected_version: int) -> ObjectiveRuntimeState:
        """Add settled mission usage. Usage only ever grows, across revisions."""
        for name, value in (("missions", missions), ("wall_seconds", wall_seconds),
                            ("cost_usd", cost_usd)):
            if (type(value) not in (int, float) or type(value) is bool
                    or not math.isfinite(value) or value < 0):
                raise ObjectiveStoreError(f"{name} must be a nonnegative finite number")
        with self._connect() as con:
            state = self._cas_read(con, objective_id, expected_version)
            self._swapped(con.execute(
                "UPDATE v5_objectives SET missions_used=missions_used+?,"
                "wall_seconds_used=wall_seconds_used+?,cost_usd_used=cost_usd_used+?,"
                "version=version+1 WHERE objective_id=? AND version=?",
                (int(missions), float(wall_seconds), float(cost_usd),
                 objective_id, expected_version)))
        return replace(state, missions_used=state.missions_used + int(missions),
                       wall_seconds_used=state.wall_seconds_used + float(wall_seconds),
                       cost_usd_used=state.cost_usd_used + float(cost_usd),
                       version=expected_version + 1)

    # --------------------------------------------------- proposals/reservations

    def insert_proposal_once(self, *, proposal_id: str, objective_id: str,
                             objective_digest: str, objective_revision: int,
                             created_at: float, valid_until: float,
                             payload: dict[str, Any]) -> None:
        """Record a proposal identity exactly once.

        Uniqueness is the primary key, so a duplicate event storm or a crash
        between check and write cannot produce two admissible proposals for the
        same objective revision and observation content.
        """
        try:
            with self._connect() as con:
                con.execute(
                    "INSERT INTO v5_proposals(proposal_id,objective_id,objective_digest,"
                    "objective_revision,created_at,valid_until,payload) VALUES(?,?,?,?,?,?,?)",
                    (proposal_id, objective_id, objective_digest, objective_revision,
                     created_at, valid_until, _dumps(payload)))
                con.execute("UPDATE v5_objectives SET last_proposal_at=? WHERE objective_id=?",
                            (created_at, objective_id))
                self._log(con, objective_id, "proposal", proposal_id)
        except sqlite3.IntegrityError as exc:
            raise DuplicateProposal(proposal_id) from exc

    def reserve_once(self, *, reservation_id: str, objective_id: str, proposal_id: str,
                     created_at: float, payload: dict[str, Any]) -> None:
        """Claim the single reservation slot for this proposal.

        Both the reservation id and the proposal it belongs to are unique, so a
        replayed admission cannot buy a second reservation under a fresh id.
        """
        try:
            with self._connect() as con:
                con.execute(
                    "INSERT INTO v5_reservations(reservation_id,objective_id,proposal_id,"
                    "created_at,state,payload) VALUES(?,?,?,?,'RESERVED',?)",
                    (reservation_id, objective_id, proposal_id, created_at, _dumps(payload)))
                self._log(con, objective_id, "reserved", reservation_id)
        except sqlite3.IntegrityError as exc:
            raise DuplicateReservation(reservation_id) from exc

    def get_proposal(self, proposal_id: str) -> dict[str, Any]:
        """Return detached durable proposal content, never caller-owned metadata."""
        with self._connect() as con:
            row = con.execute("SELECT * FROM v5_proposals WHERE proposal_id=?",
                              (proposal_id,)).fetchone()
        if row is None:
            raise ObjectiveStoreError("unknown proposal")
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def claim_admission(self, *, reservation_id: str, objective_id: str,
                        proposal_id: str, created_at: float, expected_version: int,
                        proposal_payload: dict[str, Any], payload: dict[str, Any]) -> None:
        """Durable once-only intent BEFORE any external port is called.

        Serializes competing callers through SQLite, not a process-local lock.
        An unresolved objective admission blocks another one, including after
        restart. This is intentionally conservative until reconciliation.
        """
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if con.execute("SELECT 1 FROM v5_reservations WHERE proposal_id=? OR reservation_id=?",
                           (proposal_id, reservation_id)).fetchone():
                raise DuplicateReservation(reservation_id)
            state = self._cas_read(con, objective_id, expected_version)
            if state.lifecycle != "ACTIVE" or state.stopped:
                raise ObjectiveStoreError("objective_not_active")
            row = con.execute("SELECT * FROM v5_proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
            if (row is None or row["objective_id"] != objective_id
                    or row["objective_revision"] != state.revision
                    or row["objective_digest"] != state.spec_digest
                    or row["payload"] != _dumps(proposal_payload)):
                raise ObjectiveStoreError("proposal_binding_mismatch")
            if con.execute("SELECT 1 FROM v5_reservations WHERE objective_id=? AND state='RESERVED'",
                           (objective_id,)).fetchone():
                raise ObjectiveStoreError("objective_has_unreconciled_admission")
            spec_row = con.execute("SELECT spec_json,spec_digest FROM v5_objectives WHERE objective_id=?",
                                   (objective_id,)).fetchone()
            spec = _trusted_spec(spec_row["spec_json"], spec_row["spec_digest"]).to_dict()
            limits, estimate = spec["limits"], payload["estimate"]
            if (created_at >= spec["expires_at"] or created_at >= row["valid_until"]
                    or state.missions_used + 1 > limits["max_missions"]
                    or state.cost_usd_used + estimate["cost_usd"] > limits["max_cost_usd"]
                    or state.wall_seconds_used + estimate["wall_seconds"] > limits["max_wall_seconds"]):
                raise ObjectiveStoreError("objective_limit_or_expiry")
            con.execute("INSERT INTO v5_reservations VALUES(?,?,?,?,?,?)",
                        (reservation_id, objective_id, proposal_id, created_at, "RESERVED", _dumps(payload)))
            self._log(con, objective_id, "admission_pending", reservation_id)

    def complete_admission(self, reservation_id: str, *, expected_version: int,
                           payload: dict[str, Any]) -> None:
        """Publish authority only after ports succeeded and lifecycle still matches."""
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM v5_reservations WHERE reservation_id=?",
                              (reservation_id,)).fetchone()
            if row is None or row["state"] != "RESERVED":
                raise ObjectiveStoreError("admission is not pending")
            prior = json.loads(row["payload"])
            state = self._cas_read(con, row["objective_id"], expected_version)
            if state.lifecycle != "ACTIVE" or state.stopped:
                raise ObjectiveStoreError("objective_not_active")
            if (prior.get("phase") != "PENDING" or payload.get("phase") != "READY"
                    or prior.get("binding") != payload.get("binding")
                    or prior.get("estimate") != payload.get("estimate")
                    or prior.get("scopes") != payload.get("scopes")):
                raise ObjectiveStoreError("admission_binding_changed")
            con.execute("UPDATE v5_reservations SET payload=? WHERE reservation_id=?",
                        (_dumps(payload), reservation_id))
            self._log(con, row["objective_id"], "admission_ready", reservation_id)

    @staticmethod
    def _settled_usage(payload: Mapping[str, Any] | None) -> tuple[float, float, bool]:
        """Расход, зафиксированный при бронировании. Возвращает (wall, cost, usable).

        Числа лежат ВНУТРИ `estimate` — ровно так их пишет admit. Плоское чтение
        всегда давало ноль, то есть «бесплатно и мгновенно» для реально
        потраченного бюджета.
        """
        estimate = (dict(payload or {}).get("estimate") or {})
        out: list[float] = []
        for key in ("wall_seconds", "cost_usd"):
            value = estimate.get(key)
            if (type(value) not in (int, float) or type(value) is bool
                    or not math.isfinite(value) or value < 0):
                return 0.0, 0.0, False
            out.append(float(value))
        return out[0], out[1], True

    def settle_reservation(self, reservation_id: str, state: str) -> ObjectiveRuntimeState:
        """Close a reservation as COMMITTED or RELEASED; settlement is final.

        Здесь же расходуется бюджет цели. Раньше `record_mission_usage` не звал
        никто, кроме восстановления после падения: `claim_admission` сверялся с
        `missions_used`/`cost_usd_used`, которые никогда не росли, и цель с
        лимитом в одну миссию допускала сколько угодно. Расход списывается в ТОЙ
        ЖЕ транзакции, что и закрытие брони: между «эффект зачтён» и «бюджет
        списан» не должно быть окна, которое переживает падение процесса.
        """
        if state not in {"COMMITTED", "RELEASED"}:
            raise ObjectiveStoreError("reservation settles as COMMITTED or RELEASED")
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT objective_id,state,payload FROM v5_reservations WHERE reservation_id=?",
                (reservation_id,)).fetchone()
            if row is None:
                raise ObjectiveStoreError(f"unknown reservation {reservation_id}")
            if row["state"] != "RESERVED":
                raise ObjectiveStoreError(
                    f"reservation {reservation_id} already settled as {row['state']}")
            con.execute("UPDATE v5_reservations SET state=? WHERE reservation_id=? AND state='RESERVED'",
                        (state, reservation_id))
            if state == "COMMITTED":
                try:
                    payload = json.loads(row["payload"])
                except (TypeError, ValueError):
                    payload = None
                wall, cost, usable = self._settled_usage(payload)
                con.execute(
                    "UPDATE v5_objectives SET missions_used=missions_used+1,"
                    "wall_seconds_used=wall_seconds_used+?,cost_usd_used=cost_usd_used+?,"
                    "version=version+1 WHERE objective_id=?",
                    (wall, cost, row["objective_id"]))
                if not usable:
                    # Счётчик миссий всё равно вырос: молча «не потратить» хуже,
                    # чем потратить неточно. Но след обязан остаться.
                    self._log(con, row["objective_id"], "usage_estimate_unusable",
                              f"{reservation_id}: reservation payload carries no usable estimate")
            self._log(con, row["objective_id"], "settled", f"{reservation_id}:{state}")
        return self.get(row["objective_id"])

    def note_release(self, reservation_id: str, status: str, detail: str = "") -> None:
        """Записать в бронь, чем кончилась попытка отпустить ключи конфликта.

        Между закрытием брони в БД и вызовом внешнего порта нет и не может быть
        общей транзакции. Делать вид, что она есть, — значит терять ключ при
        падении ровно в этом промежутке. Поэтому исход попытки хранится в самой
        (уже закрытой) брони: незавершённый release ВИДЕН и повторяем.

        Трогается только служебный ключ `release`; тело допуска не меняется.
        """
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT objective_id,payload FROM v5_reservations "
                              "WHERE reservation_id=?", (reservation_id,)).fetchone()
            if row is None:
                raise ObjectiveStoreError(f"unknown reservation {reservation_id}")
            try:
                payload = json.loads(row["payload"])
            except (TypeError, ValueError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            payload["release"] = {"status": status, "detail": str(detail)[:500]}
            con.execute("UPDATE v5_reservations SET payload=? WHERE reservation_id=?",
                        (_dumps(payload), reservation_id))
            self._log(con, row["objective_id"], "conflict_release", f"{reservation_id}:{status}")

    def pending_releases(self, objective_id: str | None = None) -> list[dict[str, Any]]:
        """Закрытые брони, чьи ключи конфликта, возможно, всё ещё держатся.

        Это рабочий список повторяемой уборки, а не отчёт: он должен пустеть.
        Броня без записанных `conflict_keys` (её завёл старый билд) сюда не
        попадает — отпускать по ней нечего, и это отдельно видно в `settle`.
        """
        sql = ("SELECT reservation_id,objective_id,state,payload FROM v5_reservations "
               "WHERE state<>'RESERVED'")
        args: tuple = ()
        if objective_id is not None:
            sql += " AND objective_id=?"
            args = (objective_id,)
        out = []
        with self._connect() as con:
            for row in con.execute(sql + " ORDER BY created_at", args).fetchall():
                try:
                    payload = json.loads(row["payload"])
                except (TypeError, ValueError):
                    continue
                if not isinstance(payload, dict):
                    continue
                keys = payload.get("conflict_keys") or []
                if not keys:
                    continue
                status = ((payload.get("release") or {}).get("status")
                          if isinstance(payload.get("release"), dict) else None)
                if status == "RELEASED":
                    continue
                out.append({"reservation_id": row["reservation_id"],
                            "objective_id": row["objective_id"], "state": row["state"],
                            "conflict_keys": list(keys), "release_status": status})
        return out

    def open_reservations(self, objective_id: str) -> list[dict[str, Any]]:
        """Reservations still in flight; a restart must resolve each explicitly.

        Recovery reads this rather than assuming: an in-flight reservation is
        ambiguity, and ambiguity never authorizes an irreversible replay.
        """
        with self._connect() as con:
            rows = con.execute(
                "SELECT reservation_id,proposal_id,created_at,payload FROM v5_reservations "
                "WHERE objective_id=? AND state='RESERVED' ORDER BY created_at",
                (objective_id,)).fetchall()
        return [{"reservation_id": r["reservation_id"], "proposal_id": r["proposal_id"],
                 "created_at": r["created_at"], "payload": json.loads(r["payload"])} for r in rows]

    def reservation(self, reservation_id: str) -> dict[str, Any] | None:
        """Одна бронь по идентификатору, в любом состоянии.

        `open_reservations` фильтрует по RESERVED, поэтому закрыть бронь и
        одновременно узнать, ЧТО она держала, через него нельзя. Закрытию
        допуска нужны ключи конфликта из полезной нагрузки.
        """
        with self._connect() as con:
            row = con.execute(
                "SELECT reservation_id,objective_id,proposal_id,created_at,state,payload "
                "FROM v5_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
        if row is None:
            return None
        return {"reservation_id": row["reservation_id"], "objective_id": row["objective_id"],
                "proposal_id": row["proposal_id"], "created_at": row["created_at"],
                "state": row["state"], "payload": json.loads(row["payload"])}

    # `v5_reservations` — НЕ журнал допусков: строка заводится до опроса портов,
    # поэтому отказ тоже оставляет запись. Отличает их единственный факт —
    # `payload.phase`, который в 'READY' переводит только `complete_admission`.
    # Оба чтения ниже фильтруют по нему, иначе честность расписания держалась бы
    # на отказах.
    _ADMITTED = "json_extract(payload,'$.phase')='READY'"

    def last_admission_at(self, objective_id: str) -> float | None:
        """Когда цель В ПОСЛЕДНИЙ РАЗ была допущена (не предложена).

        `last_proposal_at` — это последнее ПРЕДЛОЖЕНИЕ: оно пишется и тогда,
        когда допуска не было, поэтому голодающая цель выглядит через него
        свежеобслуженной. Справедливость обязана считать по обслуживанию.
        """
        with self._connect() as con:
            row = con.execute(
                f"SELECT MAX(created_at) AS at FROM v5_reservations "
                f"WHERE objective_id=? AND {self._ADMITTED}", (objective_id,)).fetchone()
        return None if row is None or row["at"] is None else float(row["at"])

    def admissions_since(self, objective_id: str, since: float) -> int:
        """Сколько раз цель была допущена начиная с `since` — окно квоты.

        Лимит `max_missions` в спецификации — пожизненный итог; он не мешает
        одной цели забрать все допуски одного часа. Окно — отдельный вопрос.
        """
        with self._connect() as con:
            row = con.execute(
                f"SELECT COUNT(*) AS n FROM v5_reservations "
                f"WHERE objective_id=? AND created_at>=? AND {self._ADMITTED}",
                (objective_id, float(since))).fetchone()
        return int(row["n"]) if row is not None else 0

    def journal(self, objective_id: str) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT at,event,detail FROM v5_journal WHERE objective_id=? ORDER BY seq",
                (objective_id,)).fetchall()
        return [{"at": r["at"], "event": r["event"], "detail": r["detail"]} for r in rows]

    # ---------------------------------------------------------------- helpers

    def _cas_read(self, con: sqlite3.Connection, objective_id: str,
                  expected_version: int) -> ObjectiveRuntimeState:
        row = con.execute("SELECT * FROM v5_objectives WHERE objective_id=?",
                          (objective_id,)).fetchone()
        if row is None:
            raise ObjectiveStoreError(f"unknown objective {objective_id}")
        state = _row_state(row)
        if state.version != expected_version:
            raise CompareAndSwapError("stale objective state; re-read before deciding")
        return state

    @staticmethod
    def _swapped(cursor: sqlite3.Cursor) -> None:
        """A compare-and-swap that matched no row lost its race; never report success.

        The snapshot is read outside the write transaction, so a competing writer
        can commit in between. The guarded UPDATE is what makes the swap atomic;
        without this check the loser would silently drop its own mutation.
        """
        if cursor.rowcount != 1:
            raise CompareAndSwapError("stale objective state; re-read before deciding")

    @staticmethod
    def _log(con: sqlite3.Connection, objective_id: str, event: str, detail: str = "") -> None:
        con.execute("INSERT INTO v5_journal(objective_id,at,event,detail) "
                    "VALUES(?,strftime('%s','now'),?,?)", (objective_id, event, detail))
