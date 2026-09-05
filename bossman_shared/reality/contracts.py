"""Immutable Mission IR. Model proposals are data, never authority."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from enum import Enum
import hashlib
import json
import math


class RealityError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def text(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise RealityError(f'invalid {name}')


def integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise RealityError(f'invalid {name}')


def unit(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise RealityError('expected finite probability in [0,1]')
    return value


class Knowledge(str, Enum):
    ASSUMED = 'ASSUMED'
    INFERRED = 'INFERRED'
    REPORTED = 'REPORTED'
    OBSERVED = 'OBSERVED'
    VERIFIED = 'VERIFIED'
    ATTESTED = 'ATTESTED'


@dataclass(frozen=True)
class Obligation:
    id: str
    target: str
    expected_digest: str
    verifier: str
    max_age_seconds: int = 300

    def __post_init__(self):
        for name in ('id', 'target', 'verifier'):
            text(getattr(self, name), name)
        if len(self.expected_digest) != 64 or any(c not in '0123456789abcdef' for c in self.expected_digest):
            raise RealityError('expected_digest must be sha256')
        integer(self.max_age_seconds, 'max_age_seconds', 1)


@dataclass(frozen=True)
class Effect:
    id: str
    target: str
    action: str
    args_digest: str
    obligation_id: str
    idempotency_domain: str
    reconciliation: str
    uncertainty_seconds: int = 300
    irreversible: bool = False
    required_level: int = 1

    def __post_init__(self):
        for name in ('id', 'target', 'action', 'obligation_id', 'idempotency_domain', 'reconciliation'):
            text(getattr(self, name), name)
        if len(self.args_digest) != 64 or any(c not in '0123456789abcdef' for c in self.args_digest):
            raise RealityError('args_digest must be sha256')
        integer(self.uncertainty_seconds, 'uncertainty_seconds', 1)
        integer(self.required_level, 'required_level')
        if self.required_level > 4 or type(self.irreversible) is not bool:
            raise RealityError('invalid effect flags')
        if self.irreversible and self.required_level != 4:
            raise RealityError('irreversible effect requires level 4')


@dataclass(frozen=True)
class Mission:
    id: str
    run_id: str
    intent: str
    executor: str
    policy_digest: str
    obligations: tuple[Obligation, ...]
    effects: tuple[Effect, ...]
    budget_microusd: int = 0
    privacy: str = 'LOCAL'
    schema_version: int = 1

    def __post_init__(self):
        for name in ('id', 'run_id', 'intent', 'executor', 'policy_digest'):
            text(getattr(self, name), name)
        integer(self.budget_microusd, 'budget')
        if self.schema_version != 1 or type(self.schema_version) is not int or self.privacy not in ('LOCAL', 'PUBLIC'):
            raise RealityError('invalid mission metadata')
        if type(self.obligations) is not tuple or not self.obligations or type(self.effects) is not tuple:
            raise RealityError('immutable nonempty obligations required')
        if not all(type(o) is Obligation for o in self.obligations) or not all(type(e) is Effect for e in self.effects):
            raise RealityError('typed IR required')
        obs = {o.id: o for o in self.obligations}
        if len(obs) != len(self.obligations) or len({e.id for e in self.effects}) != len(self.effects):
            raise RealityError('duplicate id')
        if len({e.obligation_id for e in self.effects}) != len(self.effects):
            raise RealityError('each effect requires its own post-state obligation')
        for e in self.effects:
            if e.obligation_id not in obs or obs[e.obligation_id].target != e.target:
                raise RealityError('effect must bind matching post-state obligation')

    @property
    def fingerprint(self):
        return digest(asdict(self))

    def obligation(self, oid):
        for o in self.obligations:
            if o.id == oid:
                return o
        raise RealityError('unknown obligation')

    def effect(self, eid):
        if len({e.obligation_id for e in self.effects}) != len(self.effects):
            raise RealityError('each effect requires its own post-state obligation')
        for e in self.effects:
            if e.id == eid:
                return e
        raise RealityError('unknown effect')


class RealityCompiler:
    """Strict deterministic lowering. An external planner may propose this JSON.

    Explicit compiler input is not proof that intent interpretation is correct.
    Host policy admission and owner goal confirmation remain separate boundaries.
    """
    def compile(self, proposal: dict) -> Mission:
        try:
            p = dict(proposal)
            p['obligations'] = tuple(Obligation(**o) for o in p['obligations'])
            p['effects'] = tuple(Effect(**e) for e in p['effects'])
            return Mission(**p)
        except (TypeError, KeyError, AttributeError) as exc:
            raise RealityError('malformed Mission IR') from exc
