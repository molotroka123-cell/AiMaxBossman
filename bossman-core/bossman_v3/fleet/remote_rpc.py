"""Opt-in mTLS RPC over a host-provisioned canonical lease/dispatch authority.

Experimental, NOT production certification. There is no automatic listener,
remote enrollment, policy grant, credential import, retry or evidence signing.
The reference node must access the SAME transactional authority as its controller;
an independently copied SQLite database is not a distributed lease authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import http.client
import ipaddress
import json
import math
from pathlib import Path
import socket
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Mapping

from ..organization.contracts import DelegationContract
from ..organization.models import Resources, WorkResult
from .node_agent import LocalNodeTransport, NodeExecutionRequest, NodeUnavailable
from .remote_auth import (MAX_PACKET_BYTES, DispatchOutcomeUnknown, NodeAuthDenied,
                          NodeAuthenticator, canonical, decode_packet)


def _secure(context: ssl.SSLContext, *, client: bool) -> None:
    if (context.verify_mode != ssl.CERT_REQUIRED
            or context.minimum_version < ssl.TLSVersion.TLSv1_2
            or (client and not context.check_hostname)
            or context.keylog_filename):
        raise ValueError("verified TLS >= 1.2, hostname verification and no TLS key logging required")


def _ip(host: str) -> str:
    address = ipaddress.ip_address(host)
    if address.is_unspecified or address.is_multicast:
        raise ValueError("an explicit unicast peer address is required")
    return str(address)


@dataclass(frozen=True)
class NodeEndpoint:
    host: str
    port: int
    key_id: str
    tls: ssl.SSLContext = field(repr=False)
    privacy_levels: frozenset[str] = frozenset({"public"})

    def __post_init__(self):
        _ip(self.host)                 # Literal, owner-configured IP; no DNS/proxy/URL routing.
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("invalid node port")
        _secure(self.tls, client=True)
        if not self.privacy_levels or not self.privacy_levels <= {"public", "private"}:
            raise ValueError("explicit remote privacy classes required; LOCAL_ONLY is forbidden")


def _request_dict(request: NodeExecutionRequest) -> dict:
    body = {"work_id": request.work_id, "mission_id": request.mission_id,
            "agent_id": request.agent_id, "lease_id": request.lease_id,
            "fence": request.fence, "contract": request.contract.to_dict(),
            "timeout_seconds": request.timeout_seconds, "context_policy": request.context_policy}
    _request(body)                    # Validate before authorizing, connecting or disclosing data.
    return body


def _request(body: dict) -> NodeExecutionRequest:
    from .remote_auth import ID
    fields = {"work_id", "mission_id", "agent_id", "lease_id", "fence", "contract", "timeout_seconds", "context_policy"}
    if set(body) != fields or any(not isinstance(body[k], str) or not ID.fullmatch(body[k])
                                  for k in ("work_id", "mission_id", "agent_id", "lease_id")):
        raise NodeAuthDenied("invalid dispatch identity/schema")
    timeout = body["timeout_seconds"]
    if (type(body["fence"]) is not int or body["fence"] < 1
            or isinstance(timeout, bool) or not isinstance(timeout, (float, int))
            or not math.isfinite(timeout) or not 0 < timeout <= 600
            or body["context_policy"] not in ("FULL", "MINIMIZED")):
        raise NodeAuthDenied("invalid dispatch fence, timeout or context policy")
    raw = body["contract"]
    if not isinstance(raw, dict) or type(raw.get("side_effect")) is not bool:
        raise NodeAuthDenied("invalid contract effect type")
    try:
        contract = DelegationContract.from_dict(raw)
        if contract.problems() or contract.work_id != body["work_id"] or contract.mission_id != body["mission_id"]:
            raise ValueError("invalid delegation")
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        raise NodeAuthDenied("invalid delegation contract") from exc
    if contract.privacy not in ("public", "private"):
        raise NodeAuthDenied("LOCAL_ONLY cannot be dispatched remotely")
    return NodeExecutionRequest(body["work_id"], body["mission_id"], body["agent_id"],
                                body["lease_id"], body["fence"], contract, timeout, body["context_policy"])


def _identity(node_id: str, request: NodeExecutionRequest) -> str:
    # Key rotation/new nonce/changed arguments must not create another execution slot.
    return hashlib.sha256(canonical({"node": node_id, "mission": request.mission_id,
        "work": request.work_id, "lease": request.lease_id, "fence": request.fence})).hexdigest()


def _lease(leases, node_id: str, request: NodeExecutionRequest):
    if leases is None:
        raise NodeAuthDenied("canonical lease authority is unavailable")
    lease = next((l for l in leases.store.leases(node_id=node_id)
                  if l.lease_id == request.lease_id and l.work_id == request.work_id
                  and l.fence == request.fence), None)
    if lease is None or not leases.valid(lease, now=time.time())[0]:
        raise NodeAuthDenied("no current dispatch lease")
    return lease


def _admit(leases, node_id: str, request: NodeExecutionRequest, *, con=None):
    """Reuse canonical privacy decisions on controller AND at the effect boundary."""
    from .privacy import PrivacyRouter
    from .models import NodeState
    from .node_agent import _minimized
    if con is None:
        node = leases.store.node(node_id)
    else:
        row = con.execute("SELECT payload FROM fleet_nodes WHERE node_id=?", (node_id,)).fetchone()
        node = NodeState.from_dict(json.loads(row[0])) if row else None
    if node is None:
        raise NodeAuthDenied("node is not registered by the host")
    decision = PrivacyRouter().decide(requested_privacy=request.contract.privacy, node=node,
        contains_secrets=bool(request.contract.placement.get("contains_secrets")))
    if not decision.allowed or request.context_policy != decision.context_policy:
        raise NodeAuthDenied("canonical privacy policy refuses this node/context")
    if decision.context_policy == "MINIMIZED":
        # Validate before sending even one byte; stripping context after arrival is too late.
        _minimized(request.contract)


class RpcNodeClient:
    def __init__(self, auth: NodeAuthenticator, endpoints: Mapping[str, NodeEndpoint], *, leases=None):
        self.auth, self.endpoints, self.leases = auth, dict(endpoints), leases

    def _rpc(self, node_id: str, operation: str, payload: dict, timeout: float) -> dict:
        endpoint = self.endpoints.get(node_id)
        if endpoint is None:
            raise NodeAuthDenied("node endpoint not provisioned")
        _secure(endpoint.tls, client=True)  # SSLContext is mutable; recheck on every connection.
        key = self.auth.active(node_id, endpoint.key_id, operation)
        conn = http.client.HTTPSConnection(_ip(endpoint.host), endpoint.port,
                                           timeout=timeout, context=endpoint.tls)
        transport_socket = None
        def interrupt():
            if transport_socket is not None:
                try:
                    transport_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            conn.close()
        timer = threading.Timer(timeout, interrupt)
        timer.daemon = True
        timer.start()
        try:
            conn.connect()
            transport_socket = conn.sock
            observed = hashlib.sha256(transport_socket.getpeercert(binary_form=True)).hexdigest()
            if observed != key.certificate_sha256:
                raise NodeAuthDenied("server certificate does not match node pin")
            # The body is never transmitted before both CA/SAN and pin checks.
            packet = self.auth.issue(node_id, endpoint.key_id, operation, payload)
            encoded = canonical(packet)
            conn.request("POST", "/fleet/v1/" + operation, body=encoded,
                         headers={"Content-Type": "application/json", "Connection": "close"})
            response = conn.getresponse()
            if response.status != 200:
                if response.status == 409:
                    raise DispatchOutcomeUnknown("remote dispatch already accepted; reconcile before retry")
                raise NodeUnavailable("node rejected RPC; no automatic retry")
            if (response.getheader("Content-Type") != "application/json"
                    or response.getheader("Transfer-Encoding") or response.getheader("Content-Encoding")):
                raise NodeAuthDenied("unsupported RPC response framing")
            length = response.getheader("Content-Length", "")
            if not length.isdigit() or not 0 < int(length) <= MAX_PACKET_BYTES:
                raise NodeAuthDenied("invalid RPC response length")
            raw = response.read(int(length) + 1)
            if len(raw) != int(length):
                raise NodeAuthDenied("truncated RPC response")
            result = self.auth.verify(decode_packet(raw), peer_id=node_id,
                operation=operation + "-response", observed_certificate_sha256=observed)
            if (result.get("request_nonce") != packet["nonce"]
                    or result.get("request_sha256") != hashlib.sha256(encoded).hexdigest()):
                raise NodeAuthDenied("response belongs to another request")
            return result
        except NodeAuthDenied:
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise NodeUnavailable("RPC interrupted: effect outcome unknown; do not retry automatically") from exc
        finally:
            timer.cancel()
            conn.close()

    def dispatch(self, node_id: str, request: NodeExecutionRequest) -> WorkResult:
        body = _request_dict(request)
        endpoint = self.endpoints.get(node_id)
        if endpoint is None or request.contract.privacy not in endpoint.privacy_levels:
            raise NodeAuthDenied("data privacy does not permit this remote endpoint")
        _lease(self.leases, node_id, request)
        _admit(self.leases, node_id, request)
        if Path(self.auth.store.path).resolve() != Path(self.leases.store.path).resolve():
            raise NodeAuthDenied("controller authentication and lease authority must share canonical store")
        self.auth.active(node_id, endpoint.key_id, "dispatch")
        # Owner-side durable exact-payload admission, before any network effects.
        self.auth.authorize_dispatch(_identity(node_id, request), hashlib.sha256(canonical(body)).hexdigest())
        reply = self._rpc(node_id, "dispatch", body, request.timeout_seconds)
        raw = reply.get("result")
        if (not isinstance(raw, dict) or type(raw.get("executed")) is not bool
                or type(raw.get("success")) is not bool or not isinstance(raw.get("evidence"), list)
                or any(not isinstance(e, dict) or type(e.get("verified")) is not bool for e in raw["evidence"])
                or raw.get("work_id") != request.work_id or raw.get("produced_by") != request.agent_id):
            raise NodeAuthDenied("invalid result identity or schema")
        try:
            result = WorkResult.from_dict(raw)
            if any(not math.isfinite(getattr(result.cost, f)) or getattr(result.cost, f) < 0
                   for f in Resources._FIELDS):
                raise ValueError("invalid result cost")
        except (ValueError, TypeError, KeyError) as exc:
            raise NodeAuthDenied("invalid result payload") from exc
        # RPC authentication never grants verification or review authority.
        result.success, result.reviewed_by = False, ""
        return result

    def probe(self, node_id: str) -> bool:
        try:
            return self._rpc(node_id, "probe", {}, 5).get("available") is True
        except (NodeAuthDenied, NodeUnavailable, DispatchOutcomeUnknown):
            return False

    def cancel(self, node_id: str, work_id: str) -> bool:
        return False                   # No remotely proven cooperative cancellation contract yet.


class RemoteNodeGateway:
    def __init__(self, node_id: str, auth: NodeAuthenticator, transport: LocalNodeTransport, *,
                 controllers: frozenset[str], privacy_levels: frozenset[str] = frozenset({"public"})):
        if (auth.local_id != node_id or transport.leases is None or not controllers
                or Path(auth.store.path).resolve() != Path(transport.leases.store.path).resolve()
                or not privacy_levels or not privacy_levels <= {"public", "private"}):
            raise ValueError("node requires host-provisioned identity, controllers and canonical authority")
        self.node_id, self.auth, self.transport = node_id, auth, transport
        self.controllers, self.privacy_levels = frozenset(controllers), frozenset(privacy_levels)

    def handle(self, raw: bytes, operation: str, peer_certificate_sha256: str) -> bytes:
        if operation not in ("probe", "dispatch"):
            raise NodeAuthDenied("unsupported operation")
        packet = decode_packet(raw)
        peer = packet.get("sender")
        if not isinstance(peer, str) or peer not in self.controllers:
            raise NodeAuthDenied("controller not provisioned")
        body = self.auth.verify(packet, peer_id=peer, operation=operation,
                                observed_certificate_sha256=peer_certificate_sha256)
        if operation == "probe":
            if body:
                raise NodeAuthDenied("probe payload must be empty")
            answer = {"available": self.transport.probe(self.node_id)}
        else:
            request = _request(body)
            if request.contract.privacy not in self.privacy_levels:
                raise NodeAuthDenied("node not cleared for privacy class")
            _lease(self.transport.leases, self.node_id, request)
            _admit(self.transport.leases, self.node_id, request)
            identity = _identity(self.node_id, request)
            self.auth.claim_dispatch(identity, hashlib.sha256(canonical(body)).hexdigest())
            deadline = time.monotonic() + request.timeout_seconds
            def authorized(con):
                self.auth.active(peer, packet["key_id"], operation, con=con)
                _admit(self.transport.leases, self.node_id, request, con=con)
                if time.monotonic() >= deadline:
                    raise NodeAuthDenied("dispatch deadline expired before effect")
            result = self.transport.dispatch(self.node_id, request, authorization_check=authorized)
            self.auth.finish_dispatch(identity)
            answer = {"result": result.to_dict()}
        answer.update(request_nonce=packet["nonce"], request_sha256=hashlib.sha256(raw).hexdigest())
        return canonical(self.auth.issue(peer, packet["key_id"], operation + "-response", answer))


def make_node_server(address: tuple[str, int], tls: ssl.SSLContext, gateway: RemoteNodeGateway):
    """Explicit reference listener; caller owns start/shutdown. Never auto-enabled.

    Bounded threads/handshake/body sizes. This stdlib listener is for controlled
    deployments/evaluation, not an Internet-facing production server.
    """
    _ip(address[0])
    _secure(tls, client=False)
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def setup(self):
            super().setup()
            def close_slow_reader():
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            self.body_timer = threading.Timer(15, close_slow_reader)
            self.body_timer.daemon = True
            self.body_timer.start()

        def finish(self):
            self.body_timer.cancel()
            super().finish()

        def log_message(self, format, *args):
            pass                      # Do not log arbitrary request URLs/payloads/credentials.

        def do_POST(self):
            operation = self.path.removeprefix("/fleet/v1/")
            lengths = self.headers.get_all("Content-Length", [])
            if (self.path not in ("/fleet/v1/probe", "/fleet/v1/dispatch")
                    or self.headers.get("Transfer-Encoding") or self.headers.get("Content-Encoding")
                    or self.headers.get("Content-Type") != "application/json"
                    or len(lengths) != 1 or not lengths[0].isdigit()
                    or not 0 < int(lengths[0]) <= MAX_PACKET_BYTES):
                self.reply(400, b'{"error":"invalid_rpc_request"}')
                return
            try:
                raw = self.rfile.read(int(lengths[0]))
                if len(raw) != int(lengths[0]):
                    raise NodeAuthDenied("truncated RPC request")
                self.body_timer.cancel()
                # Derive identity from the verified TLS socket; forwarded headers are irrelevant.
                pin = hashlib.sha256(self.connection.getpeercert(binary_form=True)).hexdigest()
                self.reply(200, gateway.handle(raw, operation, pin))
            except NodeAuthDenied:
                self.reply(403, b'{"error":"node_auth_denied"}')
            except DispatchOutcomeUnknown:
                self.reply(409, b'{"error":"dispatch_reconciliation_required"}')
            except Exception:
                self.reply(503, b'{"error":"node_execution_unavailable"}')

        def reply(self, status: int, body: bytes):
            self.close_connection = True
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        request_queue_size = 8
        address_family = socket.AF_INET6 if ipaddress.ip_address(address[0]).version == 6 else socket.AF_INET
        def __init__(self):
            self.slots = threading.BoundedSemaphore(8)
            super().__init__(address, Handler)
        def process_request(self, request, client_address):
            if not self.slots.acquire(blocking=False):
                self.shutdown_request(request)
                return
            try:
                super().process_request(request, client_address)
            except BaseException:
                self.slots.release()
                raise
        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                self.slots.release()
        def finish_request(self, request, client_address):
            request.settimeout(10)
            _secure(tls, client=False)
            try:
                with tls.wrap_socket(request, server_side=True) as peer:
                    Handler(peer, client_address, self)
            except OSError:
                return
        def handle_error(self, request, client_address):
            pass                       # No traceback containing user input on the network perimeter.
    return Server()
