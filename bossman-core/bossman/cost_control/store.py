from __future__ import annotations
import hashlib
import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .models import (
    ZERO, BucketSnapshot, BudgetContext, BudgetDecision, BudgetPolicy, BudgetScope,
    DecisionKind, HardLimitAction, Reservation, ReservationStatus, money,
)

class BudgetError(RuntimeError):
    pass

class BudgetExtensionRequired(BudgetError):
    def __init__(self, delta_usd: Decimal):
        super().__init__(f"actual cost exceeds reservation by {delta_usd}")
        self.delta_usd = delta_usd

@dataclass(slots=True, frozen=True)
class OverrideGrant:
    token: str
    context_fingerprint: str
    extra_usd: Decimal
    expires_at: float

class SQLiteBudgetStore:
    """Atomic subsystem-local money ledger. BEGIN IMMEDIATE prevents parallel overspend."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=FULL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=30000")
        return c

    def _init(self) -> None:
        with self._connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS policies(
              scope TEXT NOT NULL, subject TEXT NOT NULL, hard_limit_usd TEXT NOT NULL,
              warning_fraction TEXT NOT NULL, hard_action TEXT NOT NULL,
              enabled INTEGER NOT NULL, updated_at REAL NOT NULL,
              PRIMARY KEY(scope, subject)
            );
            CREATE TABLE IF NOT EXISTS buckets(
              bucket_key TEXT PRIMARY KEY, scope TEXT NOT NULL, subject TEXT NOT NULL,
              spent_usd TEXT NOT NULL DEFAULT '0', reserved_usd TEXT NOT NULL DEFAULT '0',
              warning_emitted INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reservations(
              id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
              context_fingerprint TEXT NOT NULL, estimated_usd TEXT NOT NULL,
              actual_usd TEXT, status TEXT NOT NULL, expires_at REAL NOT NULL,
              created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reservation_buckets(
              reservation_id TEXT NOT NULL REFERENCES reservations(id) ON DELETE CASCADE,
              bucket_key TEXT NOT NULL REFERENCES buckets(bucket_key),
              amount_usd TEXT NOT NULL,
              PRIMARY KEY(reservation_id, bucket_key)
            );
            CREATE TABLE IF NOT EXISTS overrides(
              token_hash TEXT PRIMARY KEY, context_fingerprint TEXT NOT NULL,
              extra_usd TEXT NOT NULL, expires_at REAL NOT NULL,
              used_at REAL, created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_reservation_status_exp
              ON reservations(status, expires_at);
            """)

    def set_policy(self, p: BudgetPolicy) -> None:
        with self._lock, self._connect() as c:
            c.execute(
                """INSERT INTO policies(scope,subject,hard_limit_usd,warning_fraction,
                   hard_action,enabled,updated_at) VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(scope,subject) DO UPDATE SET
                   hard_limit_usd=excluded.hard_limit_usd,
                   warning_fraction=excluded.warning_fraction,
                   hard_action=excluded.hard_action,
                   enabled=excluded.enabled,
                   updated_at=excluded.updated_at""",
                (p.scope.value, p.subject, str(p.hard_limit_usd), str(p.warning_fraction),
                 p.hard_action.value, int(p.enabled), time.time()),
            )
            # A policy change creates a new warning regime.
            if p.subject == "*":
                c.execute("UPDATE buckets SET warning_emitted=0 WHERE scope=?", (p.scope.value,))
            else:
                c.execute("UPDATE buckets SET warning_emitted=0 WHERE bucket_key=?",
                          (f"{p.scope.value}:{p.subject}",))

    def list_policies(self) -> list[BudgetPolicy]:
        with self._connect() as c:
            rows = c.execute("SELECT * FROM policies ORDER BY scope,subject").fetchall()
        return [
            BudgetPolicy(
                BudgetScope(r["scope"]), money(r["hard_limit_usd"]), r["subject"],
                Decimal(r["warning_fraction"]), HardLimitAction(r["hard_action"]),
                bool(r["enabled"]),
            ) for r in rows
        ]

    def has_enabled_policies(self) -> bool:
        with self._connect() as c:
            return bool(c.execute("SELECT 1 FROM policies WHERE enabled=1 LIMIT 1").fetchone())

    def issue_override(self, context: BudgetContext, extra_usd, ttl_s: int = 300) -> OverrideGrant:
        extra = money(extra_usd)
        if extra <= ZERO:
            raise ValueError("override extra must be > 0")
        ttl_s = max(1, min(int(ttl_s), 3600))
        raw = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        fp = context.fingerprint()
        exp = time.time() + ttl_s
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT INTO overrides VALUES(?,?,?,?,NULL,?)",
                (token_hash, fp, str(extra), exp, time.time()),
            )
        return OverrideGrant(raw, fp, extra, exp)

    def reserve(self, context: BudgetContext, estimated_usd, *,
                idempotency_key: str, ttl_s: int = 900,
                override_token: str | None = None) -> BudgetDecision:
        amount = money(estimated_usd)
        if amount <= ZERO:
            return BudgetDecision(DecisionKind.ALLOW, "zero-cost request")
        if not idempotency_key or len(idempotency_key) > 300:
            raise ValueError("bounded idempotency_key is required")

        now = time.time()
        expires = now + max(5, min(int(ttl_s), 86400))
        ctx = context.normalized()
        fp = ctx.fingerprint()

        with self._lock, self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                self._expire_locked(c, now)
                old = c.execute(
                    "SELECT * FROM reservations WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
                if old:
                    res = self._reservation(old)
                    c.execute("COMMIT")
                    # A billable-attempt key is single-claim. Returning ALLOW here could
                    # let two duplicate callers share one reservation and open two cloud calls.
                    return BudgetDecision(
                        DecisionKind.DENY,
                        f"duplicate billable attempt ({res.status.value})",
                    )

                apps = self._applications_locked(c, ctx)
                if not apps:
                    c.execute("COMMIT")
                    return BudgetDecision(DecisionKind.ALLOW, "no enabled budget policy")

                snaps: list[BucketSnapshot] = []
                warnings: list[BucketSnapshot] = []
                exceeded: list[tuple[BucketSnapshot, Decimal]] = []

                for policy, key, subject in apps:
                    row = c.execute(
                        "SELECT * FROM buckets WHERE bucket_key=?", (key,)
                    ).fetchone()
                    if not row:
                        c.execute(
                            """INSERT INTO buckets(bucket_key,scope,subject,spent_usd,
                               reserved_usd,warning_emitted,updated_at)
                               VALUES(?,?,?,?,?,?,?)""",
                            (key, policy.scope.value, subject, "0", "0", 0, now),
                        )
                        spent = reserved = ZERO
                        warned = False
                    else:
                        spent = money(row["spent_usd"])
                        reserved = money(row["reserved_usd"])
                        warned = bool(row["warning_emitted"])

                    snap = BucketSnapshot(
                        key, policy.scope, subject, spent, reserved,
                        policy.hard_limit_usd, policy.warning_fraction, policy.hard_action,
                    )
                    snaps.append(snap)
                    projected = spent + reserved + amount
                    if projected > policy.hard_limit_usd:
                        exceeded.append((snap, projected - policy.hard_limit_usd))
                    elif not warned and projected >= policy.hard_limit_usd * policy.warning_fraction:
                        warnings.append(snap)

                if exceeded:
                    required = max(delta for _, delta in exceeded)
                    if override_token and self._consume_override_locked(
                        c, override_token, fp, required, now
                    ):
                        exceeded.clear()
                    else:
                        c.execute("COMMIT")
                        kind = (
                            DecisionKind.DENY
                            if any(s.hard_action is HardLimitAction.STOP for s, _ in exceeded)
                            else DecisionKind.REQUIRE_APPROVAL
                        )
                        return BudgetDecision(
                            kind,
                            "hard budget exceeded" if kind is DecisionKind.DENY
                            else "budget override approval required",
                            required_extra_usd=required,
                            exceeded=tuple(s for s, _ in exceeded),
                        )

                rid = "br_" + uuid.uuid4().hex
                c.execute(
                    """INSERT INTO reservations(id,idempotency_key,context_fingerprint,
                       estimated_usd,status,expires_at,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (rid, idempotency_key, fp, str(amount), ReservationStatus.ACTIVE.value,
                     expires, now, now),
                )
                for snap in snaps:
                    c.execute(
                        "UPDATE buckets SET reserved_usd=?,updated_at=? WHERE bucket_key=?",
                        (str(snap.reserved_usd + amount), now, snap.bucket_key),
                    )
                    c.execute(
                        "INSERT INTO reservation_buckets VALUES(?,?,?)",
                        (rid, snap.bucket_key, str(amount)),
                    )
                for snap in warnings:
                    c.execute(
                        "UPDATE buckets SET warning_emitted=1,updated_at=? WHERE bucket_key=?",
                        (now, snap.bucket_key),
                    )
                c.execute("COMMIT")
                return BudgetDecision(
                    DecisionKind.ALLOW, "reserved",
                    Reservation(
                        rid, idempotency_key, fp, amount,
                        ReservationStatus.ACTIVE, expires,
                    ),
                    warnings=tuple(warnings),
                )
            except Exception:
                c.execute("ROLLBACK")
                raise

    def extend_reservation(self, reservation_id: str, additional_usd, *,
                           override_token: str | None = None) -> BudgetDecision:
        """Atomically buy more headroom for an ACTIVE reservation."""
        delta = money(additional_usd)
        if delta <= ZERO:
            raise ValueError("extension must be > 0")
        now = time.time()
        with self._lock, self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                row = c.execute(
                    "SELECT * FROM reservations WHERE id=?", (reservation_id,)
                ).fetchone()
                if not row or row["status"] != ReservationStatus.ACTIVE.value:
                    c.execute("COMMIT")
                    return BudgetDecision(DecisionKind.DENY, "reservation not active")
                fp = row["context_fingerprint"]
                links = c.execute(
                    """SELECT rb.bucket_key,rb.amount_usd,b.scope,b.subject,
                              b.spent_usd,b.reserved_usd
                       FROM reservation_buckets rb JOIN buckets b
                         ON b.bucket_key=rb.bucket_key
                       WHERE rb.reservation_id=?""",
                    (reservation_id,),
                ).fetchall()
                exceeded: list[tuple[BucketSnapshot, Decimal]] = []
                for link in links:
                    policy = self._resolve_policy_locked(
                        c, BudgetScope(link["scope"]), link["subject"]
                    )
                    if policy is None:
                        continue
                    snap = BucketSnapshot(
                        link["bucket_key"], policy.scope, link["subject"],
                        money(link["spent_usd"]), money(link["reserved_usd"]),
                        policy.hard_limit_usd, policy.warning_fraction,
                        policy.hard_action,
                    )
                    projected = snap.spent_usd + snap.reserved_usd + delta
                    if projected > policy.hard_limit_usd:
                        exceeded.append((snap, projected - policy.hard_limit_usd))
                if exceeded:
                    required = max(d for _, d in exceeded)
                    if not (override_token and self._consume_override_locked(
                        c, override_token, fp, required, now
                    )):
                        c.execute("COMMIT")
                        kind = (
                            DecisionKind.DENY
                            if any(s.hard_action is HardLimitAction.STOP for s, _ in exceeded)
                            else DecisionKind.REQUIRE_APPROVAL
                        )
                        return BudgetDecision(
                            kind, "reservation extension blocked",
                            required_extra_usd=required,
                            exceeded=tuple(s for s, _ in exceeded),
                        )
                for link in links:
                    c.execute(
                        "UPDATE buckets SET reserved_usd=?,updated_at=? WHERE bucket_key=?",
                        (str(money(link["reserved_usd"]) + delta), now, link["bucket_key"]),
                    )
                    c.execute(
                        """UPDATE reservation_buckets SET amount_usd=?
                           WHERE reservation_id=? AND bucket_key=?""",
                        (str(money(link["amount_usd"]) + delta),
                         reservation_id, link["bucket_key"]),
                    )
                estimate = money(row["estimated_usd"]) + delta
                c.execute(
                    "UPDATE reservations SET estimated_usd=?,updated_at=? WHERE id=?",
                    (str(estimate), now, reservation_id),
                )
                c.execute("COMMIT")
                return BudgetDecision(
                    DecisionKind.ALLOW, "reservation extended",
                    Reservation(
                        reservation_id, row["idempotency_key"], fp, estimate,
                        ReservationStatus.ACTIVE, float(row["expires_at"]),
                    ),
                )
            except Exception:
                c.execute("ROLLBACK")
                raise

    def commit(self, reservation_id: str, actual_usd) -> Reservation:
        actual = money(actual_usd)
        now = time.time()
        with self._lock, self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                row = c.execute(
                    "SELECT * FROM reservations WHERE id=?", (reservation_id,)
                ).fetchone()
                if not row:
                    raise BudgetError("unknown reservation")
                status = ReservationStatus(row["status"])
                if status is ReservationStatus.COMMITTED:
                    c.execute("COMMIT")
                    return self._reservation(row)
                if status is not ReservationStatus.ACTIVE:
                    raise BudgetError(f"cannot commit {status.value}")
                estimate = money(row["estimated_usd"])
                if actual > estimate:
                    raise BudgetExtensionRequired(actual - estimate)

                links = c.execute(
                    """SELECT rb.bucket_key,rb.amount_usd,b.spent_usd,b.reserved_usd
                       FROM reservation_buckets rb JOIN buckets b
                         ON b.bucket_key=rb.bucket_key
                       WHERE rb.reservation_id=?""",
                    (reservation_id,),
                ).fetchall()
                for link in links:
                    held = money(link["amount_usd"])
                    reserved = money(link["reserved_usd"])
                    spent = money(link["spent_usd"])
                    if reserved < held:
                        raise BudgetError("ledger corruption: reserved underflow")
                    c.execute(
                        """UPDATE buckets SET spent_usd=?,reserved_usd=?,updated_at=?
                           WHERE bucket_key=?""",
                        (str(spent + actual), str(reserved - held), now, link["bucket_key"]),
                    )
                c.execute(
                    "UPDATE reservations SET actual_usd=?,status=?,updated_at=? WHERE id=?",
                    (str(actual), ReservationStatus.COMMITTED.value, now, reservation_id),
                )
                c.execute("COMMIT")
                fresh = c.execute(
                    "SELECT * FROM reservations WHERE id=?", (reservation_id,)
                ).fetchone()
                return self._reservation(fresh)
            except BudgetExtensionRequired:
                c.execute("ROLLBACK")
                raise
            except Exception:
                c.execute("ROLLBACK")
                raise

    def release(self, reservation_id: str) -> Reservation | None:
        return self._release_as(reservation_id, ReservationStatus.RELEASED)

    def cleanup_expired(self) -> int:
        with self._lock, self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                n = self._expire_locked(c, time.time())
                c.execute("COMMIT")
                return n
            except Exception:
                c.execute("ROLLBACK")
                raise

    def snapshots(self) -> list[dict]:
        with self._connect() as c:
            return [
                dict(r) for r in c.execute(
                    "SELECT * FROM buckets ORDER BY scope,subject,bucket_key"
                ).fetchall()
            ]

    def _release_as(self, rid: str, final: ReservationStatus) -> Reservation | None:
        now = time.time()
        with self._lock, self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                row = c.execute("SELECT * FROM reservations WHERE id=?", (rid,)).fetchone()
                if not row:
                    c.execute("COMMIT")
                    return None
                if ReservationStatus(row["status"]) is not ReservationStatus.ACTIVE:
                    c.execute("COMMIT")
                    return self._reservation(row)
                links = c.execute(
                    """SELECT rb.bucket_key,rb.amount_usd,b.reserved_usd
                       FROM reservation_buckets rb JOIN buckets b
                         ON b.bucket_key=rb.bucket_key
                       WHERE rb.reservation_id=?""", (rid,)
                ).fetchall()
                for link in links:
                    new_reserved = max(
                        ZERO,
                        money(link["reserved_usd"]) - money(link["amount_usd"]),
                    )
                    c.execute(
                        "UPDATE buckets SET reserved_usd=?,updated_at=? WHERE bucket_key=?",
                        (str(new_reserved), now, link["bucket_key"]),
                    )
                c.execute(
                    "UPDATE reservations SET status=?,updated_at=? WHERE id=?",
                    (final.value, now, rid),
                )
                c.execute("COMMIT")
                fresh = c.execute("SELECT * FROM reservations WHERE id=?", (rid,)).fetchone()
                return self._reservation(fresh)
            except Exception:
                c.execute("ROLLBACK")
                raise

    def _expire_locked(self, c: sqlite3.Connection, now: float) -> int:
        rows = c.execute(
            "SELECT id FROM reservations WHERE status=? AND expires_at<=?",
            (ReservationStatus.ACTIVE.value, now),
        ).fetchall()
        for row in rows:
            rid = row["id"]
            links = c.execute(
                """SELECT rb.bucket_key,rb.amount_usd,b.reserved_usd
                   FROM reservation_buckets rb JOIN buckets b
                     ON b.bucket_key=rb.bucket_key
                   WHERE rb.reservation_id=?""", (rid,)
            ).fetchall()
            for link in links:
                new_reserved = max(
                    ZERO,
                    money(link["reserved_usd"]) - money(link["amount_usd"]),
                )
                c.execute(
                    "UPDATE buckets SET reserved_usd=?,updated_at=? WHERE bucket_key=?",
                    (str(new_reserved), now, link["bucket_key"]),
                )
            c.execute(
                "UPDATE reservations SET status=?,updated_at=? WHERE id=?",
                (ReservationStatus.EXPIRED.value, now, rid),
            )
        return len(rows)

    def _applications_locked(self, c: sqlite3.Connection, ctx: BudgetContext):
        out = []
        for scope, subject in (
            (BudgetScope.RUN, ctx.run_id),
            (BudgetScope.TASK, ctx.task_id),
            (BudgetScope.PROJECT, ctx.project_id),
            (BudgetScope.DAILY_GLOBAL, ctx.day_utc),
        ):
            if subject is None:
                continue
            policy = self._resolve_policy_locked(c, scope, str(subject))
            if policy and policy.enabled:
                out.append((policy, f"{scope.value}:{subject}", str(subject)))
        return out

    def _resolve_policy_locked(self, c, scope: BudgetScope, subject: str):
        row = c.execute(
            """SELECT * FROM policies WHERE scope=? AND subject IN (?, '*')
               ORDER BY CASE WHEN subject=? THEN 0 ELSE 1 END LIMIT 1""",
            (scope.value, subject, subject),
        ).fetchone()
        if not row or not row["enabled"]:
            return None
        return BudgetPolicy(
            scope, money(row["hard_limit_usd"]), row["subject"],
            Decimal(row["warning_fraction"]), HardLimitAction(row["hard_action"]),
            bool(row["enabled"]),
        )

    def _consume_override_locked(self, c, raw: str, fp: str,
                                 needed: Decimal, now: float) -> bool:
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        row = c.execute(
            "SELECT * FROM overrides WHERE token_hash=?", (token_hash,)
        ).fetchone()
        if not row:
            return False
        if row["used_at"] is not None or row["expires_at"] <= now:
            return False
        if row["context_fingerprint"] != fp or money(row["extra_usd"]) < needed:
            return False
        return c.execute(
            """UPDATE overrides SET used_at=? WHERE token_hash=? AND used_at IS NULL
               AND expires_at>?""",
            (now, token_hash, now),
        ).rowcount == 1

    @staticmethod
    def _reservation(row) -> Reservation:
        return Reservation(
            row["id"], row["idempotency_key"], row["context_fingerprint"],
            money(row["estimated_usd"]), ReservationStatus(row["status"]),
            float(row["expires_at"]),
        )
