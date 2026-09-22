"""Small durable inbox and private conversation memory; never modifies Bossman's DB."""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from bcc.secrets import Vault
from .config import CompanionError


class Store:
    def __init__(self, home: Path):
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.vault = Vault(home)
        self.path = home / "companion.sqlite3"
        self.db = sqlite3.connect(self.path, timeout=2, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS inbox(id INTEGER PRIMARY KEY, who TEXT NOT NULL,
          body TEXT NOT NULL, lane TEXT NOT NULL DEFAULT 'chat', phase TEXT NOT NULL DEFAULT 'pending', created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY, who TEXT NOT NULL,
          body TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS learning_log(id INTEGER PRIMARY KEY, who TEXT NOT NULL,
          body TEXT NOT NULL, created REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS learning_log_who ON learning_log(who, id);
        CREATE TABLE IF NOT EXISTS profiles(who TEXT PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY, who TEXT NOT NULL,
          body TEXT NOT NULL, expires REAL NOT NULL, phase TEXT NOT NULL DEFAULT 'pending',
          task_id INTEGER);
        ''')
        os.chmod(self.path, 0o600)

    @contextmanager
    def tx(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def close(self):
        self.db.close()

    def seal(self, value) -> str:
        return self.vault.encrypt(json.dumps(value, ensure_ascii=False))

    def open(self, value: str):
        data = self.vault.decrypt(value)
        if data is None:
            raise CompanionError("STATE_DECRYPTION_FAILED")
        return json.loads(data)

    def get(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return self.open(row[0]) if row else default

    def put(self, key: str, value):
        self.db.execute("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, self.seal(value)))

    def ingest(self, update_id: int, who: str | None, body: dict | None) -> bool:
        """Durably acknowledge only after accepting or explicitly refusing the update."""
        with self.tx():
            if update_id < self.get("offset", 0):
                return False
            count = self.db.execute("SELECT count(*) FROM inbox WHERE phase='pending'").fetchone()[0]
            lane = self.lane(body) if body else "chat"
            per_user = self.db.execute("SELECT count(*) FROM inbox WHERE who=? AND lane=? AND phase='pending'", (who, lane)).fetchone()[0]
            accepted = False
            if who and body is not None and count < 64 and per_user < 4:
                cursor = self.db.execute("INSERT OR IGNORE INTO inbox(id,who,body,lane,created) VALUES(?,?,?,?,?)",
                                         (update_id, who, self.seal(body), self.lane(body), time.time()))
                accepted = cursor.rowcount == 1
            self.put("offset", update_id + 1)
            return accepted

    def recover(self):
        # Side effects/Telegram sends may already have happened. Do not replay them.
        self.db.execute("UPDATE inbox SET phase='interrupted_unknown' WHERE phase='processing'")
        self.db.execute("UPDATE proposals SET phase='dispatch_unknown' WHERE phase='dispatching'")

    @staticmethod
    def lane(body: dict) -> str:
        if body.get("_image"):
            return "chat"
        command = str(body.get("text", "")).strip().partition(" ")[0].lower()
        if command in {"/status", "/help", "/lock", "/watch", "/cloud", "/model", "/cancel", "/menu", "/start", "/photo",
                       "/privacy", "/forget", "/forget_confirm", "/pause_learning", "/resume_learning"}:
            return "control"
        # "/best" or "/fast" alone only switches the route; with a question it is chat.
        bare = not str(body.get("text", "")).strip().partition(" ")[2].strip()
        return "control" if command in {"/best", "/fast"} and bare else "chat"

    def claim(self, who: str, lane: str = "chat"):
        with self.tx():
            row = self.db.execute("SELECT * FROM inbox WHERE who=? AND lane=? AND phase='pending' ORDER BY id LIMIT 1", (who, lane)).fetchone()
            if not row:
                return None
            self.db.execute("UPDATE inbox SET phase='processing' WHERE id=?", (row['id'],))
        return row['id'], self.open(row['body'])

    def finish(self, update_id: int, phase: str):
        if phase not in {"done", "failed", "delivery_unknown"}:
            raise ValueError("invalid inbox terminal state")
        self.db.execute("UPDATE inbox SET phase=? WHERE id=? AND phase='processing'", (phase, update_id))

    def remember(self, who: str, user: str, assistant: str):
        with self.tx():
            self.db.execute("INSERT INTO history(who,body,created) VALUES(?,?,?)",
                            (who, self.seal([user[:4000], assistant[:4000]]), time.time()))
            self.db.execute("DELETE FROM history WHERE who=? AND id NOT IN (SELECT id FROM history WHERE who=? ORDER BY id DESC LIMIT 4)", (who, who))

    def history(self, who: str):
        rows = self.db.execute("SELECT body FROM history WHERE who=? ORDER BY id", (who,)).fetchall()
        pairs = [self.open(r[0]) for r in rows]
        messages = []
        remaining = 8000
        for user, assistant in reversed(pairs):
            if len(user) + len(assistant) > remaining:
                break
            messages[0:0] = [{"role": "user", "content": user}, {"role": "assistant", "content": assistant}]
            remaining -= len(user) + len(assistant)
        return messages

    # ---- local per-user learning: every query is keyed by exactly one principal
    def log(self, who: str, user: str, assistant: str) -> int:
        cur = self.db.execute("INSERT INTO learning_log(who,body,created) VALUES(?,?,?)",
                              (who, self.seal({"user": user[:4000], "assistant": assistant[:8000]}), time.time()))
        return cur.lastrowid

    def log_entries(self, who: str, *, after_id: int = 0, limit: int = 1000) -> list:
        rows = self.db.execute("SELECT id, body, created FROM learning_log WHERE who=? AND id>? ORDER BY id DESC LIMIT ?",
                               (who, after_id, limit)).fetchall()
        return [{"id": r[0], "ts": r[2], **self.open(r[1])} for r in reversed(rows)]

    def log_count(self, who: str, after_id: int = 0) -> int:
        return self.db.execute("SELECT count(*) FROM learning_log WHERE who=? AND id>?", (who, after_id)).fetchone()[0]

    def learners(self) -> list:
        return [r[0] for r in self.db.execute("SELECT DISTINCT who FROM learning_log").fetchall()]

    def profile(self, who: str):
        row = self.db.execute("SELECT body FROM profiles WHERE who=?", (who,)).fetchone()
        return self.open(row[0]) if row else None

    def put_profile(self, who: str, text: str, last_log_id: int, *, edited_by_owner: bool = False) -> dict:
        old = self.profile(who) or {}
        value = {"text": text[:2000], "version": int(old.get("version", 0)) + 1, "updated": time.time(),
                 "last_log_id": last_log_id, "edited_by_owner": edited_by_owner}
        self.db.execute("INSERT INTO profiles VALUES (?,?) ON CONFLICT(who) DO UPDATE SET body=excluded.body",
                        (who, self.seal(value)))
        return value

    def delete_profile(self, who: str):
        self.db.execute("DELETE FROM profiles WHERE who=?", (who,))

    def prune_learning(self, retention_days: int):
        self.db.execute("DELETE FROM learning_log WHERE created<?", (time.time() - retention_days * 86400,))

    def forget(self, who: str):
        with self.tx():
            self.db.execute("DELETE FROM learning_log WHERE who=?", (who,))
            self.db.execute("DELETE FROM profiles WHERE who=?", (who,))
            self.db.execute("DELETE FROM history WHERE who=?", (who,))
            self.put("cloud:" + who, False)
            self.db.execute("UPDATE inbox SET body=? WHERE who=? AND phase NOT IN ('pending','processing')", (self.seal({}), who))
            self.db.execute("DELETE FROM proposals WHERE who=? AND phase='pending'", (who,))

    def propose(self, who: str, payload: dict) -> str:
        self.db.execute("DELETE FROM proposals WHERE phase='pending' AND expires<?", (time.time(),))
        count = self.db.execute("SELECT count(*) FROM proposals WHERE who=?", (who,)).fetchone()[0]
        if count >= 1000:
            raise CompanionError("LOCAL_PROPOSAL_LIMIT_REACHED")
        nonce = secrets.token_hex(6)
        self.db.execute("INSERT INTO proposals(id,who,body,expires) VALUES(?,?,?,?)",
                        (nonce, who, self.seal(payload), time.time() + 300))
        return nonce

    def consume(self, who: str, nonce: str):
        with self.tx():
            row = self.db.execute("SELECT * FROM proposals WHERE id=? AND who=? AND phase='pending' AND expires>?",
                                  (nonce, who, time.time())).fetchone()
            if not row:
                raise CompanionError("PROPOSAL_EXPIRED_OR_USED")
            self.db.execute("UPDATE proposals SET phase='dispatching' WHERE id=?", (nonce,))
        return self.open(row['body'])

    def delegated(self, who: str, nonce: str, task_id: int | None, identity: str | None = None):
        if identity is not None:
            row = self.db.execute("SELECT body FROM proposals WHERE id=? AND who=? AND phase='dispatching'", (nonce, who)).fetchone()
            if row:
                body = self.open(row[0])
                body["task_identity"] = identity
                self.db.execute("UPDATE proposals SET body=? WHERE id=? AND who=?", (self.seal(body), nonce, who))
        self.db.execute("UPDATE proposals SET task_id=?,phase=? WHERE id=? AND who=? AND phase='dispatching'",
                        (task_id, "submitted" if task_id is not None else "dispatch_unknown", nonce, who))

    def owns_task(self, who: str, task_id: int) -> bool:
        return self.db.execute("SELECT 1 FROM proposals WHERE who=? AND task_id=? AND phase='submitted'", (who, task_id)).fetchone() is not None

    def task_binding(self, who: str, task_id: int) -> str:
        row = self.db.execute("SELECT body FROM proposals WHERE who=? AND task_id=? AND phase='submitted' ORDER BY rowid DESC LIMIT 1", (who, task_id)).fetchone()
        identity = self.open(row[0]).get("task_identity") if row else None
        if not isinstance(identity, str) or len(identity) != 64:
            raise CompanionError("TASK_IDENTITY_UNVERIFIED")
        return identity

    def prune(self):
        cutoff = time.time() - 7 * 86400
        with self.tx():
            self.db.execute("DELETE FROM history WHERE created<?", (cutoff,))
            self.db.execute("DELETE FROM inbox WHERE phase NOT IN ('pending','processing') AND created<?", (cutoff,))
            self.db.execute("DELETE FROM proposals WHERE phase='pending' AND expires<?", (time.time(),))


@contextmanager
def single_instance(home: Path):
    """Kernel-owned lock: released on process death, no stale lockfile guessing."""
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    file = (home / "poller.lock").open("a+b")
    try:
        if file.seek(0, 2) == 0:
            file.write(b"0")
            file.flush()
        file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise CompanionError("ANOTHER_COMPANION_IS_RUNNING") from None
        yield
    finally:
        file.close()
