from __future__ import annotations
from dataclasses import replace
import threading
from .models import ObjectiveRuntimeState, Proposal

class CompareAndSwapError(RuntimeError): pass
class DuplicateProposal(RuntimeError): pass

class InMemoryReferenceStore:
    """Reference transaction semantics only.

    Production integration MUST use Bossman's canonical durable store.
    """
    def __init__(self):
        self._lock = threading.RLock()
        self._objectives = {}
        self._proposals = {}
        self._reservations = set()

    def put_new_objective(self, state: ObjectiveRuntimeState):
        with self._lock:
            if state.objective_id in self._objectives:
                raise CompareAndSwapError("objective already exists")
            self._objectives[state.objective_id] = state

    def get_objective(self, objective_id: str):
        with self._lock:
            return self._objectives[objective_id]

    def cas_objective(self, objective_id: str, *, expected_version: int, replacement):
        with self._lock:
            current = self._objectives[objective_id]
            if current.version != expected_version:
                raise CompareAndSwapError("stale objective state")
            if replacement.objective_id != current.objective_id:
                raise CompareAndSwapError("identity mutation")
            committed = replace(replacement, version=current.version + 1)
            self._objectives[objective_id] = committed
            return committed

    def insert_proposal_once(self, proposal: Proposal):
        with self._lock:
            if proposal.proposal_id in self._proposals:
                raise DuplicateProposal(proposal.proposal_id)
            self._proposals[proposal.proposal_id] = proposal

    def reserve_once(self, reservation_id: str) -> bool:
        with self._lock:
            if reservation_id in self._reservations:
                return False
            self._reservations.add(reservation_id)
            return True
