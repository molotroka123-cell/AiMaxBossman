"""Opt-in node RPC authentication, using the existing FleetStore for replay state.

RPC keys are host-provisioned, pairwise credentials, NEVER evidence-signing keys.
TLS certificate fingerprints are observed from sockets, not forwarded headers.
No network registration, privilege grant or key import endpoint is provided.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac
import json
import math
import re
import sqlite3
import time
import uuid
from typing import Any, Mapping

from .store import FleetStore

MAX_PACKET_BYTES = 2 * 1024 * 1024
MAX_NONCES = 10000
ID = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
NONCE = re.compile(r"^[0-9a-f]{32}$")
DOMAIN = "bossman.fleet.rpc.v1"


class NodeAuthDenied(PermissionError):
    pass


class DispatchOutcomeUnknown(RuntimeError):
    """A previous dispatch may already have had effects; observation is required."""


def canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def decode_packet(raw: bytes) -> dict:
    if len(raw) > MAX_PACKET_BYTES:
        raise NodeAuthDenied("RPC packet exceeds size limit")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise NodeAuthDenied("duplicate JSON key")
            result[key] = value
        return result
    def constant(_):
        raise NodeAuthDenied("non-finite JSON number")
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
        if not isinstance(value, dict):
            raise NodeAuthDenied("RPC packet must be an object")
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise NodeAuthDenied("invalid RPC JSON") from exc


@dataclass(frozen=True)
class PeerCredential:
    peer_id: str
    key_id: str
    key: bytes = field(repr=False)
    certificate_sha256: str
    expires_at: float
    operations: frozenset[str] = frozenset({"probe"})

    def __post_init__(self):
        if not ID.fullmatch(self.peer_id) or not ID.fullmatch(self.key_id):
            raise ValueError("invalid node/key identifier")
        if not isinstance(self.key, bytes) or len(self.key) < 32:
            raise ValueError("node RPC key must contain at least 32 random bytes")
        if not FINGERPRINT.fullmatch(self.certificate_sha256):
            raise ValueError("peer certificate SHA256 required")
        if isinstance(self.expires_at, bool) or not math.isfinite(self.expires_at):
            raise ValueError("finite credential expiry required")
        if not self.operations or not self.operations <= {"probe", "dispatch"}:
            raise ValueError("explicit supported RPC scopes required")


class NodeAuthenticator:
    def __init__(self, local_id: str, store: FleetStore, credentials: tuple[PeerCredential, ...]):
        if not ID.fullmatch(local_id):
            raise ValueError("invalid local node identity")
        self.local_id, self.store = local_id, store
        self._keys = {(c.peer_id, c.key_id): c for c in credentials}
        if len(self._keys) != len(credentials) or any(c.peer_id == local_id for c in credentials):
            raise ValueError("duplicate or self-referential peer credential")

    def active(self, peer_id: str, key_id: str, operation: str, *, con=None, now=None) -> PeerCredential:
        now = time.time() if now is None else now
        key = self._keys.get((peer_id, key_id))
        purpose = operation.removesuffix("-response")
        if (key is None or purpose not in key.operations or not math.isfinite(now)
                or key.expires_at <= now):
            raise NodeAuthDenied("unknown, expired or wrong-scope node credential")
        if con is None:
            with self.store.connect() as connection:
                revoked = connection.execute("SELECT 1 FROM fleet_rpc_revoked WHERE peer_id=? AND key_id=?",
                                              (peer_id, key_id)).fetchone()
        else:
            revoked = con.execute("SELECT 1 FROM fleet_rpc_revoked WHERE peer_id=? AND key_id=?",
                                  (peer_id, key_id)).fetchone()
        if revoked:
            raise NodeAuthDenied("node credential was revoked")
        return key

    def revoke(self, peer_id: str, key_id: str) -> None:
        """Trusted owner control only; persisted revocation survives a stale key file."""
        with self.store.connect() as con:
            con.execute("INSERT OR IGNORE INTO fleet_rpc_revoked VALUES(?,?)", (peer_id, key_id))

    def issue(self, peer_id: str, key_id: str, operation: str, payload: dict, *, now=None) -> dict:
        now = time.time() if now is None else now
        key = self.active(peer_id, key_id, operation, now=now)
        packet = {"domain": DOMAIN, "sender": self.local_id, "recipient": peer_id,
            "key_id": key_id, "operation": operation, "nonce": uuid.uuid4().hex,
            "issued_at": now, "expires_at": min(now + 30, key.expires_at), "payload": payload}
        body = canonical(packet)
        if len(body) > MAX_PACKET_BYTES - 80:
            raise NodeAuthDenied("RPC packet exceeds size limit")
        packet["mac"] = hmac.new(key.key, body, hashlib.sha256).hexdigest()
        return packet

    def verify(self, packet: dict, *, peer_id: str, operation: str,
               observed_certificate_sha256: str, now=None) -> dict:
        """Authenticate direction, scope, TLS principal, freshness and durable nonce."""
        now = time.time() if now is None else now
        fields = {"domain", "sender", "recipient", "key_id", "operation", "nonce", "issued_at", "expires_at", "payload", "mac"}
        if (set(packet) != fields or packet.get("domain") != DOMAIN
                or packet.get("sender") != peer_id or packet.get("recipient") != self.local_id
                or packet.get("operation") != operation or not isinstance(packet.get("payload"), dict)):
            raise NodeAuthDenied("RPC identity, direction or schema mismatch")
        if not isinstance(packet.get("key_id"), str):
            raise NodeAuthDenied("invalid RPC key identifier")
        key = self.active(peer_id, packet["key_id"], operation, now=now)
        if not hmac.compare_digest(key.certificate_sha256, observed_certificate_sha256):
            raise NodeAuthDenied("TLS peer does not match provisioned node identity")
        issued, expires = packet["issued_at"], packet["expires_at"]
        if (any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                for v in (issued, expires, now)) or not issued <= now < expires
                or not 0 < expires - issued <= 30):
            raise NodeAuthDenied("expired or future RPC request")
        if not isinstance(packet["nonce"], str) or not NONCE.fullmatch(packet["nonce"]):
            raise NodeAuthDenied("invalid RPC nonce")
        body = canonical({k: v for k, v in packet.items() if k != "mac"})
        mac = packet["mac"]
        if (len(body) > MAX_PACKET_BYTES or not isinstance(mac, str)
                or not FINGERPRINT.fullmatch(mac)
                or not hmac.compare_digest(mac, hmac.new(key.key, body, hashlib.sha256).hexdigest())):
            raise NodeAuthDenied("RPC signature mismatch")
        with self.store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                self.active(peer_id, packet["key_id"], operation, con=con, now=now)
                floor = con.execute("SELECT value FROM fleet_rpc_clock WHERE id=1").fetchone()
                if floor and now < floor[0] - 1:
                    raise NodeAuthDenied("clock rollback: remote execution unavailable")
                con.execute("INSERT INTO fleet_rpc_clock VALUES(1,?) ON CONFLICT(id) DO UPDATE SET value=max(value,excluded.value)", (now,))
                con.execute("DELETE FROM fleet_rpc_nonces WHERE expires_at<=?", (now - 1,))
                if con.execute("SELECT count(*) FROM fleet_rpc_nonces").fetchone()[0] >= MAX_NONCES:
                    raise NodeAuthDenied("RPC replay ledger capacity reached")
                con.execute("INSERT INTO fleet_rpc_nonces VALUES(?,?,?,?)", (peer_id, packet["key_id"], packet["nonce"], expires))
                con.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                con.execute("ROLLBACK")
                raise NodeAuthDenied("RPC nonce already consumed") from exc
            except BaseException:
                con.execute("ROLLBACK")
                raise
        return packet["payload"]

    def authorize_dispatch(self, logical_id: str, request_digest: str) -> None:
        """Trusted controller writes exact payload admission to canonical authority."""
        with self.store.connect() as con:
            con.execute("INSERT OR IGNORE INTO fleet_rpc_dispatches VALUES(?, 'authorized', ?)",
                        (logical_id, request_digest))
            row = con.execute("SELECT * FROM fleet_rpc_dispatches WHERE logical_id=?", (logical_id,)).fetchone()
            if row["request_digest"] != request_digest:
                raise NodeAuthDenied("dispatch intent changed within the same lease")
            if row["status"] != "authorized":
                raise DispatchOutcomeUnknown("dispatch already accepted; reconcile before retry")

    def claim_dispatch(self, logical_id: str, request_digest: str) -> None:
        """Consume exact durable admission BEFORE effects; never auto-expire intent."""
        with self.store.connect() as con:
            changed = con.execute("UPDATE fleet_rpc_dispatches SET status='uncertain' "
                "WHERE logical_id=? AND request_digest=? AND status='authorized'",
                (logical_id, request_digest)).rowcount
            if not changed:
                raise DispatchOutcomeUnknown("dispatch lacks unused exact admission; reconcile before any effect")

    def finish_dispatch(self, logical_id: str) -> None:
        with self.store.connect() as con:
            con.execute("UPDATE fleet_rpc_dispatches SET status='returned' WHERE logical_id=?", (logical_id,))
