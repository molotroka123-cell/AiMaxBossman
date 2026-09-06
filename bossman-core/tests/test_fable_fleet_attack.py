"""AGENT-3 adversarial suite for the remote Fleet transport, node auth and fencing.

Unit tests passing is not production readiness. Everything here attacks the real
boundary: identity, mutual authentication, credential scope/expiry/revocation,
replay, request/lease binding, fencing, duplicate claims, interruption, restart,
capability and telemetry claims, PRIVATE locality, resource admission and
completion ownership. Each confirmed defect is attacked a second, different way.
"""
from __future__ import annotations

import hashlib
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from bossman_v3.fleet import FleetControlPlane
from bossman_v3.fleet.leases import StaleLease
from bossman_v3.fleet.models import CLOUD, Heartbeat, NodeStatus, PlacementRequirement, RetryPolicy
from bossman_v3.fleet.node_agent import NodeExecutionRequest, NodeUnavailable
from bossman_v3.fleet.queue import WorkQueue
from bossman_v3.fleet.remote_auth import (DispatchOutcomeUnknown, NodeAuthDenied, NodeAuthenticator,
                                          PeerCredential, canonical, decode_packet)
from bossman_v3.fleet.remote_rpc import (NodeEndpoint, RemoteNodeGateway, RpcNodeClient, _identity,
                                         _request_dict, make_node_server)
from bossman_v3.fleet.scheduler import FleetScheduler
from bossman_v3.fleet.store import FleetStore

from tests.test_fleet_remote_rpc import certificates
from tests.test_v3_fleet_e2e import World, _contract, _node, _node_bridge

SCOPES = frozenset({"probe", "dispatch"})


@pytest.fixture
def fleet(tmp_path):
    """Controller + node-1 behind a real loopback mTLS listener, plus an
    in-process node-2 gateway used to attack cross-node identity directly."""
    client_tls, server_tls, pins = certificates(tmp_path)
    plane = FleetControlPlane(tmp_path / "fleet.sqlite")
    for nid in ("node-1", "node-2"):
        plane.registry.register(_node(nid), now=time.time())
    world = World(tmp_path / "world")
    world.root.mkdir()
    local = plane.transport
    for nid in ("node-1", "node-2"):
        local.attach(nid, _node_bridge(world, nid, tmp_path / "journals"))

    key1, key2 = os.urandom(32), os.urandom(32)
    expiry = time.time() + 3600
    controller = NodeAuthenticator("controller", plane.store, (
        PeerCredential("node-1", "r1", key1, pins["node"], expiry, SCOPES),
        PeerCredential("node-2", "r1", key2, pins["node"], expiry, SCOPES)))
    node1 = NodeAuthenticator("node-1", plane.store,
                              (PeerCredential("controller", "r1", key1, pins["controller"], expiry, SCOPES),))
    node2 = NodeAuthenticator("node-2", plane.store,
                              (PeerCredential("controller", "r1", key2, pins["controller"], expiry, SCOPES),))
    gateway1 = RemoteNodeGateway("node-1", node1, local, controllers=frozenset({"controller"}))
    gateway2 = RemoteNodeGateway("node-2", node2, local, controllers=frozenset({"controller"}))
    server = make_node_server(("127.0.0.1", 0), server_tls, gateway1)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = NodeEndpoint("127.0.0.1", server.server_address[1], "r1", client_tls)
    client = RpcNodeClient(controller, {"node-1": endpoint,
                                        "node-2": replace(endpoint, port=server.server_address[1])},
                           leases=plane.leases)
    contract = _contract(world, "work", ["output.txt"], privacy="public")
    placement = plane.place(contract)
    assert placement.ok
    req = NodeExecutionRequest("work", "m1", "coder", placement.lease.lease_id,
                               placement.lease.fence, contract, 10)
    yield dict(tmp_path=tmp_path, plane=plane, world=world, local=local, pins=pins,
               controller=controller, node1=node1, node2=node2, gateway1=gateway1, gateway2=gateway2,
               server=server, endpoint=endpoint, client=client, contract=contract,
               placement=placement, req=req, client_tls=client_tls, keys=(key1, key2))
    server.shutdown()
    server.server_close()
    thread.join(3)
    assert not thread.is_alive()


# ------------------------------------------------------- identity & mutual auth

def test_node_identity_spoofing_recipient_is_bound(fleet):
    """A packet minted for node-1 is refused by node-2 even with a valid controller pin."""
    f = fleet
    packet = f["controller"].issue("node-1", "r1", "dispatch", _request_dict(f["req"]))
    with pytest.raises(NodeAuthDenied):
        f["gateway2"].handle(canonical(packet), "dispatch", f["pins"]["controller"])
    assert f["world"].side_effects() == 0


def test_credential_scope_one_node_key_does_not_authorize_another(fleet):
    """node-2's pairwise key must not authenticate work against node-1 (scope)."""
    f = fleet
    forged = f["controller"].issue("node-2", "r1", "dispatch", _request_dict(f["req"]))
    forged["recipient"] = "node-1"                       # re-address without the node-1 key
    with pytest.raises(NodeAuthDenied):
        f["gateway1"].handle(canonical(forged), "dispatch", f["pins"]["controller"])
    assert f["world"].side_effects() == 0


def test_node_authenticates_the_controller_not_only_the_reverse(fleet):
    """Mutual: an unprovisioned peer and a wrong TLS principal are both refused."""
    f = fleet
    stranger = NodeAuthenticator("stranger", FleetStore(f["tmp_path"] / "rogue.sqlite"),
                                 (PeerCredential("node-1", "r1", f["keys"][0], f["pins"]["node"],
                                                 time.time() + 60, SCOPES),))
    packet = stranger.issue("node-1", "r1", "probe", {})
    with pytest.raises(NodeAuthDenied, match="controller not provisioned"):
        f["gateway1"].handle(canonical(packet), "probe", f["pins"]["controller"])
    # Correct sender name, wrong TLS certificate: the socket principal decides.
    good = f["controller"].issue("node-1", "r1", "probe", {})
    with pytest.raises(NodeAuthDenied, match="TLS peer"):
        f["gateway1"].handle(canonical(good), "probe", f["pins"]["node"])


def test_controller_rejects_a_server_that_is_not_the_pinned_node(fleet, tmp_path):
    """Second angle on mutual auth: a CA-valid rogue listener gets no dispatch body."""
    f = fleet
    rogue_root = tmp_path / "rogue"
    rogue_root.mkdir()
    rogue_client, rogue_server, _ = certificates(rogue_root)
    seen: list[bytes] = []

    class Sink(threading.Thread):
        daemon = True

        def run(self):
            while True:
                try:
                    raw, _ = self.sock.accept()
                except OSError:
                    return
                try:
                    with rogue_server.wrap_socket(raw, server_side=True) as tls:
                        seen.append(tls.recv(4096))
                except OSError:
                    pass

    sink = Sink()
    sink.sock = socket.socket()
    sink.sock.bind(("127.0.0.1", 0))
    sink.sock.listen(4)
    port = sink.sock.getsockname()[1]
    sink.start()
    try:
        rogue = RpcNodeClient(f["controller"], {"node-1": NodeEndpoint("127.0.0.1", port, "r1", rogue_client)},
                              leases=f["plane"].leases)
        with pytest.raises(NodeAuthDenied, match="certificate does not match node pin"):
            rogue.dispatch("node-1", f["req"])
    finally:
        sink.sock.close()
    assert not any(b"work" in blob for blob in seen)
    assert f["world"].side_effects() == 0


# ------------------------------------------------- scope, expiry and revocation

def test_probe_scope_credential_cannot_dispatch(fleet):
    f = fleet
    limited = NodeAuthenticator("controller", f["plane"].store,
                                (PeerCredential("node-1", "probe-only", f["keys"][0], f["pins"]["node"],
                                                time.time() + 60, frozenset({"probe"})),))
    with pytest.raises(NodeAuthDenied):
        limited.issue("node-1", "probe-only", "dispatch", _request_dict(f["req"]))
    with pytest.raises(NodeAuthDenied):
        limited.active("node-1", "probe-only", "dispatch")


def test_expired_credential_is_refused_and_packets_cannot_outlive_it(fleet):
    f = fleet
    now = time.time()
    short = NodeAuthenticator("controller", f["plane"].store,
                              (PeerCredential("node-1", "short", f["keys"][0], f["pins"]["node"],
                                              now + 5, SCOPES),))
    packet = short.issue("node-1", "short", "probe", {}, now=now)
    assert packet["expires_at"] <= now + 5            # never outlives the credential
    with pytest.raises(NodeAuthDenied):
        short.issue("node-1", "short", "probe", {}, now=now + 6)
    node = NodeAuthenticator("node-1", f["plane"].store,
                             (PeerCredential("controller", "short", f["keys"][0], f["pins"]["controller"],
                                             now + 5, SCOPES),))
    with pytest.raises(NodeAuthDenied):
        node.verify(packet, peer_id="controller", operation="probe",
                    observed_certificate_sha256=f["pins"]["controller"], now=now + 6)


def test_revocation_takes_effect_at_use_time_not_issue_time(fleet):
    """A packet minted while the key was live must die the moment it is revoked."""
    f = fleet
    packet = f["controller"].issue("node-1", "r1", "probe", {})
    f["node1"].revoke("controller", "r1")
    with pytest.raises(NodeAuthDenied, match="revoked"):
        f["gateway1"].handle(canonical(packet), "probe", f["pins"]["controller"])


def test_revocation_stops_a_dispatch_that_is_already_in_flight(fleet, monkeypatch):
    """Second angle: revoked between admission and the effect => nothing is written."""
    f = fleet
    original = f["local"].dispatch

    def revoke_then_run(node_id, request, **kw):
        f["node1"].revoke("controller", "r1")
        return original(node_id, request, **kw)

    monkeypatch.setattr(f["local"], "dispatch", revoke_then_run)
    with pytest.raises(NodeUnavailable):
        f["client"].dispatch("node-1", f["req"])
    assert f["world"].side_effects() == 0


# ------------------------------------------------------ replay & request binding

def test_captured_request_cannot_be_replayed(fleet):
    f = fleet
    packet = f["controller"].issue("node-1", "r1", "probe", {})
    raw = canonical(packet)
    assert decode_packet(f["gateway1"].handle(raw, "probe", f["pins"]["controller"]))["payload"]["available"]
    with pytest.raises(NodeAuthDenied, match="consumed"):
        f["gateway1"].handle(raw, "probe", f["pins"]["controller"])


def test_signed_request_is_bound_to_mission_task_and_lease(fleet):
    """The MAC covers mission/work/lease/fence: no field may be re-pointed."""
    f = fleet
    body = _request_dict(f["req"])
    packet = f["controller"].issue("node-1", "r1", "dispatch", body)
    for field, value in (("work_id", "other"), ("mission_id", "other"),
                         ("lease_id", "other"), ("fence", 999)):
        tampered = dict(packet, payload={**body, field: value})
        with pytest.raises(NodeAuthDenied, match="signature mismatch"):
            f["gateway1"].handle(canonical(tampered), "dispatch", f["pins"]["controller"])
    assert f["world"].side_effects() == 0


def test_admission_slot_is_bound_to_one_lease_and_fence(fleet):
    """Second angle: a fresh, perfectly signed packet for another lease has no admission."""
    f = fleet
    other = f["plane"].leases.acquire(node_id="node-1", work_id="work", now=time.time(),
                                      ttl_seconds=600, resource_class="second")
    moved = replace(f["req"], lease_id=other.lease_id, fence=other.fence)
    assert _identity("node-1", moved) != _identity("node-1", f["req"])
    f["controller"].authorize_dispatch(_identity("node-1", f["req"]),
                                       hashlib.sha256(canonical(_request_dict(f["req"]))).hexdigest())
    packet = f["controller"].issue("node-1", "r1", "dispatch", _request_dict(moved))
    with pytest.raises(DispatchOutcomeUnknown):
        f["gateway1"].handle(canonical(packet), "dispatch", f["pins"]["controller"])
    assert f["world"].side_effects() == 0


# ------------------------------------------------------------ lease & fencing

def test_expired_lease_worker_cannot_write(fleet):
    f = fleet
    f["plane"].store.update_lease_expiry(f["placement"].lease.lease_id, time.time() - 1)
    with pytest.raises(NodeAuthDenied, match="no current dispatch lease"):
        f["client"].dispatch("node-1", f["req"])
    assert f["world"].side_effects() == 0


def test_stale_fence_is_refused_at_the_effect_boundary(fleet):
    """Second angle: the lease is replaced under a live worker; the mutation guard fences it."""
    f = fleet
    stale = f["placement"].lease
    f["plane"].leases.release(stale)
    fresh = f["plane"].leases.acquire(node_id="node-1", work_id="work", now=time.time(), ttl_seconds=600)
    assert fresh.fence > stale.fence
    with pytest.raises(StaleLease):
        with f["plane"].leases.mutation_guard(stale):
            pytest.fail("a superseded fence must never reach the effect")
    assert f["plane"].leases.valid(stale, now=time.time())[0] is False


def test_expired_lease_cannot_be_renewed_or_guarded(fleet):
    f = fleet
    lease = f["plane"].leases.acquire(node_id="node-2", work_id="w2", now=time.time(), ttl_seconds=1)
    f["plane"].store.update_lease_expiry(lease.lease_id, time.time() - 1)
    dead = replace(lease, expires_ts=time.time() - 1)
    with pytest.raises(StaleLease):
        f["plane"].leases.renew(dead, now=time.time(), ttl_seconds=60)
    with pytest.raises(StaleLease):
        with f["plane"].leases.mutation_guard(lease):
            pytest.fail("an expired lease must not authorize a mutation")


# --------------------------------------------- queue: duplicate claim & ownership

def _queue(plane) -> WorkQueue:
    return WorkQueue(plane.store, FleetScheduler(), plane.journal, RetryPolicy())


def test_duplicate_claim_of_one_task_has_exactly_one_winner(fleet):
    f = fleet
    q = _queue(f["plane"])
    assert q.enqueue("qw", "m1", priority=1, requirement=PlacementRequirement(
        capabilities=("fs.write",), privacy="private"))
    nodes = [f["plane"].registry.node("node-1"), f["plane"].registry.node("node-2")]
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda i: q.claim(nodes[i % 2]), range(8)))
    won = [c for c in claims if c is not None]
    assert len(won) == 1
    assert len({c.claim_fence for c in won}) == 1


def test_only_the_current_claim_holder_may_finish_the_task(fleet):
    f = fleet
    q = _queue(f["plane"])
    q.enqueue("qw", "m1", priority=1, requirement=PlacementRequirement(
        capabilities=("fs.write",), privacy="private"))
    first = q.claim(f["plane"].registry.node("node-1"))
    assert first is not None
    assert q.release(first)
    second = q.claim(f["plane"].registry.node("node-2"))
    assert second is not None and second.claim_fence > first.claim_fence
    assert q.complete(first) is False                      # zombie holder cannot finish
    assert q.complete(replace(second, node_id="node-1")) is False
    assert [row["work_id"] for row in f["plane"].store.queue()] == ["qw"]
    assert q.complete(second) is True
    assert f["plane"].store.queue() == []


def test_a_stale_claim_cannot_fail_or_requeue_someone_elses_task(fleet):
    """Second angle on completion ownership: failure handling is fenced too."""
    f = fleet
    q = _queue(f["plane"])
    q.enqueue("qw", "m1", priority=1, requirement=PlacementRequirement(
        capabilities=("fs.write",), privacy="private"))
    first = q.claim(f["plane"].registry.node("node-1"))
    q.release(first)
    q.claim(f["plane"].registry.node("node-2"))
    with pytest.raises(PermissionError, match="stale queue claim"):
        q.on_failure("qw", "m1", claim=first, reason="TimeoutError: boom")
    with pytest.raises(TypeError):
        q.complete(("qw", "node-1", 1))                    # tuples are not capabilities


# --------------------------------------- interruption, reconnect and restarts

def test_network_interruption_midrequest_is_ambiguous_and_never_auto_retried(fleet, monkeypatch):
    f = fleet
    original = f["gateway1"].handle

    def die_after_effect(raw, operation, pin):
        original(raw, operation, pin)
        raise ConnectionResetError("link dropped after the effect")

    monkeypatch.setattr(f["gateway1"], "handle", die_after_effect)
    with pytest.raises((NodeUnavailable, NodeAuthDenied)):
        f["client"].dispatch("node-1", f["req"])
    assert f["world"].side_effects() == 1
    monkeypatch.setattr(f["gateway1"], "handle", original)
    # Reconnect works, but the ambiguous dispatch stays EXPLICIT: no silent replay.
    assert f["client"].probe("node-1") is True
    with pytest.raises(DispatchOutcomeUnknown):
        f["client"].dispatch("node-1", f["req"])
    assert f["world"].side_effects() == 1


def test_node_restart_does_not_reopen_a_consumed_admission(fleet):
    f = fleet
    f["client"].dispatch("node-1", f["req"])
    assert f["world"].side_effects() == 1
    restarted = NodeAuthenticator("node-1", FleetStore(f["plane"].store.path),
                                  tuple(f["node1"]._keys.values()))
    gateway = RemoteNodeGateway("node-1", restarted, f["local"], controllers=frozenset({"controller"}))
    packet = f["controller"].issue("node-1", "r1", "dispatch", _request_dict(f["req"]))
    with pytest.raises(DispatchOutcomeUnknown):
        gateway.handle(canonical(packet), "dispatch", f["pins"]["controller"])
    assert f["world"].side_effects() == 1


def test_controller_restart_does_not_reissue_a_spent_dispatch(fleet):
    f = fleet
    f["client"].dispatch("node-1", f["req"])
    fresh_plane = FleetControlPlane(f["plane"].store.path)
    reborn = NodeAuthenticator("controller", fresh_plane.store, tuple(f["controller"]._keys.values()))
    client = RpcNodeClient(reborn, {"node-1": f["endpoint"]}, leases=fresh_plane.leases)
    with pytest.raises(DispatchOutcomeUnknown):
        client.dispatch("node-1", f["req"])
    assert f["world"].side_effects() == 1
    assert client.probe("node-1") is True            # the transport itself still works


# --------------------------------- capability, telemetry and resource admission

def test_malicious_node_cannot_claim_capabilities_it_does_not_have(fleet):
    f = fleet
    req = PlacementRequirement(capabilities=("gpu.render",), required_models=("secret-llm",), privacy="private")
    assert f["plane"].scheduler.reject_reasons(f["plane"].registry.node("node-1"), req)
    # A heartbeat is the only channel a node has, and it carries no capability grant.
    f["plane"].registry.heartbeat(Heartbeat("node-1", time.time(), warm_models=("secret-llm",)))
    node = f["plane"].registry.node("node-1")
    assert "gpu.render" not in node.capabilities and "secret-llm" not in node.models
    assert "missing_capability:gpu.render" in f["plane"].scheduler.reject_reasons(node, req)
    assert "missing_model:secret-llm" in f["plane"].scheduler.reject_reasons(node, req)


def test_heartbeat_cannot_forge_free_memory_or_concurrency(fleet):
    """REGRESSION: self-reported telemetry is clamped into the provisioned envelope."""
    f = fleet
    plane = f["plane"]
    plane.registry.register(_node("small", ram_gb=16.0, gpu_memory_gb=8.0, max_concurrency=1),
                            now=time.time())
    req = PlacementRequirement(capabilities=("fs.write",), min_ram_gb=999.0,
                               min_gpu_memory_gb=500.0, privacy="private")
    plane.registry.heartbeat(Heartbeat("small", time.time(), load=-50.0, ram_used_gb=-100000.0,
                                       gpu_memory_used_gb=-100000.0, active_work=-99))
    node = plane.registry.node("small")
    assert node.ram_free_gb <= 16.0 and node.gpu_free_gb <= 8.0
    assert node.active_work >= 0 and 0.0 <= node.load <= 1.0
    assert plane.scheduler.reject_reasons(node, req)


def test_heartbeat_cannot_forge_freshness_or_hide_usage(fleet):
    """Second angle: a future/NaN timestamp must not outlive the watchdog."""
    f = fleet
    plane = f["plane"]
    now = time.time()
    plane.registry.heartbeat(Heartbeat("node-2", now + 10 ** 9, ram_used_gb=float("nan")))
    node = plane.registry.node("node-2")
    assert node.last_heartbeat_ts <= now + 5
    assert node.ram_used_gb == node.ram_gb            # unreadable usage is NOT free memory
    report = plane.registry.evaluate(now + plane.registry.heartbeat_timeout_s + 10)
    assert "node-2" in report.newly_offline
    assert plane.registry.node("node-2").status == NodeStatus.OFFLINE


def test_resource_admission_refuses_before_any_dispatch(fleet):
    f = fleet
    plane = f["plane"]
    plane.registry.register(_node("tiny", ram_gb=4.0, gpu_memory_gb=0.0, max_concurrency=1), now=time.time())
    contract = _contract(f["world"], "huge", ["huge.txt"], privacy="private",
                         placement={"min_ram_gb": 10 ** 6})
    placement = plane.place(contract)
    assert not placement.ok and placement.status == "ADMISSION_REJECTED"
    assert plane.store.leases(work_id="huge") == []
    assert f["world"].side_effects() == 0


# ---------------------------------------------- PRIVATE never leaves this node

def test_private_placement_never_leaves_the_local_node(fleet):
    """REGRESSION: PRIVATE/LOCAL_ONLY/INTERNAL are refused BEFORE any byte is sent."""
    f = fleet
    for privacy in ("private", "local_only", "internal"):
        contract = replace(f["contract"], privacy=privacy)
        with pytest.raises(NodeAuthDenied):
            f["client"].dispatch("node-1", replace(f["req"], contract=contract))
    assert f["world"].side_effects() == 0


def test_no_remote_configuration_can_opt_into_private_work(fleet):
    """Second angle: the escape hatch itself is gone — endpoint AND node refuse it."""
    f = fleet
    for levels in (frozenset({"private"}), frozenset({"public", "private"}),
                   frozenset({"internal"}), frozenset({"local_only"})):
        with pytest.raises(ValueError):
            replace(f["endpoint"], privacy_levels=levels)
        with pytest.raises(ValueError):
            RemoteNodeGateway("node-1", f["node1"], f["local"],
                              controllers=frozenset({"controller"}), privacy_levels=levels)
    # And a CLOUD node still cannot receive full context for public work.
    f["plane"].registry.register(_node("node-1", trust_class=CLOUD, privacy_level="public"), now=time.time())
    with pytest.raises(NodeAuthDenied):
        f["client"].dispatch("node-1", f["req"])
    assert f["world"].side_effects() == 0
