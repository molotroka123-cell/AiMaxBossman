"""CLOSURE-002 §4 — десять доказательств безопасности флота, каждое привязано к
детерминированному тесту в репозитории (реестр ниже), плюс два новых теста для
пунктов, которых не было явно: (2) истёкшая аренда не возвращает власть и
(8) провал размещения не штрафует надёжность исполнителя.

REMOTE_TRANSPORT_PRODUCTION_READY=NO, NODE_AUTH_PRODUCTION_READY=NO — здесь
доказывается только локальный транспорт; распределённая готовность не заявляется.
"""
from __future__ import annotations

import importlib

import pytest

from bossman_v3.fleet import FleetStore, LeaseManager, RemoteTransportUnavailable
from bossman_v3.fleet.leases import StaleLease
from bossman_v3.fleet.node_agent import RemoteNodeTransport
from test_v3_fleet_e2e import Stack, _contract, _node

PROOFS = {
    1: ("stale fencing token never authorizes a side effect",
        [("test_fence_fl01", "test_fence_rejects_zombie_writer"), ("test_v3_fleet_core", "test_lease_ttl_renew_expire_and_fencing")]),
    2: ("expired lease cannot regain authority", [("test_v3_fleet_safety_proofs", "test_expired_lease_cannot_regain_authority")]),
    3: ("zombie worker after reassignment cannot produce VERIFIED",
        [("test_fence_fl01", "test_zombie_execute_exits_without_writing"), ("test_v3_fleet_core", "test_placed_cannot_become_verified_and_verified_needs_trusted_evidence")]),
    4: ("two racing workers cannot both own a mutation",
        [("test_v3_fleet_core", "test_double_claim_race_has_exactly_one_winner"), ("test_v3_fleet_e2e", "test_e2e_double_claim_two_nodes_one_winner"),
         ("test_v3_fleet_core", "test_verified_mutation_key_prevents_duplicate_and_counts_it")]),
    5: ("restart after a verified step does not replay it",
        [("test_v3_fleet_e2e", "test_e2e_node_failure_restart_resumes_on_node_2_without_duplicate"),
         ("test_v3_org_benchmark", "test_long_horizon_resume_benchmark_crash_at_step_7")]),
    6: ("irreversible in-flight action blocks the owner instead of replaying",
        [("test_v3_fleet_e2e", "test_node_loss_mid_irreversible_step_blocks_instead_of_replaying"),
         ("test_v3_fleet_core", "test_resume_kernel_blocks_in_flight_irreversible_step_after_node_loss")]),
    7: ("private contract cannot select cloud",
        [("test_v3_fleet_core", "test_private_work_never_goes_to_cloud_even_if_cloud_is_the_only_capable_node"),
         ("test_v3_fleet_e2e", "test_e2e_private_work_never_reaches_cloud")]),
    8: ("placement failure does not penalize executor reliability",
        [("test_v3_fleet_safety_proofs", "test_placement_failure_does_not_penalize_executor")]),
    9: ("node-supplied forged journal evidence is rejected",
        [("test_v3_fleet_e2e", "test_node_returned_forged_journal_evidence_is_rejected_by_fleet"),
         ("test_v3_evidence_signing", "test_evidence_unsigned_verified_is_rejected")]),
    10: ("PLACED→VERIFIED shortcut is illegal",
         [("test_v3_fleet_core", "test_placed_cannot_become_verified_and_verified_needs_trusted_evidence"),
          ("test_v3_fleet_e2e", "test_placement_alone_never_completes_work")]),
}


def test_every_proof_is_backed_by_an_existing_deterministic_test():
    """Реестр не декларативен: каждый указанный тест реально существует."""
    import sys
    from pathlib import Path
    cc_tests = Path(__file__).resolve().parents[2] / "command-center" / "tests"
    for n, (claim, refs) in PROOFS.items():
        assert refs, f"proof {n} ({claim}) has no test"
        for module, func in refs:
            if module == "test_fence_fl01":
                src = (cc_tests / f"{module}.py").read_text(encoding="utf-8")
                assert f"def {func}(" in src, f"proof {n}: {module}.{func} missing"
                continue
            mod = importlib.import_module(module)
            assert callable(getattr(mod, func, None)), f"proof {n}: {module}.{func} missing"


def test_expired_lease_cannot_regain_authority(tmp_path):
    """Fence аренды — на (узел, класс ресурса): это исключительный доступ к GPU/CPU узла.
    Владение единицей работы между узлами защищает `claim_fence` очереди (proof 4) и
    fence движка V2 (FL-01, proof 1). Здесь: истёкшая аренда не продлевается ни до, ни
    после появления нового держателя на том же узле, а новый держатель получает больший fence."""
    lm = LeaseManager(FleetStore(tmp_path / "f.sqlite"))
    lease = lm.acquire(node_id="n1", work_id="w", now=0.0, ttl_seconds=10, resource_class="gpu", exclusive=True)
    assert lm.expire(now=30.0)
    assert lm.valid(lease, now=30.0)[0] is False
    with pytest.raises(StaleLease):
        lm.renew(lease, now=31.0, ttl_seconds=10)       # даже без нового держателя власть не возвращается
    fresh = lm.acquire(node_id="n1", work_id="w", now=32.0, ttl_seconds=10, resource_class="gpu", exclusive=True)
    assert fresh.fence > lease.fence
    with pytest.raises(StaleLease):
        lm.renew(lease, now=33.0, ttl_seconds=10)
    assert lm.valid(fresh, now=33.0)[0] is True and lm.valid(lease, now=33.0)[0] is False


def test_placement_failure_does_not_penalize_executor(tmp_path):
    s = Stack(tmp_path)
    for nid in ("node-1", "node-2"):
        n = s.plane.registry.node(nid); n.capabilities = {"other"}; s.plane.store.save_node(n)
    before = s.org.learning.stats("coder", "fs.write").to_dict()
    s.org.receive_mission("m1", title="x", department_id="engineering", contracts=[_contract(s.world, "w1", ["a.txt"])])
    status = s.org.run_mission("m1")
    assert not status.done and status.blockers and s.world.side_effects() == 0
    after = s.org.learning.stats("coder", "fs.write").to_dict()
    assert after["n_raw"] == before["n_raw"] and after["failures"] == before["failures"]
    assert s.org.store.work("w1")["attempts"] == 0                    # попытка не списана
    assert s.org.store.work("w1")["state"] == "blocked"


def test_remote_transport_is_not_production_ready():
    with pytest.raises(RemoteTransportUnavailable):
        RemoteNodeTransport().dispatch("node-x", None)  # type: ignore[arg-type]
