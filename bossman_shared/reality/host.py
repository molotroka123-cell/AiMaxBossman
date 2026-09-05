"""Protected single-host adapter configuration. No model-facing registration API."""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

from .contracts import RealityCompiler, RealityError, digest
from .proof import ProofAuthority, Receipt
from .runtime import RealityRuntime
from .store import RealityStore
from .intelligence import Bid, LearningLedger, compare_world


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
    def __init__(self, path, *, policy, authority, observers, actions, fence_check, level_provider,
                 route_bids=None, learning_redactor=None):
        self.path = Path(path)
        self.policy, self.authority = policy, authority
        self.observers, self.actions = dict(observers), dict(actions)
        self.fence_check, self.level_provider = fence_check, level_provider
        # Optional, trusted bootstrap metadata only. No model probabilities,
        # paid routing or action substitution is admitted by this local host.
        self.route_bids = dict(route_bids or {})
        if any(type(bid) is not Bid or bid.route != route or not bid.local
               or bid.cost_microusd != 0 or route not in self.actions
               or route not in self.policy.allowed_actions
               for route, bid in self.route_bids.items()):
            raise RealityError("learning bids require a fixed allowed local zero-cost action")
        if self.route_bids and not callable(learning_redactor):
            raise RealityError("configured learning requires the host privacy redactor")
        self.learning_redactor = learning_redactor

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

    def observed(self, obligation, value, *, mission=None, effect=None, fence=None):
        # Fixed, non-sensitive delta keys; no clinical content enters learning.
        delta = compare_world({"poststate": obligation.expected_digest}, {"poststate": digest(value)})
        if delta.divergent:
            self.call(lambda rt: rt.store.db.execute(
                "INSERT INTO host_restrictions VALUES(1,0) ON CONFLICT(id) DO UPDATE SET level=0"))
            if mission is not None and effect is not None and effect.action in self.route_bids:
                ledger = LearningLedger(str(self.path) + ".learning")
                try:
                    self._lesson(ledger, mission, effect, fence, obligation.expected_digest,
                                 digest(value), "observed_poststate_diverged")
                finally:
                    ledger.close()
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
            if route in self.route_bids:
                # Selection cannot alter the compiled action, target or args.
                ledger.choose([self.route_bids[route]], budget_microusd=0, privacy="LOCAL")
        finally:
            ledger.close()

    def _lesson(self, ledger, mission, effect, fence, expected, observed, lesson):
        return ledger.record_lesson(digest([mission.fingerprint, effect.id, fence, "lesson", lesson]),
            context_digest=mission.fingerprint, action_digest=effect.args_digest,
            expected={"poststate": expected}, observed={"poststate": observed},
            cause_hypothesis=self.learning_redactor("cause_not_assessed"),
            lesson=self.learning_redactor(lesson))

    def record_confirmed(self, mission, effect, fence):
        """Host-only, retryable audit of a confirmed effect; never re-executes IO.

        Fresh validation of persisted proof precedes any success settlement.
        An audit failure leaves CONFIRMED intact, so dispatch cannot be replayed.
        The host may retry this method after repairing its learning store.
        """
        if effect.action not in self.route_bids:
            return
        if mission.effect(effect.id) != effect:
            raise RealityError("learning effect differs from immutable mission")
        self.validate(mission)
        def proof(rt):
            rt.store._bound(mission)
            row = rt.store.db.execute("SELECT * FROM effects WHERE mission=? AND id=?",
                                     (mission.id, effect.id)).fetchone()
            stored = rt.store.db.execute("SELECT payload FROM receipts WHERE mission=? AND obligation=?",
                                        (mission.id, effect.obligation_id)).fetchone()
            if row is None or stored is None or (row["state"], row["owner"], row["fence"]) != (
                    "CONFIRMED", mission.executor, fence):
                raise RealityError("learning requires independently confirmed effect")
            receipt = Receipt(**json.loads(stored[0]))
            rt.authority.check(mission, receipt)
            if (receipt.dispatch_binding != digest([mission.fingerprint, effect.id, fence])
                    or receipt.observed_at < row["dispatched_at"]):
                raise RealityError("learning receipt is not bound to this attempt")
            return receipt
        receipt = self.call(proof)
        ledger = LearningLedger(str(self.path) + ".learning")
        try:
            self._lesson(ledger, mission, effect, fence, receipt.expected_digest,
                         receipt.observed_digest, "independent_poststate_confirmed")
            self.authority.check(mission, receipt)
            ledger.settle(digest([mission.fingerprint, effect.id, fence]), self.route_bids[effect.action],
                          verified_success=True)
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
