"""Single-host durable state. SQLite FULL sync; serialize claims before dispatch.

Do not place on network filesystems or use across Fleet hosts. Integrate the same
CAS contracts into the existing transactional DB for distributed execution.
"""
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict
from .contracts import RealityError, canonical, integer, digest
from .proof import Receipt


class RealityStore:
    def __init__(self, path, clock=time.time):
        self.clock = clock
        self.db = sqlite3.connect(str(path), timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS missions(
          id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, payload TEXT NOT NULL,
          state TEXT NOT NULL, budget INTEGER NOT NULL, spent INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS effects(
          mission TEXT NOT NULL REFERENCES missions(id), id TEXT NOT NULL,
          state TEXT NOT NULL, dispatched_at INTEGER, owner TEXT, fence INTEGER NOT NULL DEFAULT 0,
          reconciliation TEXT, PRIMARY KEY(mission,id));
        CREATE TABLE IF NOT EXISTS receipts(
          mission TEXT NOT NULL REFERENCES missions(id), obligation TEXT NOT NULL,
          payload TEXT NOT NULL, PRIMARY KEY(mission,obligation));
        CREATE TABLE IF NOT EXISTS charges(
          mission TEXT NOT NULL REFERENCES missions(id), id TEXT NOT NULL, amount INTEGER NOT NULL,
          PRIMARY KEY(mission,id));
        CREATE TABLE IF NOT EXISTS events(
          seq INTEGER PRIMARY KEY AUTOINCREMENT, mission TEXT NOT NULL, kind TEXT NOT NULL,
          at INTEGER NOT NULL, payload TEXT NOT NULL);
        ''')

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def _event(self, mid, kind, payload):
        self.db.execute('INSERT INTO events(mission,kind,at,payload) VALUES(?,?,?,?)',
                        (mid, kind, int(self.clock()), canonical(payload)))

    def _bound(self, m):
        row = self.db.execute('SELECT * FROM missions WHERE id=?', (m.id,)).fetchone()
        if row is None or row['fingerprint'] != m.fingerprint:
            raise RealityError('mission identity or full plan mismatch')
        return row

    def register(self, m):
        with self.transaction():
            old = self.db.execute('SELECT id FROM missions WHERE id=?', (m.id,)).fetchone()
            if old:
                self._bound(m)
                return
            self.db.execute('INSERT INTO missions(id,fingerprint,payload,state,budget) VALUES(?,?,?,?,?)',
                            (m.id, m.fingerprint, canonical(asdict(m)), 'ACTIVE', m.budget_microusd))
            self.db.executemany('INSERT INTO effects(mission,id,state) VALUES(?,?,?)',
                                [(m.id, e.id, 'PREPARED') for e in m.effects])
            self._event(m.id, 'MISSION_REGISTERED', {'fingerprint': m.fingerprint})

    def reserve(self, m, charge_id, amount):
        """Conservative spend reservation. No automatic refund after uncertainty.

        Owner global ledger must ALSO reserve atomically before paid dispatch.
        This local ledger limits this mission; it is not a replacement global cap.
        """
        integer(amount, 'amount')
        if not isinstance(charge_id, str) or not charge_id:
            raise RealityError('charge id required')
        with self.transaction():
            row = self._bound(m)
            if row['state'] != 'ACTIVE':
                raise RealityError('mission is not active')
            old = self.db.execute('SELECT amount FROM charges WHERE mission=? AND id=?', (m.id, charge_id)).fetchone()
            if old:
                if old['amount'] != amount:
                    raise RealityError('charge id reused with different amount')
                return
            if row['spent'] + amount > row['budget']:
                raise RealityError('budget exhausted')
            self.db.execute('INSERT INTO charges VALUES(?,?,?)', (m.id, charge_id, amount))
            self.db.execute('UPDATE missions SET spent=spent+? WHERE id=?', (amount, m.id))
            self._event(m.id, 'BUDGET_RESERVED', {'charge_id': charge_id, 'amount': amount})

    def claim(self, m, eid, owner):
        """Commit ESCROW before executing. No automatic lease expiry/reclaim."""
        m.effect(eid)
        if not isinstance(owner, str) or not owner:
            raise RealityError('owner required')
        with self.transaction():
            if self._bound(m)['state'] != 'ACTIVE':
                raise RealityError('mission is not active')
            row = self.db.execute('SELECT * FROM effects WHERE mission=? AND id=?', (m.id, eid)).fetchone()
            if row['state'] not in ('PREPARED', 'SAFE_TO_RETRY'):
                raise RealityError('effect in escrow: reconcile, never blindly retry')
            fence = row['fence'] + 1
            self.db.execute('UPDATE effects SET state=?,owner=?,fence=?,dispatched_at=? WHERE mission=? AND id=?',
                            ('EFFECT_ESCROW', owner, fence, int(self.clock()), m.id, eid))
            self._event(m.id, 'EFFECT_ESCROW', {'effect': eid, 'fence': fence})
            return fence

    def put_receipt(self, m, receipt, authority):
        authority.check(m, receipt)
        with self.transaction():
            if self._bound(m)['state'] != 'ACTIVE':
                raise RealityError('mission is not active')
            self.db.execute('INSERT OR REPLACE INTO receipts VALUES(?,?,?)',
                            (m.id, receipt.obligation_id, canonical(asdict(receipt))))

    def confirm(self, m, eid, owner, fence, receipt, authority):
        authority.check(m, receipt)
        e = m.effect(eid)
        with self.transaction():
            if self._bound(m)['state'] != 'ACTIVE':
                raise RealityError('mission is not active')
            row = self.db.execute('SELECT * FROM effects WHERE mission=? AND id=?', (m.id, eid)).fetchone()
            if (row['state'], row['owner'], row['fence']) != ('EFFECT_ESCROW', owner, fence):
                raise RealityError('stale owner/fence or non-escrow state')
            if (receipt.obligation_id != e.obligation_id or receipt.observed_at < row['dispatched_at']
                    or receipt.dispatch_binding != digest([m.fingerprint, eid, fence])):
                raise RealityError('receipt predates dispatch or wrong obligation')
            self.db.execute('INSERT OR REPLACE INTO receipts VALUES(?,?,?)',
                            (m.id, receipt.obligation_id, canonical(asdict(receipt))))
            self.db.execute('UPDATE effects SET state=? WHERE mission=? AND id=?', ('CONFIRMED', m.id, eid))
            self._event(m.id, 'CONFIRMED_ALREADY_HAPPENED', {'effect': eid})

    def reconcile_absent(self, m, eid, owner, fence, *, absence_verified, prior_attempt_terminal, reference):
        """Trusted host reconciliation ONLY; booleans must never come from model JSON.

        Retry requires authoritative absence AND proof old attempt cannot arrive
        later (cancel/join or provider terminal request status). Elapsed time is
        never proof of either. Unknown => MANUAL_REVIEW_REQUIRED, no auto reset.
        """
        if type(absence_verified) is not bool or type(prior_attempt_terminal) is not bool or not reference:
            raise RealityError('invalid reconciliation')
        with self.transaction():
            if self._bound(m)['state'] != 'ACTIVE':
                raise RealityError('mission is not active')
            row = self.db.execute('SELECT * FROM effects WHERE mission=? AND id=?', (m.id, eid)).fetchone()
            if row is None or (row['state'], row['owner'], row['fence']) != ('EFFECT_ESCROW', owner, fence):
                raise RealityError('stale reconciliation')
            e = m.effect(eid)
            expired = self.clock() - row['dispatched_at'] > e.uncertainty_seconds
            state = 'SAFE_TO_RETRY' if absence_verified and prior_attempt_terminal and not expired else 'MANUAL_REVIEW_REQUIRED'
            self.db.execute('UPDATE effects SET state=?,reconciliation=? WHERE mission=? AND id=?',
                            (state, str(reference), m.id, eid))
            self._event(m.id, state, {'effect': eid})
            return state

    def completion(self, m, authority):
        import json
        with self.transaction():
            self._bound(m)
            unresolved = self.db.execute('SELECT id FROM effects WHERE mission=? AND state != ?',
                                         (m.id, 'CONFIRMED')).fetchall()
            if unresolved:
                raise RealityError('unconfirmed effects')
            stored = self.db.execute('SELECT payload FROM receipts WHERE mission=?', (m.id,)).fetchall()
            receipts = {r.obligation_id: r for r in (Receipt(**json.loads(x['payload'])) for x in stored)}
            for o in m.obligations:
                if o.id not in receipts:
                    raise RealityError('missing proof: ' + o.id)
                authority.check(m, receipts[o.id])
            for e in m.effects:
                row = self.db.execute('SELECT fence,dispatched_at FROM effects WHERE mission=? AND id=?', (m.id, e.id)).fetchone()
                receipt = receipts[e.obligation_id]
                if (receipt.dispatch_binding != digest([m.fingerprint, e.id, row['fence']])
                        or receipt.observed_at < row['dispatched_at']):
                    raise RealityError('completion receipt not bound to dispatched effect')
            self.db.execute('UPDATE missions SET state=? WHERE id=?', ('COMPLETE', m.id))
            self._event(m.id, 'MISSION_COMPLETE', {'fingerprint': m.fingerprint})
            return {'verdict': 'PASS', 'mission_digest': m.fingerprint, 'obligations': len(receipts)}
