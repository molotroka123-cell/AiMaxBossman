"""Independent journal high-water marks in a host's EXISTING canonical database.

No new database, policy authority or execution ledger. The host supplies fresh
SQLite connections to its canonical store, and applies the additive schema.
Protect this table from the model and exclude it from journal-only restores.
Restoring this database AND the journal can still roll back history; a TPM or
external monotonic witness is needed for that stronger threat model.
"""
from __future__ import annotations

from contextlib import contextmanager
import re
import sqlite3
from typing import Callable, Iterator, Protocol

SCHEMA = """
CREATE TABLE IF NOT EXISTS org_journal_heads (
    namespace TEXT NOT NULL,
    task_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK (generation > 0),
    snapshot_sha256 TEXT NOT NULL,
    PRIMARY KEY (namespace, task_id)
);
"""
_HASH = re.compile(r"[0-9a-f]{64}\Z")


class AnchorConflict(ValueError):
    """Rollback, stale writer, missing witness or uninitialized migration."""


class JournalAnchorPort(Protocol):
    def check(self, task_id: str, snapshot_sha256: str) -> None: ...
    def advance(self, task_id: str, expected: str | None, new: str) -> None: ...


class SQLiteJournalAnchor:
    """CAS-only adapter. A connection belongs to this call and is always closed.

    ``connect`` must return a NEW connection to trusted durable storage. It must
    not return an already-open transaction: accepting it could acknowledge an
    anchor which the caller subsequently rolls back. No reset/delete API exists.
    """

    def __init__(self, connect: Callable[[], sqlite3.Connection], *, namespace: str):
        if not callable(connect) or type(namespace) is not str or not namespace.strip() or len(namespace) > 200:
            raise ValueError("canonical connection factory and bounded namespace required")
        if namespace != namespace.strip() or "\x00" in namespace:
            raise ValueError("invalid anchor namespace")
        self._connect = connect
        self.namespace = namespace

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            con = self._connect()
        except sqlite3.Error as exc:
            raise AnchorConflict("canonical journal anchor connection unavailable") from exc
        try:
            if con.in_transaction:
                raise AnchorConflict("anchor requires an independent committed transaction")
            # The durability of an effect intent cannot be weaker than the
            # fsynced journal. This applies only to this owned connection.
            con.execute("PRAGMA synchronous=FULL")
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except sqlite3.Error as exc:
            con.rollback()
            raise AnchorConflict("canonical journal anchor unavailable; reconcile before execution") from exc
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    @staticmethod
    def _validate(task_id: str, *hashes: str | None) -> None:
        if type(task_id) is not str or not task_id or len(task_id) > 200 or "\x00" in task_id:
            raise AnchorConflict("invalid anchor task identity")
        if any(h is not None and (type(h) is not str or not _HASH.fullmatch(h)) for h in hashes):
            raise AnchorConflict("full lowercase snapshot digest required")

    def check(self, task_id: str, snapshot_sha256: str) -> None:
        self._validate(task_id, snapshot_sha256)
        if snapshot_sha256 is None:
            raise AnchorConflict("snapshot digest is missing")
        with self._transaction() as con:
            row = con.execute(
                "SELECT snapshot_sha256 FROM org_journal_heads WHERE namespace=? AND task_id=?",
                (self.namespace, task_id),
            ).fetchone()
            if row is None or row[0] != snapshot_sha256:
                raise AnchorConflict("journal differs from canonical high-water mark; reconciliation required")

    def advance(self, task_id: str, expected: str | None, new: str) -> None:
        self._validate(task_id, expected, new)
        if new is None or new == expected:
            raise AnchorConflict("new distinct snapshot digest required")
        with self._transaction() as con:
            row = con.execute(
                "SELECT generation, snapshot_sha256 FROM org_journal_heads WHERE namespace=? AND task_id=?",
                (self.namespace, task_id),
            ).fetchone()
            if expected is None:
                if row is not None:
                    raise AnchorConflict("journal identity already anchored; do not recreate missing history")
                con.execute("INSERT INTO org_journal_heads VALUES (?, ?, 1, ?)",
                            (self.namespace, task_id, new))
            else:
                if row is None or row[1] != expected:
                    raise AnchorConflict("stale journal writer or rolled-back snapshot")
                changed = con.execute(
                    "UPDATE org_journal_heads SET generation=generation+1, snapshot_sha256=? "
                    "WHERE namespace=? AND task_id=? AND generation=? AND snapshot_sha256=?",
                    (new, self.namespace, task_id, row[0], expected),
                ).rowcount
                if changed != 1:
                    raise AnchorConflict("journal anchor compare-and-swap failed")
