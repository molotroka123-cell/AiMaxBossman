"""Host-bound single-machine evidence; no cloud or external API fixtures."""
import asyncio
import builtins
import json
import os
from dataclasses import asdict, replace
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from bossman_shared import reality_guard as guard
from bossman_shared.reality.contracts import Effect, Mission, Obligation, digest
from bossman_shared.reality.host import LocalHost, persistent_authority
from bossman_shared.reality.policy import Constitution
from bossman_shared.reality.proof import ProofAuthority


def fixture_host(root, *, executor="worker", run="r1", action="file.write", args=None, fence=lambda *a: True):
    target = root / "result.txt"
    args = args or {"path": str(target), "content": "verified"}
    policy = Constitution("test-owner-v1", allowed_actions=(action,), allowed_targets=(str(target),),
                          verifiers=("reader",))
    host = LocalHost(root / "reality.sqlite", policy=policy,
        authority=ProofAuthority({"reader": b"r" * 32}, {"reader": "host-reader"}),
        observers={"reader": lambda target: Path(target).read_text(encoding="utf-8")},
        actions={action: None}, fence_check=fence, level_provider=lambda: 1)
    mission = Mission("m1", run, "controlled file write", executor, policy.fingerprint,
        (Obligation("written", str(target), digest("verified"), "reader"),),
        (Effect("write", str(target), action, digest(args), "written", "local-file", "read-file"),))
    return host, mission, args, target


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "STATE_ROOT", tmp_path / "protected")
    monkeypatch.setattr(guard, "_hosts", {})
    monkeypatch.setenv("BOSSMAN_REALITY_ENABLED", "1")
    host, mission, args, target = fixture_host(tmp_path)
    guard.install("local", host)
    guard.enroll("core", 1, "r1", asdict(mission), trusted_ir=asdict(mission), profile="local")
    return host, mission, args, target


def write(setup):
    host, mission, args, target = setup
    async def invoke():
        target.write_text("verified", encoding="utf-8")
        return "done"
    return asyncio.run(guard.dispatch("core", 1, "r1", "worker", "file.write", args, invoke))


def test_real_file_dispatch_and_restart_proof(setup):
    write(setup)
    host, mission, _, target = setup
    # Reconstruct host/SQLite connections; exact IR and independent read survive.
    restored, _, _, _ = fixture_host(target.parent)
    guard.install("local", restored)
    guard.require_complete("core", 1, "r1")
    assert target.read_text() == "verified"
    assert restored.load(mission.id) == mission


@pytest.mark.parametrize("change", ["run", "actor", "args", "module", "profile", "store", "payload"])
def test_fail_closed_before_io(setup, monkeypatch, change):
    host, mission, args, target = setup
    run, actor = "r1", "worker"
    if change == "run": run = "r2"
    if change == "actor": actor = "renamed-worker"
    if change == "args": args = dict(args, content="changed")
    if change == "profile": guard._hosts.clear()
    if change == "store": host.path.unlink()
    if change == "payload":
        host.call(lambda rt: rt.store.db.execute("UPDATE missions SET payload='{}'"))
    if change == "module":
        original = builtins.__import__
        def no_module(name, *a, **kw):
            if "reality.contracts" in name:
                raise ImportError("module unavailable")
            return original(name, *a, **kw)
        monkeypatch.setattr(builtins, "__import__", no_module)
    async def invoke():
        pytest.fail("unauthorized IO")
    with pytest.raises(guard.RealityBlocked):
        asyncio.run(guard.dispatch("core", 1, run, actor, "file.write", args, invoke))
    assert not target.exists()


def test_disable_flag_keeps_existing_run_gated(setup, monkeypatch):
    monkeypatch.setenv("BOSSMAN_REALITY_ENABLED", "0")
    with pytest.raises(Exception):
        guard.require_complete("core", 1, "r1")
    host, mission, _, _ = setup
    with pytest.raises(guard.RealityBlocked):
        guard.enroll("core", 2, "r1", asdict(mission), trusted_ir=asdict(mission), profile="local")


def test_unrelated_tasks_do_not_need_optional_module(setup):
    guard._hosts.clear()
    guard.require_complete("core", "unrelated", 9)


def test_proposal_cannot_downgrade_host_privacy_or_risk(setup):
    _, mission, _, _ = setup
    for edited in (replace(mission, privacy="PUBLIC"), replace(mission, executor="model"),
                   replace(mission, effects=(replace(mission.effects[0], required_level=0),))):
        with pytest.raises(guard.RealityBlocked):
            guard.enroll("core", 2, "r1", asdict(edited), trusted_ir=asdict(mission), profile="local")


def test_lease_lost_after_write_retains_escrow(setup):
    host, mission, args, target = setup
    valid = [True]
    host.fence_check = lambda *a: valid[0]
    async def invoke():
        target.write_text("verified")
        valid[0] = False
    with pytest.raises(guard.RealityBlocked):
        asyncio.run(guard.dispatch("core", 1, "r1", "worker", "file.write", args, invoke))
    assert host.call(lambda rt: rt.store.db.execute("SELECT state FROM effects").fetchone()[0]) == "EFFECT_ESCROW"
    with pytest.raises(Exception): guard.require_complete("core", 1, "r1")


def test_cancel_during_io_does_not_replay(setup):
    _, _, args, target = setup
    async def main():
        started = asyncio.Event()
        async def invoke():
            target.write_text("verified")
            started.set()
            await asyncio.Event().wait()
        task = asyncio.create_task(guard.dispatch("core", 1, "r1", "worker", "file.write", args, invoke))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        with pytest.raises(guard.RealityBlocked):
            await guard.dispatch("core", 1, "r1", "worker", "file.write", args, invoke)
    asyncio.run(main())


def test_slow_observer_off_async_event_loop(setup):
    host, _, _, target = setup
    release = threading.Event()
    observing = threading.Event()
    def observer(path):
        observing.set()
        assert release.wait(3)
        return Path(path).read_text()
    host.observers["reader"] = observer
    async def main():
        async def invoke(): target.write_text("verified")
        task = asyncio.create_task(guard.dispatch("core", 1, "r1", "worker", "file.write", setup[2], invoke))
        assert await asyncio.to_thread(observing.wait, 3)
        release.set()  # This coroutine must run while the observer is blocked.
        await task
    asyncio.run(asyncio.wait_for(main(), 5))


@pytest.mark.parametrize("after_write", [False, True])
def test_killed_process_recovery_without_duplicate(setup, after_write):
    host, mission, args, target = setup
    script = '''
import sys, time
from pathlib import Path
from bossman_shared.reality.store import RealityStore
from bossman_shared.reality.contracts import RealityCompiler
import json
store=RealityStore(sys.argv[1])
row=store.db.execute('SELECT payload FROM missions').fetchone()
m=RealityCompiler().compile(json.loads(row[0]))
store.claim(m, 'write', 'worker')
if sys.argv[3]=='True':
    Path(sys.argv[2]).write_text('verified')
    Path(sys.argv[2]+'.count').write_text('1')
print('ready', flush=True)
time.sleep(30)
'''
    process = subprocess.Popen([sys.executable, "-c", script, str(host.path), str(target), str(after_write)],
                               stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "ready"
    finally:
        process.kill()
        process.wait(timeout=5)
        process.stdout.close()
    assert process.returncode != 0
    restored, _, _, _ = fixture_host(target.parent)
    guard.install("local", restored)
    if after_write:
        restored.reconcile_written(mission.id, "write")
        guard.require_complete("core", 1, "r1")
        assert Path(str(target) + ".count").read_text() == "1"
    else:
        # Parent has joined the killed worker; until terminal status is known,
        # absence is insufficient. This API cannot be called by a model tool.
        with pytest.raises(guard.RealityBlocked): write(setup)
        state = restored.call(lambda rt: rt.store.reconcile_absent(mission, "write", "worker", 1,
            absence_verified=not target.exists(), prior_attempt_terminal=process.poll() is not None,
            reference="controlled-child-killed-and-joined"))
        assert state == "SAFE_TO_RETRY"
        write(setup)
        guard.require_complete("core", 1, "r1")


def test_unknown_attempt_is_not_retryable(setup):
    host, mission, _, _ = setup
    fence = host.call(lambda rt: rt.store.claim(mission, "write", "worker"))
    state = host.call(lambda rt: rt.store.reconcile_absent(mission, "write", "worker", fence,
        absence_verified=True, prior_attempt_terminal=False, reference="unknown-provider-status"))
    assert state == "MANUAL_REVIEW_REQUIRED"
    with pytest.raises(guard.RealityBlocked): write(setup)


def test_stable_signing_key_across_restart(tmp_path, monkeypatch):
    from bossman_shared import evidence
    monkeypatch.setenv(evidence.ENV_KEY_FILE, str(tmp_path / "keys" / "evidence.key"))
    evidence.reset_cache()
    first = persistent_authority({"reader": "host-reader"})
    host, mission, _, target = fixture_host(tmp_path)
    target.write_text("verified")
    receipt = first.observe(mission, "written", lambda p: Path(p).read_text())
    evidence.reset_cache()
    second = persistent_authority({"reader": "host-reader"})
    assert second.check(mission, receipt)
    with pytest.raises(Exception):
        persistent_authority({"reader": "worker"}).check(mission, receipt)
    evidence.reset_cache()


def test_quarantine_blocks_real_dispatch(setup):
    from bossman_shared.reality.intelligence import Bid, LearningLedger
    host, _, _, target = setup
    ledger = LearningLedger(str(host.path) + ".learning")
    ledger.settle("failed-host-mission", Bid("file.write", .8, 0, 1, 0, 1),
                  verified_success=False, hard_fail=True)
    ledger.close()
    with pytest.raises(guard.RealityBlocked): write(setup)
    assert not target.exists()


def test_paid_and_cloud_paths_blocked_before_call(setup):
    with pytest.raises(guard.RealityBlocked):
        guard.block_unmetered_model("core", 1, "r1")


def test_poststate_divergence_restricts_after_restart(setup):
    host, _, args, target = setup
    async def invoke(): target.write_text("unexpected")
    with pytest.raises(guard.RealityBlocked):
        asyncio.run(guard.dispatch("core", 1, "r1", "worker", "file.write", args, invoke))
    restored, _, _, _ = fixture_host(target.parent)
    assert restored.effective_level() == 0
    guard.install("local", restored)
    with pytest.raises(guard.RealityBlocked): write(setup)


@pytest.mark.parametrize("actor", [None, "", False])
def test_participating_dispatch_requires_actor(setup, actor):
    _, _, args, target = setup
    async def invoke():
        target.write_text("verified")
    with pytest.raises(guard.RealityBlocked):
        asyncio.run(guard.dispatch("core", 1, "r1", actor, "file.write", args, invoke))
    assert not target.exists()


@pytest.mark.parametrize("change", ["fence", "policy", "arguments"])
def test_async_preflight_rechecks_after_adapter_wait(setup, change):
    host, _, args, target = setup
    valid = [True]
    level = [1]
    host.fence_check = lambda *a: valid[0]
    host.level_provider = lambda: level[0]
    async def adapter_fence():
        await asyncio.sleep(0)
        if change == "fence":
            valid[0] = False
        elif change == "policy":
            level[0] = 0
        else:
            args["content"] = "unauthorized"
    async def invoke():
        target.write_text(args["content"])
    with pytest.raises(guard.RealityBlocked):
        asyncio.run(guard.dispatch("core", 1, "r1", "worker", "file.write", args,
                                   invoke, fence_check=adapter_fence))
    assert not target.exists()
    assert host.call(lambda rt: rt.store.db.execute("SELECT state FROM effects").fetchone()[0]) == "EFFECT_ESCROW"


def test_completion_hook_keeps_fleet_fence(setup):
    host, mission, _, _ = setup
    guard.enroll("bcc", 1, "r1", asdict(mission), trusted_ir=asdict(mission), profile="local")
    write(setup)
    with guard.fleet_fence(lambda: False):
        result = asyncio.run(guard.completion_hook({"id": 1, "agent_id": "worker"}, "r1", "done"))
    assert result["verdict"] == "FAIL"
    assert host.call(lambda rt: rt.store.db.execute("SELECT state FROM missions").fetchone()[0]) == "ACTIVE"


def test_policy_revoked_during_observer_cannot_confirm(setup):
    host, _, _, target = setup
    level = [1]
    host.level_provider = lambda: level[0]
    def observer(path):
        level[0] = 0
        return Path(path).read_text()
    host.observers["reader"] = observer
    with pytest.raises(guard.RealityBlocked):
        write(setup)
    assert target.read_text() == "verified"
    assert host.call(lambda rt: rt.store.db.execute("SELECT state FROM effects").fetchone()[0]) == "EFFECT_ESCROW"


def learning_host(setup):
    from bossman_shared.reality.intelligence import Bid
    old, mission, args, target = setup
    redacted = []
    def redact(value):
        redacted.append(value)
        return value
    host = LocalHost(old.path, policy=old.policy, authority=old.authority,
        observers=old.observers, actions=old.actions, fence_check=old.fence_check,
        level_provider=old.level_provider, route_bids={"file.write": Bid("file.write", .5, 0, 1, 0, 1)},
        learning_redactor=redact)
    guard.install("local", host)
    return host, redacted


def learning_counts(host):
    from bossman_shared.reality.intelligence import LearningLedger
    ledger = LearningLedger(str(host.path) + ".learning")
    try:
        return (ledger.db.execute("SELECT COUNT(*) FROM settlements WHERE success=1").fetchone()[0],
                ledger.db.execute("SELECT COUNT(*) FROM lessons").fetchone()[0])
    finally:
        ledger.close()


def test_learning_confirmed_effect_survives_restart_without_duplicate(setup):
    host, redacted = learning_host(setup)
    _, mission, _, target = setup
    write(setup)
    assert target.read_text() == "verified"
    assert learning_counts(host) == (1, 1)
    assert set(redacted) == {"cause_not_assessed", "independent_poststate_confirmed"}
    restored, _ = learning_host(setup)
    restored.record_confirmed(mission, mission.effects[0], 1)
    assert learning_counts(restored) == (1, 1)
    with pytest.raises(guard.RealityBlocked):
        write(setup)
    assert learning_counts(restored) == (1, 1)


@pytest.mark.parametrize("invalid", ["unconfirmed", "tampered", "stale", "wrong_fence"])
def test_learning_never_settles_without_fresh_bound_proof(setup, invalid):
    host, _ = learning_host(setup)
    _, mission, _, _ = setup
    # Confirm without learning first, then challenge the host audit boundary.
    guard.install("local", setup[0])
    if invalid != "unconfirmed":
        write(setup)
    if invalid == "tampered":
        def tamper(rt):
            raw = json.loads(rt.store.db.execute("SELECT payload FROM receipts").fetchone()[0])
            raw["signature"] = "0" * 64
            rt.store.db.execute("UPDATE receipts SET payload=?", (json.dumps(raw),))
        host.call(tamper)
    if invalid == "stale":
        host.authority.clock = lambda: 9_999_999_999
    with pytest.raises(Exception):
        host.record_confirmed(mission, mission.effects[0], 2 if invalid == "wrong_fence" else 1)
    assert learning_counts(host) == (0, 0)


def test_learning_divergence_contains_hashes_and_fixed_text_only(setup):
    host, _ = learning_host(setup)
    _, _, args, target = setup
    clinical = "private clinical patient narrative must never enter a lesson"
    async def invoke():
        target.write_text(clinical)
    with pytest.raises(guard.RealityBlocked):
        asyncio.run(guard.dispatch("core", 1, "r1", "worker", "file.write", args, invoke))
    assert host.effective_level() == 0
    assert learning_counts(host) == (0, 1)
    import sqlite3
    with sqlite3.connect(str(host.path) + ".learning") as connection:
        payload = connection.execute("SELECT payload FROM lessons").fetchone()[0]
    assert clinical not in payload and str(target) not in payload
    assert "observed_poststate_diverged" in payload
    assert json.loads(payload)["cause_knowledge"] == "INFERRED"


def test_learning_absent_config_does_not_settle_or_record(setup):
    write(setup)
    assert learning_counts(setup[0]) == (0, 0)


def test_learning_audit_failure_retains_confirmed_effect_without_replay(setup, monkeypatch):
    from bossman_shared.reality.intelligence import LearningLedger
    host, _ = learning_host(setup)
    _, mission, _, target = setup
    original = LearningLedger.record_lesson
    def unavailable(*args, **kwargs):
        raise OSError("learning store unavailable")
    monkeypatch.setattr(LearningLedger, "record_lesson", unavailable)
    with pytest.raises(guard.RealityBlocked):
        write(setup)
    assert target.read_text() == "verified"
    assert host.call(lambda rt: rt.store.db.execute("SELECT state FROM effects").fetchone()[0]) == "CONFIRMED"
    assert learning_counts(host) == (0, 0)
    with pytest.raises(guard.RealityBlocked):
        write(setup)
    monkeypatch.setattr(LearningLedger, "record_lesson", original)
    host.record_confirmed(mission, mission.effects[0], 1)
    assert learning_counts(host) == (1, 1)


def test_configured_learning_route_respects_quarantine_before_io(setup):
    from bossman_shared.reality.intelligence import LearningLedger
    host, _ = learning_host(setup)
    ledger = LearningLedger(str(host.path) + ".learning")
    ledger.settle("owner-confirmed-hard-failure", host.route_bids["file.write"],
                  verified_success=False, hard_fail=True)
    ledger.close()
    with pytest.raises(guard.RealityBlocked):
        write(setup)
    assert not setup[3].exists()
    assert learning_counts(host) == (0, 0)


@pytest.mark.parametrize("invalid", ["paid", "cloud", "action", "redactor"])
def test_learning_config_is_local_zero_cost_host_owned(setup, invalid):
    from bossman_shared.reality.intelligence import Bid
    from bossman_shared.reality.contracts import RealityError
    old = setup[0]
    bid = Bid("different" if invalid == "action" else "file.write", .5,
              1 if invalid == "paid" else 0, 1, 0, 1, local=invalid != "cloud")
    with pytest.raises(RealityError):
        LocalHost(old.path, policy=old.policy, authority=old.authority,
            observers=old.observers, actions=old.actions, fence_check=old.fence_check,
            level_provider=old.level_provider, route_bids={"file.write": bid},
            learning_redactor=None if invalid == "redactor" else str)


def test_learning_transport_failure_never_infers_success_or_quarantine(setup):
    from bossman_shared.reality.intelligence import LearningLedger
    host, _ = learning_host(setup)
    async def unavailable():
        raise OSError("transport outcome unknown")
    with pytest.raises(guard.RealityBlocked):
        asyncio.run(guard.dispatch("core", 1, "r1", "worker", "file.write", setup[2], unavailable))
    assert learning_counts(host) == (0, 0)
    ledger = LearningLedger(str(host.path) + ".learning")
    try:
        assert ledger.reputation("file.write")["quarantined"] is False
    finally:
        ledger.close()
    assert host.call(lambda rt: rt.store.db.execute("SELECT state FROM effects").fetchone()[0]) == "EFFECT_ESCROW"
