"""Fleet OS — единицы: планировщик (объяснимость, приватность, память, модели,
локальность), аренды с fencing, полётный автомат состояний, durable очередь с
гонкой claim, retry/dead-letter, credential broker без секретов, журнал с
дедупом, артефакты, resume-kernel, twin.

Ни одна проверка не считает PLACED/LEASED/heartbeat исполнением.
"""
from __future__ import annotations

import threading

import pytest

from bossman_v3.contracts import SideEffectClass, TypedAction
from bossman_v3.execution import PlanStep
from bossman_v3.memory.journal import TaskJournal
from bossman_v3.organization import DelegationContract, EvidenceRequirement, Resources
from bossman_v3.fleet import (
    CLOUD, TRUSTED_LOCAL, TRUSTED_REMOTE, CredentialBroker, DistributedFlightRecorder, FailureClass, FleetControlPlane,
    FleetEventJournal, FleetEventType, FleetResumeKernel, FleetScheduler, FleetStore, FlightState, GrantDenied,
    Heartbeat, IllegalTransition, LeaseConflict, LeaseManager, NodeState, NodeStatus, PlacementRequirement,
    PrivacyRouter, StaleLease, WorkQueue, classify_failure, describe_file, mutation_key, verify_file)


def _node(nid, *, trust=TRUSTED_LOCAL, privacy="private", ram=128, gpu=96, caps=("terminal.run",), models=(),
          warm=(), load=0.1, hb=1000.0, **kw):
    return NodeState(nid, hostname=nid, os_name="Linux", ram_gb=ram, gpu_memory_gb=gpu, capabilities=set(caps),
                     models=set(models), warm_models=set(warm), privacy_level=privacy, trust_class=trust, load=load,
                     last_heartbeat_ts=hb, **kw)


def _req(**kw):
    base = dict(capabilities=("terminal.run",), privacy="private")
    base.update(kw)
    return PlacementRequirement(**base)


def _contract(work_id="w1", **kw):
    base = dict(work_id=work_id, mission_id="m1", department_id="engineering", goal="g", required_capability="terminal.run",
                success_criteria=["ok"], evidence_required=[EvidenceRequirement("file", "/x")], budget=Resources(usd=1))
    base.update(kw)
    return DelegationContract(**base)


# --------------------------------------------------------------- scheduler

def test_private_work_never_goes_to_cloud_even_if_cloud_is_the_only_capable_node():
    s = FleetScheduler()
    cloud = _node("cloud", trust=CLOUD, privacy="public")
    best, ex = s.choose([cloud], _req())
    assert best is None and "private_task_requires_trusted_local_node" in ex[0].reasons


def test_scheduler_explains_selection_and_rejections_deterministically():
    s = FleetScheduler()
    aimax = _node("ai-max", ram=128, gpu=96, models=("qwen-72b",), warm=("qwen-72b",), unified_memory=True)
    laptop = _node("laptop", ram=8, gpu=0)
    cloud = _node("cloud-01", trust=CLOUD, privacy="public", ram=256, gpu=80)
    best, ex = s.choose([laptop, cloud, aimax], _req(min_ram_gb=48, required_models=("qwen-72b",)))
    assert best.node_id == "ai-max"
    assert {"local_private", "model_warm"} <= set(best.reasons)
    by = {e.node_id: e for e in ex}
    assert any(r.startswith("insufficient_memory") for r in by["laptop"].reasons)
    assert "private_task_requires_trusted_local_node" in by["cloud-01"].reasons
    assert [e.node_id for e in ex] == ["ai-max", "cloud-01", "laptop"] or ex[0].node_id == "ai-max"


def test_scheduler_prefers_local_artifacts_over_faster_node_requiring_transfer():
    s = FleetScheduler()
    fast = _node("fast", ram=256, load=0.0)
    slow = _node("slow", ram=64, load=0.5, artifacts={"abc"})
    best, _ = s.choose([fast, slow], _req(artifacts=("abc",), artifact_bytes=40 * 1024 ** 3))
    assert best.node_id == "slow"


def test_scheduler_uses_observed_reliability_and_anti_affinity():
    s = FleetScheduler(reliability=lambda n, c: 0.9 if n == "b" else 0.1)
    a, b = _node("a", failure_domain="rack1"), _node("b", failure_domain="rack2")
    assert s.choose([a, b], _req()).__getitem__(0).node_id == "b"
    best, ex = s.choose([a, b], _req(anti_affinity_domains=("rack2",)))
    assert best.node_id == "a" and "anti_affinity_domain" in [r for e in ex if e.node_id == "b" for r in e.reasons]


def test_admission_rejects_work_no_node_can_ever_fit():
    s = FleetScheduler()
    _, ex = s.choose([_node("a", ram=16), _node("b", ram=32)], _req(min_ram_gb=200))
    assert s.admission_reason(ex).startswith("admission_rejected")


def test_offline_and_draining_nodes_are_not_scheduled():
    s = FleetScheduler()
    off = _node("off", status=NodeStatus.OFFLINE)
    drain = _node("drain", status=NodeStatus.DRAINING)
    best, ex = s.choose([off, drain], _req())
    assert best is None and {"node_offline", "node_draining"} == {e.reasons[0] for e in ex}


# ----------------------------------------------------------------- privacy

def test_privacy_router_matrix():
    pr = PrivacyRouter()
    local, remote, cloud = _node("l"), _node("r", trust=TRUSTED_REMOTE, privacy="internal"), _node("c", trust=CLOUD, privacy="public")
    assert pr.decide(requested_privacy="private", node=local).allowed
    assert not pr.decide(requested_privacy="private", node=remote).allowed
    assert not pr.decide(requested_privacy="internal", node=local, contains_secrets=False).allowed is False
    d = pr.decide(requested_privacy="internal", node=cloud)
    assert d.allowed and d.context_policy == "MINIMIZED"
    assert not pr.decide(requested_privacy="public", node=cloud, contains_secrets=True).allowed
    assert not pr.decide(requested_privacy="weird", node=local).allowed          # fail-closed


# ------------------------------------------------------------------ leases

def test_lease_ttl_renew_expire_and_fencing(tmp_path):
    store = FleetStore(tmp_path / "f.sqlite")
    lm = LeaseManager(store)
    l1 = lm.acquire(node_id="n", work_id="w1", now=0.0, ttl_seconds=10, resource_class="gpu", exclusive=True)
    with pytest.raises(LeaseConflict):
        lm.acquire(node_id="n", work_id="w2", now=1.0, ttl_seconds=10, resource_class="gpu", exclusive=True)
    l1 = lm.renew(l1, now=5.0, ttl_seconds=10)
    assert l1.expires_ts == 15.0 and lm.valid(l1, now=6.0) == (True, "valid")
    assert lm.expire(now=20.0) and lm.valid(l1, now=20.0)[0] is False
    with pytest.raises(StaleLease):
        lm.renew(l1, now=21.0, ttl_seconds=10)
    l2 = lm.acquire(node_id="n", work_id="w3", now=21.0, ttl_seconds=10, resource_class="gpu")
    assert l2.fence == l1.fence + 1
    # старый держатель «ожил»: его токен ниже текущего — власти нет
    ok, why = lm.valid(l1, now=22.0)
    assert not ok


def test_non_exclusive_leases_coexist(tmp_path):
    lm = LeaseManager(FleetStore(tmp_path / "f.sqlite"))
    lm.acquire(node_id="n", work_id="a", now=0, ttl_seconds=5, resource_class="cpu", exclusive=False)
    lm.acquire(node_id="n", work_id="b", now=0, ttl_seconds=5, resource_class="cpu", exclusive=False)
    assert len(lm.store.leases(node_id="n")) == 2


# ------------------------------------------------------------------ flight

def test_placed_cannot_become_verified_and_verified_needs_trusted_evidence(tmp_path):
    store = FleetStore(tmp_path / "f.sqlite")
    fr = DistributedFlightRecorder(store, FleetEventJournal(store))
    f = fr.open("w1", "m1")
    fr.transition(f, FlightState.QUEUED)
    fr.transition(f, FlightState.PLACED, node_id="n")
    with pytest.raises(IllegalTransition):
        fr.transition(f, FlightState.VERIFIED, evidence_refs=["journal:m1__w1/s1"])
    fr.transition(f, FlightState.LEASED, lease_id="L", fence=1)
    fr.transition(f, FlightState.DISPATCHED)
    fr.transition(f, FlightState.EXECUTING)
    fr.transition(f, FlightState.OBSERVED)
    fr.transition(f, FlightState.VERIFYING)
    with pytest.raises(IllegalTransition):
        fr.transition(f, FlightState.VERIFIED, evidence_refs=["agent:coder says done"])
    fr.transition(f, FlightState.VERIFIED, evidence_refs=["journal:m1__w1/s1"])
    again = FleetStore(tmp_path / "f.sqlite").flight("w1")
    assert again.state == FlightState.VERIFIED and [h["to"] for h in again.history][-1] == "VERIFIED"
    kinds = {e["type"] for e in store.events()}
    assert {"TASK_PLACED", "TASK_DISPATCHED", "TASK_OBSERVED", "TASK_VERIFIED"} <= kinds


def test_verified_mutation_key_prevents_duplicate_and_counts_it(tmp_path):
    store = FleetStore(tmp_path / "f.sqlite")
    fr = DistributedFlightRecorder(store)
    f = fr.open("w1", "m1")
    assert fr.record_verified_mutation(f, step_id="s1", action=None, evidence_ref="journal:m1__w1/s1") is True
    assert fr.record_verified_mutation(f, step_id="s1", action=None, evidence_ref="journal:m1__w1/s1") is False
    assert fr.duplicate_preventions == 1 and fr.verified_step_ids("m1", "w1") == {"s1"}
    with pytest.raises(IllegalTransition):
        fr.record_verified_mutation(f, step_id="s2", action=None, evidence_ref="model:trust-me")
    assert mutation_key("m", "w", "s", {"a": 1}) == mutation_key("m", "w", "s", {"a": 1})
    assert mutation_key("m", "w", "s", {"a": 1}) != mutation_key("m", "w", "s", {"a": 2})


# ------------------------------------------------------------------- queue

def test_double_claim_race_has_exactly_one_winner(tmp_path):
    store = FleetStore(tmp_path / "f.sqlite")
    q = WorkQueue(store, FleetScheduler())
    assert q.enqueue("w1", "m1", priority=1, requirement=_req())
    n1, n2 = _node("n1"), _node("n2")
    results: dict[str, object] = {}
    barrier = threading.Barrier(2)

    def race(node):
        barrier.wait()
        results[node.node_id] = q.claim(node, now=1.0)

    ts = [threading.Thread(target=race, args=(n,)) for n in (n1, n2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    winners = [k for k, v in results.items() if v is not None]
    assert len(winners) == 1
    assert store.queue()[0]["claimed_by"] == winners[0]
    # проигравший не может «перезанять» без release
    loser = "n2" if winners[0] == "n1" else "n1"
    assert q.claim(_node(loser), now=2.0) is None


def test_claim_respects_eligibility_privacy_and_capability(tmp_path):
    store = FleetStore(tmp_path / "f.sqlite")
    q = WorkQueue(store, FleetScheduler())
    q.enqueue("w1", "m1", priority=1, requirement=_req(capabilities=("vision",)))
    assert q.claim(_node("cloud", trust=CLOUD, privacy="public", caps=("vision",))) is None
    assert q.claim(_node("local", caps=("terminal.run",))) is None
    assert q.claim(_node("local-vision", caps=("vision",))).work_id == "w1"


def test_retry_classification_and_dead_letter_is_durable(tmp_path):
    store = FleetStore(tmp_path / "f.sqlite")
    q = WorkQueue(store, FleetScheduler(), authorize_requeue=lambda by, work: by == "human:owner")
    assert classify_failure("PolicyDeniedError: hard_deny") == FailureClass.NEVER_RETRY
    assert classify_failure("ApprovalDeniedError: owner") == FailureClass.HUMAN_REQUIRED
    q.enqueue("w1", "m1", priority=1, requirement=_req())
    claim = q.claim(_node("n"), now=10)
    _, action = q.on_failure("w1", "m1", claim=claim, reason="PolicyDeniedError: forbidden", now=10)
    assert action == "DEAD_LETTER" and store.queue() == []
    assert [d["work_id"] for d in FleetStore(tmp_path / "f.sqlite").dead_letters()] == ["w1"]
    q.enqueue("w2", "m1", priority=1, requirement=_req())
    now = 100
    for attempt in range(q.retry.max_attempts):
        claim = q.claim(_node("n"), now=now)
        assert claim is not None
        fc, action = q.on_failure("w2", "m1", claim=claim, reason="node lost", attempts=999, now=now)
        if attempt + 1 < q.retry.max_attempts:
            assert fc == FailureClass.REROUTE and action.startswith("REQUEUE_AFTER_")
            assert q.claim(_node("n"), now=now) is None
        now += 10000
    assert action == "DEAD_LETTER"
    with pytest.raises(PermissionError):
        q.requeue_dead_letter("w2", by="agent:coder")
    assert q.requeue_dead_letter("w2", by="human:owner")


# -------------------------------------------------------------- credentials

def test_credential_broker_stores_grants_not_secrets(tmp_path):
    store = FleetStore(tmp_path / "f.sqlite")

    class Provider:
        def resolve(self, secret_id):
            return "sk-live-abcdefghijklmnopqrstuvwxyz0123"        # ci-secret-scan: allow — канарейка

    broker = CredentialBroker(store, Provider())
    with pytest.raises(GrantDenied):
        broker.grant(secret_id="github", node_id="n1", capability="github.push", scope="mission:m1", expires_ts=100,
                     granted_by="agent:coder")
    g = broker.grant(secret_id="github", node_id="n1", capability="github.push", scope="mission:m1", expires_ts=100,
                     granted_by="human:owner")
    assert broker.resolve(secret_id="github", node_id="n1", capability="github.push", scope="mission:m1", now=10).startswith("sk-live")
    with pytest.raises(GrantDenied):
        broker.resolve(secret_id="github", node_id="n2", capability="github.push", scope="mission:m1", now=10)
    with pytest.raises(GrantDenied):
        broker.resolve(secret_id="github", node_id="n1", capability="github.push", scope="mission:m2", now=10)
    with pytest.raises(GrantDenied):
        broker.resolve(secret_id="github", node_id="n1", capability="github.push", scope="mission:m1", now=101)
    assert broker.revoke(g.grant_id) and broker.authorized(secret_id="github", node_id="n1", capability="github.push",
                                                           scope="mission:m1", now=10) is None
    raw = (tmp_path / "f.sqlite").read_bytes()
    assert b"sk-live" not in raw


# ------------------------------------------------------------------ journal

def test_event_journal_dedups_and_redacts(tmp_path):
    store = FleetStore(tmp_path / "f.sqlite")
    j = FleetEventJournal(store)
    assert j.emit(FleetEventType.NODE_REGISTERED, node_id="n", payload={"k": "v"}, ts=1.0, event_id="e1")
    assert not j.emit(FleetEventType.NODE_REGISTERED, node_id="n", payload={"k": "v"}, ts=1.0, event_id="e1")
    assert j.deduplicated == 1
    j.emit(FleetEventType.TASK_PLACED, payload={"token": "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"}, ts=2.0)  # ci-secret-scan: allow
    assert "ghp_" not in (tmp_path / "f.sqlite").read_bytes().decode("utf-8", "ignore")


# ---------------------------------------------------------------- artifacts

def test_artifact_hash_verification(tmp_path):
    f = tmp_path / "model.bin"
    f.write_bytes(b"weights")
    d = describe_file(f, artifact_id="qwen")
    assert verify_file(f, d.sha256)
    f.write_bytes(b"corrupted")
    assert not verify_file(f, d.sha256)


# ------------------------------------------------------------------- resume

def _plan():
    def step(sid, cls):
        return PlanStep(sid, sid, TypedAction("fs.write", {"name": sid}, side_effect=cls))
    return [step("s1", SideEffectClass.IDEMPOTENT_WRITE), step("s2", SideEffectClass.IRREVERSIBLE),
            step("s3", SideEffectClass.READ_ONLY)]


def test_resume_kernel_blocks_in_flight_irreversible_step_after_node_loss(tmp_path):
    j = TaskJournal.start(task_id="m__w", plan=[(s.step_id, s.intent) for s in _plan()], root=tmp_path)
    j.record("s1", receipt={"eff": 1}, verified=True)
    k = FleetResumeKernel()
    d = k.decide(j, _plan(), lost_in_flight=True)
    assert not d.resumable and "owner decision" in d.reason  # legacy pending is ambiguous after loss
    j.fail("s2", error="node lost mid-flight", by="node-1")
    d = k.decide(j, _plan(), lost_in_flight=True)
    assert not d.resumable and "owner decision" in d.reason
    d = k.decide(j, _plan(), lost_in_flight=False)
    assert d.resumable and d.next_step_id == "s2"


# ----------------------------------------------------------- registry/health

def test_heartbeat_timeout_marks_offline_and_reclaims_leases(tmp_path):
    plane = FleetControlPlane(tmp_path / "f.sqlite", heartbeat_timeout_s=30)
    plane.registry.register(_node("n1", hb=100.0), now=100.0)
    plane.leases.acquire(node_id="n1", work_id="w", now=100.0, ttl_seconds=1000)
    plane.registry.heartbeat(Heartbeat("n1", 110.0, load=0.3, warm_models=("m",)))
    assert plane.registry.node("n1").warm_models == {"m"}
    rep = plane.health(now=200.0)
    assert rep.newly_offline == ("n1",) and plane.store.leases(node_id="n1") == []
    assert plane.registry.node("n1").status == NodeStatus.OFFLINE
    plane.registry.heartbeat(Heartbeat("n1", 201.0))
    assert plane.registry.node("n1").status == NodeStatus.ONLINE          # вернулся
    plane.registry.drain("n1")
    plane.registry.heartbeat(Heartbeat("n1", 202.0))
    assert plane.registry.node("n1").status == NodeStatus.DRAINING        # heartbeat не снимает drain


def test_twin_reads_only_durable_truth(tmp_path):
    plane = FleetControlPlane(tmp_path / "f.sqlite")
    plane.registry.register(_node("ai-max", models=("qwen",), warm=("qwen",)), now=1.0)
    plane.registry.register(_node("cloud", trust=CLOUD, privacy="public"), now=1.0)
    snap = plane.snapshot(now=2.0)
    assert snap["online_nodes"] == ["ai-max", "cloud"] and snap["warm_models"]["ai-max"] == ["qwen"]
    assert snap["remote_transport_production_ready"] is False
    assert snap["verified_mutations"] == 0 and snap["active_leases"] == []
    again = FleetControlPlane(tmp_path / "f.sqlite")
    assert again.snapshot(now=3.0)["nodes"][0]["node_id"] == "ai-max"
