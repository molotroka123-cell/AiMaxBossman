"""Protected single-host adapter configuration. No model-facing registration API."""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

from .contracts import RealityCompiler, RealityError, digest
from .proof import ProofAuthority
from .runtime import RealityRuntime
from .store import RealityStore
from .intelligence import LearningLedger, compare_world


def persistent_authority(principals):
    # Domain-separated keys reuse the existing host evidence key infrastructure.
    from bossman_shared.evidence import load_or_create_key
    root = load_or_create_key()
    return ProofAuthority({v: hmac.new(root, ("reality/v1/" + v + "/" + p).encode(),
                                    hashlib.sha256).digest() for v, p in principals.items()}, principals)


class LocalHost:
    """Adapters/targets/principals/policy are trusted bootstrap objects.

    The host-approved IR must come from an owner contract plus canonical action
    metadata, NOT copied unvalidated from planner output. Observers must perform
    fresh target reads. No network, generic shell or automatic skill promotion
    is enabled by installing this class.
    """
    def __init__(self, path, *, policy, authority, observers, actions, fence_check, level_provider):
        self.path = Path(path)
        self.policy, self.authority = policy, authority
        self.observers, self.actions = dict(observers), dict(actions)
        self.fence_check, self.level_provider = fence_check, level_provider

    def call(self, operation, *, create=False):
        if not create and not self.path.is_file():
            raise RealityError("missing durable Reality store")
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        # Each short operation owns its connection on the calling thread.
        # No SQLite transaction or lock is held across action/network IO.
        store = RealityStore(self.path)
        try:
            store.db.execute("CREATE TABLE IF NOT EXISTS host_restrictions (id INTEGER PRIMARY KEY, level INTEGER NOT NULL)")
            runtime = RealityRuntime(store, self.policy, self.authority, observers=self.observers,
                                     actions=self.actions, fence_check=self.fence_check,
                                     level_provider=self.effective_level)
            return operation(runtime)
        finally:
            store.close()

    def validate(self, mission):
        self.policy.admit(mission, current_level=self.effective_level())
        if any(o.verifier not in self.observers for o in mission.obligations):
            raise RealityError("missing host observer")
        if any(e.action not in self.actions for e in mission.effects):
            raise RealityError("missing host action")
        for o in mission.obligations:
            if self.authority._principals.get(o.verifier) in (None, mission.executor):
                raise RealityError("independent effective principal required")

    def effective_level(self):
        import sqlite3
        ceiling = self.level_provider()
        if not self.path.exists():
            return ceiling
        with sqlite3.connect(self.path) as connection:
            table = connection.execute("SELECT name FROM sqlite_master WHERE name='host_restrictions'").fetchone()
            row = connection.execute("SELECT level FROM host_restrictions WHERE id=1").fetchone() if table else None
        connection.close()
        return min(ceiling, row[0]) if row else ceiling

    def observed(self, obligation, value):
        # Fixed, non-sensitive delta keys; no clinical content enters learning.
        delta = compare_world({"poststate": obligation.expected_digest}, {"poststate": digest(value)})
        if delta.divergent:
            self.call(lambda rt: rt.store.db.execute(
                "INSERT INTO host_restrictions VALUES(1,0) ON CONFLICT(id) DO UPDATE SET level=0"))
            raise RealityError("post-state divergence; autonomy restricted until host review")
        return value

    def register(self, mission):
        self.validate(mission)
        self.call(lambda rt: rt.admit(mission), create=True)

    def load(self, mission_id):
        def load(rt):
            row = rt.store.db.execute("SELECT payload FROM missions WHERE id=?", (mission_id,)).fetchone()
            if row is None:
                raise RealityError("missing Mission IR")
            return RealityCompiler().compile(json.loads(row[0]))
        return self.call(load)

    def route_allowed(self, route):
        ledger = LearningLedger(str(self.path) + ".learning")
        try:
            if ledger.reputation(route)["quarantined"]:
                raise RealityError("action/skill route quarantined")
        finally:
            ledger.close()

    def reconcile_written(self, mission_id, effect_id):
        """Host-only recovery: fresh observer, original owner/fence, no new IO."""
        from bossman_shared.reality_guard import Session
        mission = self.load(mission_id)
        self.validate(mission)
        def row(rt):
            return dict(rt.store.db.execute("SELECT * FROM effects WHERE mission=? AND id=?",
                                           (mission.id, effect_id)).fetchone())
        stored = self.call(row)
        if stored["owner"] != mission.executor or stored["state"] != "EFFECT_ESCROW":
            raise RealityError("recovery requires original escrow owner")
        Session(self, mission).confirm(mission.effect(effect_id), stored["fence"])
