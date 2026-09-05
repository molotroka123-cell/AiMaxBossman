"""Verifier-issued receipts, bound to exact mission/run/obligation/expectation.

Keys and observation adapters belong in the trusted host process, not agent tools.
HMAC proves provenance/integrity, not the honesty of a compromised verifier.
"""
from dataclasses import dataclass, asdict
import hmac
import hashlib
import time
from .contracts import Knowledge, RealityError, canonical, digest, integer


@dataclass(frozen=True)
class Receipt:
    mission_digest: str
    obligation_id: str
    target: str
    expected_digest: str
    observed_digest: str
    verifier: str
    principal: str
    observed_at: int
    knowledge: str
    dispatch_binding: str = ''
    signature: str = ''

    def payload(self):
        d = asdict(self)
        d.pop('signature')
        return d


class ProofAuthority:
    def __init__(self, keys: dict[str, bytes], principals: dict[str, str], clock=time.time):
        if set(keys) != set(principals) or any(not isinstance(k, bytes) or len(k) < 32 for k in keys.values()):
            raise RealityError('verifiers need protected >=32 byte keys and principals')
        self._keys, self._principals, self.clock = dict(keys), dict(principals), clock

    def _sign(self, receipt):
        key = self._keys.get(receipt.verifier)
        if key is None:
            raise RealityError('unknown verifier')
        return hmac.new(key, canonical(receipt.payload()).encode(), hashlib.sha256).hexdigest()

    def observe(self, mission, oid, observer, *, dispatch_binding=""):
        """observer is a host-registered fresh read, never a model/tool result dict."""
        o = mission.obligation(oid)
        principal = self._principals.get(o.verifier)
        if not principal or principal == mission.executor:
            raise RealityError('independent effective identity required')
        value = observer(o.target)
        actual = digest(value)
        if actual != o.expected_digest:
            raise RealityError('post-state does not match expected state')
        r = Receipt(mission.fingerprint, o.id, o.target, o.expected_digest, actual,
                    o.verifier, principal, int(self.clock()), Knowledge.VERIFIED.value, dispatch_binding)
        return Receipt(**r.payload(), signature=self._sign(r))

    def check(self, mission, receipt):
        if type(receipt) is not Receipt:
            raise RealityError('typed receipt required')
        o = mission.obligation(receipt.obligation_id)
        integer(receipt.observed_at, 'observed_at')
        if receipt.knowledge != Knowledge.VERIFIED.value:
            raise RealityError('epistemic downgrade: not verified')
        if (receipt.mission_digest, receipt.target, receipt.expected_digest, receipt.observed_digest, receipt.verifier) != (
                mission.fingerprint, o.target, o.expected_digest, o.expected_digest, o.verifier):
            raise RealityError('receipt binding mismatch')
        if receipt.principal != self._principals.get(o.verifier) or receipt.principal == mission.executor:
            raise RealityError('invalid independent identity')
        age = self.clock() - receipt.observed_at
        if age < 0 or age > o.max_age_seconds:
            raise RealityError('stale or future receipt')
        if not isinstance(receipt.signature, str) or not hmac.compare_digest(receipt.signature, self._sign(receipt)):
            raise RealityError('invalid receipt signature')
        return True
