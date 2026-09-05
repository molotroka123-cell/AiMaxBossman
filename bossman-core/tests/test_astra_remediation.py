"""Regression probes for the 7b1377a ASTRA audit; real temporary files/SQLite."""
import asyncio
import gzip
import ipaddress
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from bossman.completion import CompletionContract, CompletionGate, FileObligation
from bossman.toolkit import net
from bossman_v3.execution import CompoundRunner, PlanStep
from bossman_v3.contracts import SideEffectClass, TypedAction
from bossman_v3.memory.journal import TaskJournal, JournalIntegrityError, journal_path
from bossman_v3.memory.assembler import ContextAssembler, estimate_tokens, redact, redact_data
from bossman_v3.organization import (DelegationContract, Evidence, EvidenceRequirement, Resources,
                                    OrganizationStore, TaskState, WorkResult, Department, MissionState,
                                    ScopedKnowledge, ExportBlocked, AgentProfile, CapabilityMarketplace)
from bossman_v3.fleet import NodeState, PlacementRequirement, FleetStore, FleetScheduler
from bossman_v3.fleet.leases import LeaseManager, LeaseConflict, StaleLease
from bossman_v3.fleet.queue import WorkQueue
from test_v3_organization_e2e import World, _agent_factory, _Approval, _contract, Org


def contract(**kw):
    return DelegationContract(**(dict(work_id="w", mission_id="m", department_id="eng", goal="write",
        required_capability="fs.write", success_criteria=["file"],
        evidence_required=[EvidenceRequirement("file", "/x", {"contains": "expected"})],
        budget=Resources(usd=6, concurrency=1, gpu_memory_gb=4)) | kw))


def bound(c, *, when=None, **updates):
    now = when or datetime.now(timezone.utc)
    b = dict(mission_id=c.mission_id, work_id=c.work_id, contract_digest=c.digest(),
             action_digest="action", attempt_id="attempt", started_at=now.isoformat(),
             verification_passed=True, verified_expect={"contains": "expected"})
    b.update(updates)
    return Evidence.signed("file", "/x", source="bossman_v3.verifier", observed_at=now.isoformat(), binding=b)


def test_astra001_final_text_does_not_complete_an_action(tmp_path):
    gate = CompletionGate(CompletionContract(mode="action", files=[FileObligation(path="x")]), tmp_path)
    assert gate.finish()[0] == "unverified"
    (tmp_path / "x").write_text("expected")  # preexisting file cannot prove this task ran
    assert gate.finish()[0] == "unverified"
    gate.record("fs.write", "write", {"path": "x", "content": "expected"}, error=False)
    assert gate.finish()[0] == "done"
    (tmp_path / "x").write_text("changed")
    assert gate.finish()[0] == "unverified"
    assert CompletionGate(CompletionContract(), tmp_path).finish()[0] == "answered"


@pytest.mark.parametrize("task_id", ["../escape", "/tmp/escape", "..", "a/b", "a\\b", "C:drive", "CON", "LPT1.txt", "trailing.", "x\x00"])
def test_astra007_journal_path_is_confined(tmp_path, task_id):
    with pytest.raises(JournalIntegrityError):
        TaskJournal.start(task_id=task_id, plan=[], root=tmp_path)


def test_astra002_tampered_completion_cannot_be_resumed(tmp_path):
    j = TaskJournal.start(task_id="job", plan=[("s", "write")], root=tmp_path)
    j.record("s", receipt={"effect": "ok"}, verified=True)
    p = journal_path(tmp_path, "job")
    raw = json.loads(p.read_text()); raw["steps"][0]["sig"] = ""
    p.write_text(json.dumps(raw))
    with pytest.raises(JournalIntegrityError):
        TaskJournal.load(task_id="job", root=tmp_path)


@pytest.mark.parametrize("changed", ["args", "expect", "side_effect", "guard"])
def test_astra003_resume_binds_entire_action(tmp_path, changed):
    from bossman_v3.organization.bridges import step_to_dict
    original = step_to_dict(PlanStep("s", "write", TypedAction("fs.write", {"path": "x"})))
    j = TaskJournal.start(task_id="j", plan=[("s", "write")], root=tmp_path)
    j.bind_plan([original])
    altered = json.loads(json.dumps(original))
    if changed == "guard": altered["guard"] = "other"
    elif changed == "side_effect": altered["action"]["side_effect"] = "irreversible"
    else: altered["action"]["args"][changed] = "changed"
    with pytest.raises(JournalIntegrityError): j.bind_plan([altered])


@pytest.mark.parametrize("updates", [dict(mission_id="other"), dict(work_id="other"),
    dict(contract_digest="old"), dict(attempt_id=""), dict(verification_passed=False),
    dict(verified_expect={"contains": "wrong"})])
def test_astra004_wrong_execution_binding_fails(updates):
    c = contract()
    assert c.validate(WorkResult("w", True, [bound(c)]))[0]
    assert not c.validate(WorkResult("w", True, [bound(c, **updates)]))[0]


def test_astra004_old_and_future_signed_observations_fail():
    c = contract()
    for when in (datetime.now(timezone.utc)-timedelta(days=1), datetime.now(timezone.utc)+timedelta(hours=1)):
        assert not c.validate(WorkResult("w", True, [bound(c, when=when)]))[0]


def test_astra005_crash_after_effect_before_receipt_never_replays(tmp_path, monkeypatch):
    world = World(tmp_path); agent = _agent_factory(world, _Approval(world))("worker", contract())
    plan = [PlanStep("s", "write", TypedAction("fs.write", {"name": "x"}, side_effect=SideEffectClass.IRREVERSIBLE))]
    j = TaskJournal.start(task_id="j", plan=[("s", "write")], root=tmp_path / "journal")
    def crash(*a, **k): raise SystemExit("power loss after effect")
    monkeypatch.setattr(j, "record", crash)
    with pytest.raises(SystemExit): CompoundRunner(agent, j).run(plan)
    assert (tmp_path / "x").exists() and world.writes == ["x"]
    restored = TaskJournal.load(task_id="j", root=tmp_path / "journal")
    assert restored.steps[0].in_flight and restored.steps[0].attempt_id
    assert not CompoundRunner(agent, restored).run(plan).completed
    assert world.writes == ["x"]


def test_astra006_absent_measurement_gets_no_perfect_score():
    from bossman_v3.benchmark_overlay import BenchmarkScorer
    score = BenchmarkScorer().score_mission("m", [])
    assert score.scores["recovery_idempotency"] is None
    assert score.scores["context_continuity"] is None


def test_astra008_pem_and_structured_secrets_are_removed():
    pem = "-----BEGIN " + "PRIVATE KEY-----\nPRIVATE_BODY_CANARY\n-----END PRIVATE KEY-----"
    assert "PRIVATE_BODY_CANARY" not in redact(pem)
    assert "PRIVATE_BODY_CANARY" not in redact(pem.split("-----END")[0])
    cleaned = redact_data({"nested": {"access_token": "synthetic-value", "note": pem}, "okay": 4})
    assert cleaned["okay"] == 4 and "synthetic-value" not in json.dumps(cleaned)


@pytest.mark.parametrize("budget", [0, 1, 3, 10, 40, 100])
def test_astra010_serialized_context_including_header_fits(tmp_path, budget):
    j = TaskJournal.start(task_id="task-with-a-long-identifier", plan=[("s", "write " * 40)], root=tmp_path)
    pack = ContextAssembler().assemble(j, budget_tokens=budget, model="long-model-name")
    assert estimate_tokens(pack.text) <= budget


def test_f001_shared_exclusive_and_renewal_capabilities(tmp_path):
    leases = LeaseManager(FleetStore(tmp_path / "f.db"))
    a = leases.acquire(node_id="n", work_id="a", now=10, ttl_seconds=10, exclusive=False)
    b = leases.acquire(node_id="n", work_id="b", now=10, ttl_seconds=10, exclusive=False)
    assert leases.valid(a, now=11)[0] and leases.valid(b, now=11)[0]
    with pytest.raises(LeaseConflict): leases.acquire(node_id="n", work_id="c", now=11, ttl_seconds=10)
    with pytest.raises(StaleLease): leases.renew(replace(a, work_id="evil"), now=11, ttl_seconds=10)
    leases.release(a); leases.release(b)
    leases.acquire(node_id="n", work_id="c", now=11, ttl_seconds=10)
    with pytest.raises(LeaseConflict): leases.acquire(node_id="n", work_id="d", now=11, ttl_seconds=10, exclusive=False)


def test_f002_expired_capability_refuses_effect(tmp_path):
    leases = LeaseManager(FleetStore(tmp_path / "f.db"))
    lease = leases.acquire(node_id="n", work_id="w", now=time.time()-10, ttl_seconds=1)
    with pytest.raises(StaleLease):
        with leases.mutation_guard(lease): (tmp_path / "effect").write_text("bad")
    assert not (tmp_path / "effect").exists()


def test_f003_f004_queue_claim_fencing_and_durable_waits(tmp_path):
    store = FleetStore(tmp_path / "f.db"); q = WorkQueue(store, FleetScheduler())
    n = NodeState("n", ram_gb=16)
    q.enqueue("w", "m", priority=1, requirement=PlacementRequirement())
    first = q.claim(n, now=10); assert first
    assert q.release(first)
    second = q.claim(n, now=11); assert second
    assert not q.complete(first) and not q.release(first)
    assert not store.dequeue("w", first.node_id, first.claim_fence)
    with pytest.raises(TypeError): store.dequeue("w")
    with pytest.raises(PermissionError): q.on_failure("w", "m", claim=first, reason="timeout", now=11)
    q.on_failure("w", "m", claim=second, reason="ApprovalDeniedError", now=12)
    again = WorkQueue(FleetStore(tmp_path / "f.db"), FleetScheduler())
    assert again.claim(n, now=100000) is None
    with pytest.raises(PermissionError): again.resume_waiting("w", by="human:forged")


def test_f005_atomic_unified_memory_admission(tmp_path):
    store = FleetStore(tmp_path / "f.db")
    store.save_node(NodeState("n", ram_gb=16, gpu_memory_gb=16, unified_memory=True, max_concurrency=8))
    req = PlacementRequirement(min_ram_gb=5, min_gpu_memory_gb=5)
    def reserve(i):
        try:
            LeaseManager(FleetStore(tmp_path / "f.db")).acquire(node_id="n", work_id=str(i), now=10,
                   ttl_seconds=100, exclusive=False, resource_class=str(i), requirement=req)
            return True
        except LeaseConflict: return False
    with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(reserve, [1,2]))
    assert sum(results) == 1


@pytest.mark.parametrize("field", Resources._FIELDS)
@pytest.mark.parametrize("value", [-1, float("nan"), float("inf")])
def test_o003_invalid_resource_values_rejected(field, value):
    with pytest.raises(ValueError): Resources.from_dict({field: value})


def test_o001_private_cloud_marketplace_blocked():
    cloud = AgentProfile("cloud", "eng", {"executor"}, {"fs.write"}, tier="frontier")
    assert not CapabilityMarketplace([cloud]).route(contract()).selected


def test_o002_conflicting_intake_rolls_back_all_rows(tmp_path):
    store = OrganizationStore(tmp_path / "o.db")
    c = contract(); store.receive_mission("m", title="first", department_id="eng", contracts=[c])
    with pytest.raises(Exception):
        store.receive_mission("other", title="bad", department_id="eng",
                              contracts=[contract(work_id="new", mission_id="other"), contract(mission_id="other")])
    assert store.mission("other") is None and store.work("new") is None
    assert store.work("w")["mission_id"] == "m"


def test_o005_o006_atomic_budget_recovery_and_capacity_release(tmp_path):
    path = tmp_path / "o.db"; store = OrganizationStore(path)
    store.save_envelope("organization", limit=Resources(usd=10, concurrency=1, gpu_memory_gb=8), spent=Resources())
    for w in ("a", "b"): store.save_work(contract(work_id=w), state=TaskState.PLANNED)
    def reserve(w):
        try: return OrganizationStore(path).begin_attempt(contract(work_id=w), ["organization"], ["worker"])
        except ValueError: return None
    with ThreadPoolExecutor(max_workers=2) as pool: attempts=list(pool.map(reserve, ["a","b"]))
    assert sum(a is not None for a in attempts) == 1
    winner = "a" if attempts[0] else "b"; attempt = next(a for a in attempts if a)
    again = OrganizationStore(path)
    assert again.envelope_reserves()["organization"].usd == 6
    again.save_envelope("organization", limit=Resources(usd=10), spent=Resources())
    assert again.envelope_reserves()["organization"].usd == 6
    actual=Resources(usd=1, concurrency=1, gpu_memory_gb=4)
    result=WorkResult(winner, executed=False)
    again.settle_attempt(attempt, actual, result); again.settle_attempt(attempt, actual, result)
    spent=again.envelopes()["organization"][1]
    assert spent.usd == 1 and spent.concurrency == 0 and spent.gpu_memory_gb == 0
    assert again.envelope_reserves()["organization"] == Resources()
    assert reserve("b" if winner == "a" else "a")


def test_o007_astra009_stored_scope_ownership_required(tmp_path):
    store=OrganizationStore(tmp_path / "o.db"); k=ScopedKnowledge(store)
    store.save_department(Department("eng", allowed_exports={"summary"}))
    store.save_department(Department("foreign"))
    store.save_mission("m", title="owned", department_id="eng", state=MissionState.RECEIVED)
    fact=k.publish("department:eng", "verified_fact", {"safe":1}, provenance="test")
    with pytest.raises(PermissionError): k.read("mission:m", include_parents=("department:foreign",))
    with pytest.raises(ExportBlocked): k.export(fact, to_scope="organization", source_department=Department("eng"))


@pytest.mark.asyncio
async def test_sec101_pinned_backend_never_resolves_original_host(monkeypatch):
    from bossman_shared.http_transport import PinnedBackend, AnyIOBackend
    calls=[]
    async def connect(self, host, *args, **kwargs): calls.append(host); return "socket"
    monkeypatch.setattr(AnyIOBackend, "connect_tcp", connect)
    pins={}; monkeypatch.setattr(net, "_resolve_host", lambda host:[ipaddress.ip_address("93.184.216.34")])
    net.check_url("https://example.org/", pins=pins)
    monkeypatch.setattr(net, "_resolve_host", lambda host:[ipaddress.ip_address("127.0.0.1")])
    assert await PinnedBackend(pins).connect_tcp("example.org", 443) == "socket"
    assert calls == ["93.184.216.34"]
    with pytest.raises(ValueError): await PinnedBackend(pins).connect_tcp("unpinned", 443)


@pytest.mark.asyncio
async def test_sec102_streamed_gzip_bomb_is_bounded():
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self): yield gzip.compress(b"x" * (net.MAX_RESPONSE_BYTES+1))
    resp=httpx.Response(200, headers={"content-encoding":"gzip"}, stream=Body())
    with pytest.raises(net.EgressDenied): await net._bounded_body(resp)
    await resp.aclose()


@pytest.mark.asyncio
async def test_sec102_http_saved_logs_remove_secret_values(tmp_path, monkeypatch):
    monkeypatch.setattr(net, "_resolve_host", lambda host:[ipaddress.ip_address("93.184.216.34")])
    monkeypatch.setattr(net, "_TRANSPORT", httpx.MockTransport(lambda req:httpx.Response(200,
        json={"access_token":"synthetic-sensitive-value", "safe":1})))
    res=await net.http({"url":"https://example.org"}, SimpleNamespace(workdir=tmp_path))
    assert not res.error
    assert all("synthetic-sensitive-value" not in p.read_text() for p in tmp_path.rglob("*.json"))


def test_o004_mandatory_risk_reviewer_veto_is_not_skipped(tmp_path):
    from bossman_v3.organization import RiskTier
    from bossman_v3.organization.models import ReviewVerdict
    o=Org(tmp_path); rt=o.runtime
    rt.register_department(Department("engineering", require_risk_review=True, budget=Resources(usd=100)))
    for role in ("lead", "executor", "reviewer", "risk"):
        rt.register_agent(AgentProfile(role, "engineering", {role}, {"fs.write"},
                                       model=role+"-model", risk_clearance=RiskTier.HIGH))
    seen=[]
    def review(c, result, *, reviewer_id, producer_id):
        seen.append(reviewer_id)
        return ReviewVerdict(reviewer_id, reviewer_id != "risk", "risk veto")
    rt.reviewer=SimpleNamespace(review=review)
    c=_contract(o.world, "w", ["x"], risk=RiskTier.HIGH, max_attempts=1)
    rt.receive_mission("m1", title="risk", department_id="engineering", contracts=[c])
    status=rt.run_mission("m1")
    assert "reviewer" in seen and "risk" in seen and not status.done
    assert not rt.store.result("w").success


@pytest.mark.asyncio
async def test_astra001_actual_worker_cannot_mark_no_tool_action_done(tmp_path, monkeypatch):
    from bossman import runner
    calls=[]
    async def db_execute(sql, *args): calls.append((sql, args))
    async def fetchrow(*args): return {"id":1}
    async def noop(*args, **kw): return None
    async def compute(*args): return 0, []
    async def model(*args, **kw): return {"content":"I wrote the file", "tool_calls":[], "_usage":{"prompt_tokens":4, "completion_tokens":5}}
    agent=SimpleNamespace(name="probe", model="local", max_steps=2, timeout_min=1, max_tokens=10000,
                          memory="", tools=[], task_types=[], prompt="")
    monkeypatch.setattr(runner, "load_all", lambda:{"probe":agent})
    monkeypatch.setattr(runner, "_system_prompt", lambda _:"test")
    monkeypatch.setattr(runner, "_tool_schemas", lambda _:[])
    monkeypatch.setattr(runner, "_select_compute", compute)
    monkeypatch.setattr(runner, "real_window", lambda _:8192)
    monkeypatch.setattr(runner, "chat", model)
    monkeypatch.setattr(runner.db, "fetchrow", fetchrow)
    monkeypatch.setattr(runner.db, "execute", db_execute)
    monkeypatch.setattr(runner, "_WM", SimpleNamespace(create_task_state=noop, update_task_state=noop))
    monkeypatch.setattr(runner.failure_memory, "record_failure", noop)
    monkeypatch.setattr(runner.events, "emit", lambda *a, **k:None)
    monkeypatch.setattr(runner.settings, "workspace_dir", tmp_path)
    monkeypatch.setattr(runner, "_learning_excluded", lambda _:True)
    await runner.run_task({"id":1, "agent":"probe", "text":"Write file x", "source":"test",
                           "completion_contract":{"mode":"action", "files":[{"path":"x"}]}})
    terminal=[args for sql,args in calls if "UPDATE tasks SET status=$2" in sql]
    assert terminal and terminal[-1][1] == "unverified"
    assert not (tmp_path / "probe" / "x").exists()


def test_reaudit_journal_stale_writer_cannot_erase_durable_intent(tmp_path):
    first = TaskJournal.start(task_id="cas", plan=[("s", "send")], root=tmp_path)
    stale = TaskJournal.load(task_id="cas", root=tmp_path)
    first.begin("s")
    with pytest.raises(JournalIntegrityError, match="changed"):
        stale.begin("s")
    persisted = TaskJournal.load(task_id="cas", root=tmp_path)
    assert persisted.steps[0].attempt_id == first.steps[0].attempt_id
    assert persisted.steps[0].in_flight
    with pytest.raises(JournalIntegrityError):
        TaskJournal.start(task_id="cas", plan=[("s", "send")], root=tmp_path)


def test_reaudit_journal_competing_processes_have_single_winner(tmp_path):
    TaskJournal.start(task_id="race", plan=[("s", "send")], root=tmp_path)
    import subprocess, sys
    worker = '''from pathlib import Path
import sys, time
from bossman_v3.memory.journal import TaskJournal, JournalIntegrityError
root = Path(sys.argv[1]); identity = sys.argv[2]
j = TaskJournal.load(task_id="race", root=root)
(root / identity).touch()
while not (root / "go").exists(): time.sleep(.005)
try: j.begin("s"); sys.exit(0)
except JournalIntegrityError: sys.exit(3)
'''
    processes = [subprocess.Popen([sys.executable, "-c", worker, str(tmp_path), str(i)]) for i in range(2)]
    try:
        deadline = time.monotonic() + 10
        while not all((tmp_path / str(i)).exists() for i in range(2)):
            assert time.monotonic() < deadline
            time.sleep(.01)
        (tmp_path / "go").touch()
        assert sorted(p.wait(timeout=10) for p in processes) == [0, 3]
    finally:
        for p in processes:
            if p.poll() is None: p.kill()
            p.wait()
    assert TaskJournal.load(task_id="race", root=tmp_path).steps[0].in_flight


def test_reaudit_journal_interrupted_writer_blocks_replay(tmp_path):
    j = TaskJournal.start(task_id="interrupted", plan=[("s", "send")], root=tmp_path)
    (tmp_path / "interrupted.lock").write_text("interrupted process")
    with pytest.raises(JournalIntegrityError, match="interrupted"):
        j.begin("s")
    assert TaskJournal.load(task_id="interrupted", root=tmp_path).steps[0].status == "PENDING"


@pytest.mark.parametrize("phase", ["record", "finish"])
def test_reaudit_completion_read_errors_fail_closed(tmp_path, monkeypatch, phase):
    gate = CompletionGate(CompletionContract(mode="action", files=[FileObligation(path="x")]), tmp_path)
    (tmp_path / "x").write_text("expected")
    if phase == "finish":
        gate.record("fs.write", "write", {"path": "x", "content": "expected"}, error=False)
    def denied(path): raise PermissionError("file became unreadable")
    monkeypatch.setattr(gate, "_read", denied)
    if phase == "record":
        gate.record("fs.write", "write", {"path": "x", "content": "expected"}, error=False)
    assert gate.finish()[0] == "unverified"


def test_reaudit_completion_read_bound_survives_growth(tmp_path):
    gate = CompletionGate(CompletionContract(), tmp_path)
    path = tmp_path / "large"
    path.write_bytes(b"x" * 8_000_001)
    with pytest.raises(ValueError, match="byte budget"):
        gate._read(path)


@pytest.mark.parametrize("address", ["100.64.0.1", "100.127.255.254", "::ffff:100.64.0.1"])
def test_reaudit_shared_address_space_is_not_public_egress(monkeypatch, address):
    monkeypatch.delenv("BOSSMAN_HTTP_ALLOW_PRIVATE", raising=False)
    monkeypatch.delenv("BOSSMAN_HTTP_ALLOW_HOSTS", raising=False)
    monkeypatch.setattr(net, "_resolve_host", lambda host: [ipaddress.ip_address(address)])
    with pytest.raises(net.EgressDenied):
        net.check_url("https://example.org", pins={})


@pytest.mark.asyncio
async def test_reaudit_dns_does_not_block_deadline_or_grant_late_pins(monkeypatch):
    from threading import Event
    entered, release = Event(), Event()
    def resolve(url, *, pins):
        entered.set()
        release.wait(2)
        pins["example.org"] = "8.8.8.8"
        return url
    monkeypatch.setattr(net, "check_url", resolve)
    sent = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: sent.append(r))) as client:
        client._bossman_pins = {}
        try:
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(.05):
                    await net._request_checked(client, "GET", "https://example.org", json_body=None, params=None)
            assert entered.is_set()
            assert not sent
        finally:
            release.set()
        await asyncio.sleep(.02)
        assert client._bossman_pins == {}


def test_f005_fake_128gb_topology_and_residency():
    scheduler = FleetScheduler()
    unified = NodeState("fake-128", ram_gb=128, gpu_memory_gb=128, unified_memory=True,
                        models={"configured-model"}, warm_models={"configured-model"})
    both = PlacementRequirement(min_ram_gb=64, min_gpu_memory_gb=64)
    assert scheduler.reject_reasons(unified, both)
    dedicated = replace(unified, node_id="dedicated", unified_memory=False)
    assert not scheduler.reject_reasons(dedicated, both)
    fitting = PlacementRequirement(min_ram_gb=32, min_gpu_memory_gb=64,
                                   required_models=("configured-model",))
    assert not scheduler.reject_reasons(unified, fitting)
    # Resident models/OS consume the same measured physical headroom.
    resident = replace(unified, ram_used_gb=40, gpu_memory_used_gb=40)
    assert scheduler.reject_reasons(resident, fitting)
