"""Real Fleet SQLite integration and adversarial preflight fixtures (no live AI)."""
from dataclasses import replace
import json
import socket
import sqlite3

import pytest

from bossman_v3.fleet.control_plane import FleetControlPlane
from bossman_v3.fleet.models import NodeState, NodeStatus, PlacementRequirement
from bossman_v3.fleet.preflight import FleetPreflight
from bossman_v3.fleet.privacy import PrivacyRouter
from bossman_v3.fleet.scheduler import FleetScheduler

NOW = 1000.0


@pytest.fixture
def plane(tmp_path):
    p = FleetControlPlane(tmp_path / "fleet.sqlite")
    p.registry.register(NodeState(
        "local", os_name="linux", ram_gb=128, gpu_memory_gb=128,
        unified_memory=True, capabilities={"code", "image"}, models={"coder"},
        pools={"local"}, failure_domain="desktop", max_concurrency=4,
    ), now=NOW)
    return p


def dump(plane):
    with sqlite3.connect(plane.store.path) as con:
        return tuple(con.iterdump())


def codes(report):
    return [b.code for b in report.blockers] + [b.code for n in report.nodes for b in n.blockers]


def corrupt_node(plane, **updates):
    with sqlite3.connect(plane.store.path) as con:
        raw = json.loads(con.execute("SELECT payload FROM fleet_nodes").fetchone()[0])
        raw.update(updates)
        con.execute("UPDATE fleet_nodes SET payload=?", (json.dumps(raw),))


def test_actual_store_read_is_effect_free_and_does_not_call_mutating_or_external_paths(plane, monkeypatch):
    before = dump(plane)
    metrics = dict(plane.metrics)
    original = plane.store.path

    def forbidden(*args, **kwargs):
        pytest.fail("preflight called a mutating or external path")

    for obj, name in ((plane, "place"), (plane, "health"), (plane.leases, "acquire"),
                      (plane.store, "connect"), (plane.registry, "evaluate"),
                      (plane.transport, "execute"), (plane.credentials, "secret_provider")):
        # No production callback is allowed, including store's WAL-setting opener.
        monkeypatch.setattr(obj, name, forbidden, raising=False)
    monkeypatch.setattr(socket, "socket", forbidden)
    probe = FleetPreflight(plane.store)
    for _ in range(3):
        result = probe.inspect(PlacementRequirement(capabilities=("code",), min_ram_gb=16), now=NOW)
        assert result.status == "CANDIDATES_AVAILABLE"
        assert result.candidate_node_ids == ("local",)
        assert result.dispatch_authorized is False
    assert dump(plane) == before
    assert plane.metrics == metrics
    assert plane.store.path == original


@pytest.mark.parametrize("host,gpu,used,eligible", [
    (60, 60, 0, True), (70, 60, 0, False), (40, 60, 20, True), (55, 55, 20, False),
])
def test_unified_128gb_pool_is_counted_once_using_canonical_scheduler(plane, host, gpu, used, eligible):
    corrupt_node(plane, ram_used_gb=used, gpu_memory_used_gb=used)
    req = PlacementRequirement(min_ram_gb=host, min_gpu_memory_gb=gpu)
    result = FleetPreflight(plane.store).inspect(req, now=NOW)
    canonical = plane.scheduler.reject_reasons(plane.registry.node("local"), req)
    assert bool(result.candidate_node_ids) is eligible
    assert codes(result) == canonical
    if not eligible:
        assert any("insufficient_unified_memory" in c for c in codes(result))


@pytest.mark.parametrize("changes", [
    {"capabilities": ("missing_app",)}, {"required_models": ("missing_model",), "min_ram_gb": 1},
    {"pools": ("remote",)}, {"allowed_os": ("windows",)},
    {"anti_affinity_domains": ("desktop",)}, {"max_load": 0.2},
    {"min_ram_gb": 256}, {"min_gpu_memory_gb": 256}, {"privacy": "invented"},
])
def test_all_existing_placement_constraints_are_preserved(plane, changes):
    corrupt_node(plane, load=0.5)
    req = replace(PlacementRequirement(), **changes)
    result = FleetPreflight(plane.store).inspect(req, now=NOW)
    expected = plane.scheduler.reject_reasons(plane.registry.node("local"), req)
    assert expected
    assert codes(result) == expected
    assert not result.candidate_node_ids


@pytest.mark.parametrize("privacy,secrets", [("private", False), ("local_only", False), ("public", True)])
def test_private_or_secret_work_never_falls_back_to_cloud(plane, privacy, secrets):
    local = plane.registry.node("local")
    local.status = NodeStatus.OFFLINE
    plane.store.save_node(local)
    plane.registry.register(NodeState("cloud", trust_class="cloud", privacy_level="public",
                                      capabilities={"code"}, ram_gb=1024), now=NOW)
    result = FleetPreflight(plane.store).inspect(
        PlacementRequirement(capabilities=("code",), privacy=privacy, contains_secrets=secrets), now=NOW)
    assert not result.candidate_node_ids
    cloud = next(n for n in result.nodes if n.node_id == "cloud")
    assert any(b.category == "privacy" and b.knowledge == "KNOWN" for b in cloud.blockers)


def test_configured_privacy_policy_is_used_and_reliability_callback_is_not(plane):
    corrupt_node(plane, trust_class="cloud", privacy_level="public")
    def no_callback(*args):
        pytest.fail("ranking callbacks must not run during read-only preflight")
    scheduler = FleetScheduler(PrivacyRouter(allow_cloud_minimized_for_internal=False), no_callback)
    result = FleetPreflight(plane.store, scheduler=scheduler).inspect(
        PlacementRequirement(privacy="internal"), now=NOW)
    assert "internal_task_not_allowed_on_cloud" in codes(result)
    assert not result.candidate_node_ids


@pytest.mark.parametrize("field,value", [
    (field, value) for field in ("ram_gb", "gpu_memory_gb", "ram_used_gb", "gpu_memory_used_gb", "load")
    for value in (float("nan"), float("inf"), -1, "128", True)
] + [("ram_used_gb", 129), ("gpu_memory_used_gb", 129), ("load", 1.1),
     ("active_work", -1), ("max_concurrency", -1), ("unified_memory", "false"),
     ("capabilities", "code"), ("status", "invented"), ("trust_class", "invented")])
def test_malformed_raw_telemetry_is_unknown_before_properties_can_clamp_it(plane, field, value):
    corrupt_node(plane, **{field: value})
    result = FleetPreflight(plane.store).inspect(PlacementRequirement(), now=NOW)
    assert result.status == "UNKNOWN"
    assert not result.candidate_node_ids
    assert "node_snapshot_invalid" in codes(result)


@pytest.mark.parametrize("field,value", [
    ("last_heartbeat_ts", 1), ("last_heartbeat_ts", 0), ("last_heartbeat_ts", -1),
    ("last_heartbeat_ts", float("nan")), ("last_heartbeat_ts", NOW + 1),
    ("registered_ts", NOW + 1), ("registered_ts", float("inf")),
])
def test_stale_or_future_node_data_cannot_be_laundered_by_fresh_capture(plane, field, value):
    corrupt_node(plane, **{field: value})
    result = FleetPreflight(plane.store).inspect(PlacementRequirement(), now=NOW)
    assert result.status == "UNKNOWN"
    assert not result.candidate_node_ids


@pytest.mark.parametrize("observed_at,now", [(NOW, NOW + 91), (NOW + 1, NOW),
    (0, NOW), (-1, NOW), (float("nan"), NOW), (NOW, float("inf")), (NOW, -1)])
def test_snapshot_freshness_fails_closed(plane, observed_at, now):
    probe = FleetPreflight(plane.store)
    snap = replace(probe.capture(now=NOW), observed_at=observed_at)
    result = probe.evaluate(snap, PlacementRequirement(), now=now)
    assert result.status == "UNKNOWN"
    assert not result.candidate_node_ids


def test_missing_node_field_and_empty_inventory_are_unknown(plane):
    probe = FleetPreflight(plane.store)
    snap = probe.capture(now=NOW)
    d = json.loads(snap.nodes[0][1])
    del d["ram_used_gb"]
    result = probe.evaluate(replace(snap, nodes=(("local", json.dumps(d)),)), PlacementRequirement(), now=NOW)
    assert result.status == "UNKNOWN"
    assert probe.evaluate(replace(snap, nodes=()), PlacementRequirement(), now=NOW).status == "UNKNOWN"


def test_existing_reservations_defer_without_reserving_releasing_or_recounting_memory(plane):
    plane.leases.acquire(node_id="local", work_id="existing", now=NOW, ttl_seconds=30,
                         exclusive=False, requirement=PlacementRequirement(min_ram_gb=50, min_gpu_memory_gb=50))
    before = dump(plane)
    result = FleetPreflight(plane.store).inspect(PlacementRequirement(min_ram_gb=40), now=NOW)
    assert result.status == "UNKNOWN"
    assert "live_lease_requires_atomic_admission" in codes(result)
    assert not result.candidate_node_ids
    assert dump(plane) == before
    # Expiration affects the observation; the expired lease and reservation
    # remain untouched. Only actual placement may reclaim them.
    result = FleetPreflight(plane.store).inspect(PlacementRequirement(min_ram_gb=40), now=NOW + 31)
    assert result.candidate_node_ids == ("local",)
    assert dump(plane) == before


def test_legacy_lease_without_memory_measurement_is_not_assumed_free(plane):
    plane.leases.acquire(node_id="local", work_id="legacy", now=NOW, ttl_seconds=30, exclusive=False)
    result = FleetPreflight(plane.store).inspect(PlacementRequirement(), now=NOW)
    assert result.status == "UNKNOWN"
    assert "live_lease_requires_atomic_admission" in codes(result)


@pytest.mark.parametrize("field,value", [("expires_ts", float("inf")), ("expires_ts", -1),
    ("acquired_ts", NOW + 1), ("acquired_ts", float("nan"))])
def test_invalid_lease_observations_fail_closed_without_cleanup(plane, field, value):
    plane.leases.acquire(node_id="local", work_id="legacy", now=NOW, ttl_seconds=30)
    probe = FleetPreflight(plane.store)
    snap = probe.capture(now=NOW)
    row = json.loads(snap.leases[0])
    row[field] = value
    before = dump(plane)
    result = probe.evaluate(replace(snap, leases=(json.dumps(row),)), PlacementRequirement(), now=NOW)
    assert result.status == "UNKNOWN"
    assert not result.candidate_node_ids
    assert dump(plane) == before


@pytest.mark.parametrize("changes", [{"min_ram_gb": float("nan")}, {"min_gpu_memory_gb": -1},
    {"max_load": float("inf")}, {"max_load": -1}, {"artifact_bytes": -1},
    {"contains_secrets": "false"}, {"capabilities": "code"}, {"min_ram_gb": 10 ** 1000}])
def test_invalid_demands_do_not_weaken_constraints(plane, changes):
    req = replace(PlacementRequirement(), **changes)
    result = FleetPreflight(plane.store).inspect(req, now=NOW)
    assert result.status == "BLOCKED"
    assert codes(result) == ["invalid_requirement"]


def test_missing_database_is_not_created(plane, tmp_path):
    missing = tmp_path / "absent.sqlite"
    plane.store.path = str(missing)
    result = FleetPreflight(plane.store).inspect(PlacementRequirement(), now=NOW)
    assert result.status == "UNKNOWN"
    assert not missing.exists()


def test_known_model_with_unknown_footprint_defers_instead_of_claiming_it_fits(plane):
    result = FleetPreflight(plane.store).inspect(
        PlacementRequirement(required_models=("coder",)), now=NOW)
    assert result.status == "UNKNOWN"
    assert "model_memory_demand_missing" in codes(result)
    assert not result.candidate_node_ids
