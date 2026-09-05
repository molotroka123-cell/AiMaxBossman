"""Conservative deterministic support layers; no claims of causal identification."""
from dataclasses import dataclass, asdict
import json
import math
import sqlite3
from .contracts import RealityError, digest, integer, unit, canonical


@dataclass(frozen=True)
class TwinResult:
    expected_digest: str
    observed_digest: str
    unexpected_keys: tuple[str, ...]
    missing_keys: tuple[str, ...]

    @property
    def divergent(self):
        return bool(self.unexpected_keys or self.missing_keys)


def compare_world(expected: dict, observed: dict) -> TwinResult:
    """Exact declared snapshot comparison, not a predictive world simulator."""
    unexpected = tuple(sorted(k for k in observed if k not in expected or digest(observed[k]) != digest(expected[k])))
    missing = tuple(sorted(set(expected) - set(observed)))
    return TwinResult(digest(expected), digest(observed), unexpected, missing)


@dataclass(frozen=True)
class Fact:
    id: str
    value: str
    source: str
    mission: str
    depends_on: tuple[str, ...] = ()
    supersedes: tuple[str, ...] = ()
    confidence: float = 0.0
    expires_at: int = 0
    privacy: str = 'LOCAL'

    def __post_init__(self):
        unit(self.confidence)
        integer(self.expires_at, 'expires_at')
        if self.privacy not in ('LOCAL', 'PUBLIC') or not self.id or not self.source or not self.mission:
            raise RealityError('invalid fact provenance')
        if type(self.depends_on) is not tuple or type(self.supersedes) is not tuple:
            raise RealityError('immutable dependencies required')


class MemoryCompiler:
    def __init__(self, facts):
        facts = tuple(facts)
        self.facts = {f.id: f for f in facts}
        if len(self.facts) != len(facts):
            raise RealityError('duplicate fact id')

    def slice(self, roots, *, now, cloud=False, max_characters=16000):
        """Dependency-complete slice. Never truncate away required dependencies.

        Roots must come from a decision-specific host/planner contract. This does
        not mathematically prove minimal sufficiency for arbitrary decisions.
        """
        integer(max_characters, 'max_characters', 1)
        replaced = {old for f in self.facts.values() if f.expires_at > now for old in f.supersedes}
        visiting, done, result = set(), set(), []
        def visit(fid):
            if fid in visiting:
                raise RealityError('dependency cycle')
            if fid in done:
                return
            f = self.facts.get(fid)
            if f is None or f.expires_at <= now or fid in replaced:
                raise RealityError('missing, expired or superseded dependency')
            if cloud and f.privacy != 'PUBLIC':
                raise RealityError('private dependency cannot be exported')
            visiting.add(fid)
            for dep in f.depends_on:
                visit(dep)
            visiting.remove(fid)
            done.add(fid)
            result.append(f)
        for root in roots:
            visit(root)
        payload = canonical([asdict(f) for f in result])
        if len(payload) > max_characters:
            raise RealityError('required context exceeds budget; replan decision')
        return tuple(result)


@dataclass(frozen=True)
class Bid:
    route: str
    predicted_success: float
    cost_microusd: int
    latency_ms: int
    risk: float
    tool_reliability: float
    expected_retries: float = 0.0
    local: bool = True

    def __post_init__(self):
        for x in (self.predicted_success, self.risk, self.tool_reliability):
            unit(x)
        integer(self.cost_microusd, 'cost')
        integer(self.latency_ms, 'latency', 1)
        if not self.route or type(self.local) is not bool or not math.isfinite(self.expected_retries) or self.expected_retries < 0:
            raise RealityError('invalid bid')


class LearningLedger:
    """Durable outcomes, quarantine and candidate comparison; host-only writes.

    Use a separate path from RealityStore. No auto-promotion or policy editing.
    Mission completion must be verified by the caller before success settlement.
    """
    def __init__(self, path):
        self.db = sqlite3.connect(str(path))
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS settlements(
          mission TEXT PRIMARY KEY, route TEXT, probability REAL, success INTEGER,
          false_success INTEGER, hard_fail INTEGER);
        CREATE TABLE IF NOT EXISTS lessons(
          mission TEXT PRIMARY KEY, payload TEXT NOT NULL);
        ''')

    def close(self):
        self.db.close()

    def settle(self, mission_digest, bid, *, verified_success, false_success=False, hard_fail=False):
        for x in (verified_success, false_success, hard_fail):
            if type(x) is not bool:
                raise RealityError('typed settlement required')
        if verified_success and (false_success or hard_fail):
            raise RealityError('contradictory outcome')
        row = (mission_digest, bid.route, bid.predicted_success, int(verified_success), int(false_success), int(hard_fail))
        with self.db:
            old = self.db.execute('SELECT * FROM settlements WHERE mission=?', (mission_digest,)).fetchone()
            if old:
                if old != row:
                    raise RealityError('settlement conflict')
                return
            self.db.execute('INSERT INTO settlements VALUES(?,?,?,?,?,?)', row)

    def reputation(self, route):
        rows = self.db.execute('SELECT probability,success,false_success,hard_fail FROM settlements WHERE route=?', (route,)).fetchall()
        n = len(rows)
        success = sum(r[1] for r in rows)
        return {'count': n, 'success_rate': (success + 1) / (n + 2),
                'brier': sum((r[0] - r[1]) ** 2 for r in rows) / n if n else 0.25,
                'quarantined': any(r[3] for r in rows) or sum(r[2] for r in rows) >= 3}

    def choose(self, bids, *, budget_microusd, privacy='LOCAL'):
        integer(budget_microusd, 'budget')
        if privacy not in ('LOCAL', 'PUBLIC'):
            raise RealityError('unknown privacy')
        choices = []
        for b in bids:
            r = self.reputation(b.route)
            cost = math.ceil(b.cost_microusd * (1 + b.expected_retries))
            if r['quarantined'] or cost > budget_microusd or (privacy == 'LOCAL' and not b.local):
                continue
            p = min(b.predicted_success, r['success_rate']) * (1 - r['brier'])
            utility = p * b.tool_reliability * (1 - b.risk) / (max(1, cost) * b.latency_ms)
            choices.append((utility, b.route, b))
        if not choices:
            raise RealityError('no admissible route')
        return max(choices, key=lambda x: (x[0], x[1]))[2]

    def record_lesson(self, mission_digest, *, context_digest, action_digest, expected, observed, cause_hypothesis, lesson):
        delta = compare_world(expected, observed)
        # Persist hashes and delta keys, no raw clinical/prompt data.
        record = {'context_digest': context_digest, 'action_digest': action_digest,
                  'delta': asdict(delta), 'cause_hypothesis': cause_hypothesis,
                  'cause_knowledge': 'INFERRED', 'lesson': lesson}
        payload = canonical(record)
        with self.db:
            old = self.db.execute('SELECT payload FROM lessons WHERE mission=?', (mission_digest,)).fetchone()
            if old and old[0] != payload:
                raise RealityError('lesson conflict')
            self.db.execute('INSERT OR IGNORE INTO lessons VALUES(?,?)', (mission_digest, payload))
        return record


def candidate_eligible(baseline, candidate, *, minimum_cases=20):
    """Compare SAME externally fixed case IDs and suite digest; owner promotes."""
    if baseline['suite_digest'] != candidate['suite_digest'] or baseline['cases'].keys() != candidate['cases'].keys():
        return False
    if len(candidate['cases']) < minimum_cases or candidate['hard_failures']:
        return False
    for result in (*baseline['cases'].values(), *candidate['cases'].values()):
        if type(result) is not bool:
            raise RealityError('case results must be booleans')
    # No loss of a previously passed case, plus strict improvement.
    return (all(not ok or candidate['cases'][case] for case, ok in baseline['cases'].items())
            and sum(candidate['cases'].values()) > sum(baseline['cases'].values()))
