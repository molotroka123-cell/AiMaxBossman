"""Actual addon Runtime + Core memory + V3 execution, confined temporary roots.

No provider/network or live Bossman database is used. Fault injection replaces
only the confirmation boundary; file effects, signatures and SQLite are real.
"""
from copy import deepcopy
import json

import pytest

from bossman_os.runtime import Runtime, canonical
from bossman_shared import evidence


@pytest.fixture
def runtime(tmp_path):
    return Runtime(tmp_path / "protected", tmp_path / "artifacts")


def mission(mid="mission", *, project="project-a", dependent=True, content="verified UTF-8: λ\n"):
    steps = [{"id": "write", "depends_on": [], "action": "artifact.write",
              "path": "nested/result.txt", "content": content}]
    if dependent:
        steps.append({"id": "verify", "depends_on": ["write"], "action": "artifact.verify",
                      "path": "nested/result.txt", "content": content})
    return {"id": mid, "project": project, "steps": steps, "context_roots": []}


def test_actual_write_dependent_verify_signed_proofs_and_idempotent_rerun(runtime, monkeypatch):
    payload = mission()
    admitted = runtime.submit(payload)
    assert admitted["status"] == "ready"
    result = runtime.run("mission")
    assert result["done"] is True
    assert result["verified_now"] == ["write", "verify"]
    target = runtime.artifact_root / "mission/nested/result.txt"
    assert target.read_bytes() == payload["steps"][0]["content"].encode("utf-8")
    for step in result["steps"]:
        assert evidence.verify_signed(step["receipt"], key=runtime.key)
        assert step["receipt"]["actor"] != step["receipt"]["verifier_principal"]
    claimed = [e["step_id"] for e in result["events"] if e["kind"] == "claimed"]
    assert claimed == ["write", "verify"]
    assert all(item["used"] == 0 for item in result["resource_usage"].values())
    def forbidden(*args, **kwargs):
        pytest.fail("verified mission must not re-execute a side effect")
    monkeypatch.setattr(runtime, "_execute", forbidden)
    repeated = runtime.run("mission")
    assert repeated["done"] is True and repeated["version"] == result["version"]
    assert repeated["events"] == result["events"]


@pytest.mark.parametrize("path", ["../escape.txt", "/outside.txt", "a/../escape.txt", "a//file.txt",
                                 "a\\file.txt", "C:/escape.txt", "file.txt:stream", ".", "./file.txt",
                                 "CON", "nul.txt", "AUX", "sub/COM1.txt", "LPT9", "file.", "file ",
                                 "sub/.. ", "CONIN$", "CONOUT$", "file?.txt", "file*.txt", "bad\x00.txt"])
def test_artifact_path_escape_and_windows_aliases_rejected_before_io(runtime, path):
    with pytest.raises((ValueError, PermissionError)):
        runtime._target("mission", path)
    assert not (runtime.artifact_root / "mission").exists()


def test_mutated_signed_receipt_is_never_a_verified_snapshot(runtime):
    runtime.submit(mission(dependent=False))
    result = runtime.run("mission")
    stored = result["steps"][0]
    receipt = deepcopy(stored["receipt"])
    receipt["observed_sha256"] = "0" * 64
    step = result["contract"]["steps"][0]
    with pytest.raises(RuntimeError):
        runtime._check_receipt("mission", step, receipt)
    # Simulate disk corruption of persisted proof, not a forged model response.
    with runtime.store._connection() as db:
        db.execute("UPDATE steps SET receipt=? WHERE mission=? AND id=?",
                   (canonical(receipt), "mission", "write"))
    snapshot = runtime.snapshot("mission")
    assert snapshot["done"] is False and snapshot["verified_now"] == []


@pytest.mark.parametrize("field,value", [("actor", "wrong-worker"), ("fence", 999),
                                         ("dispatch_binding", "0" * 64)])
def test_valid_signature_cannot_replace_effect_attempt_binding(runtime, field, value):
    runtime.submit(mission(dependent=False))
    result = runtime.run("mission")
    original = result["steps"][0]["receipt"]
    body = {key: value for key, value in original.items() if key not in evidence.SIG_FIELDS}
    body[field] = value
    # A well-formed signature alone is insufficient: the stored owner/fence
    # and declared effect must also match independently.
    signed = body | evidence.sign_fields(body, signer="bossman_v3.verifier", key=runtime.key)
    assert evidence.verify_signed(signed, key=runtime.key)
    with pytest.raises(RuntimeError):
        runtime._check_receipt("mission", result["contract"]["steps"][0], signed)


def test_revoked_owner_capability_prevents_real_file_effect(runtime):
    runtime.submit(mission(dependent=False))
    runtime.owner_capabilities = frozenset()
    with pytest.raises(PermissionError):
        runtime.run("mission")
    assert not (runtime.artifact_root / "mission/nested/result.txt").exists()
    assert runtime.snapshot("mission")["done"] is False


def test_interrupted_confirm_recovers_by_observation_without_replaying_write(runtime, monkeypatch):
    payload = mission(dependent=False)
    runtime.submit(payload)
    original_confirm = runtime.store.confirm
    def lose_confirmation(*args, **kwargs):
        raise RuntimeError("fixture: process lost before durable confirmation")
    monkeypatch.setattr(runtime.store, "confirm", lose_confirmation)
    with pytest.raises(RuntimeError, match="before durable confirmation"):
        runtime.run("mission")
    target = runtime.artifact_root / "mission/nested/result.txt"
    assert target.read_text(encoding="utf-8") == payload["steps"][0]["content"]
    unresolved = runtime.store.snapshot("mission")
    assert unresolved["steps"][0]["state"] == "unknown"
    assert unresolved["resource_usage"]["slots"]["used"] == 1
    monkeypatch.setattr(runtime.store, "confirm", original_confirm)
    restarted = Runtime(runtime.state_root, runtime.artifact_root)
    def forbidden(*args, **kwargs):
        pytest.fail("recovery must observe the existing effect, never replay IO")
    monkeypatch.setattr(restarted, "_execute", forbidden)
    recovered = restarted.recover("mission")
    assert recovered["done"] is True
    assert recovered["resource_usage"]["slots"]["used"] == 0
    assert len([e for e in recovered["events"] if e["kind"] == "claimed"]) == 1
    assert restarted.run("mission")["done"] is True


def test_missing_unknown_effect_stays_unresolved_and_is_not_replayed(runtime, monkeypatch):
    runtime.submit(mission(dependent=False))
    current = runtime.store.snapshot("mission")
    active = runtime.store.claim("mission", "write", "local-artifact-worker", current["version"],
                                 {"slots": 1, "ram_mb": 16, "gpu_mb": 0})
    runtime.store.uncertain("mission", "write", "local-artifact-worker", active["fence"])
    monkeypatch.setattr(runtime, "_execute", lambda *args: pytest.fail("unknown effect replay"))
    recovered = runtime.recover("mission")
    assert recovered["done"] is False
    assert recovered["steps"][0]["state"] == "unknown"
    assert recovered["resource_usage"]["slots"]["used"] == 1
    assert not (runtime.artifact_root / "mission/nested/result.txt").exists()


def test_memory_context_is_project_scoped_and_receipts_do_not_store_raw_artifact(runtime):
    secret = "PRIVATE_ARTIFACT_CONTENT_DO_NOT_PROMOTE"
    runtime.submit(mission(project="project-a", dependent=False, content=secret))
    runtime.run("mission")
    from bossman.context_engine import MemoryStatus
    with runtime.memory() as engine:
        rows = engine.store.memories("project-a", (MemoryStatus.ACTIVE,))
        assert rows
        root = rows[0].memory_id
        assert all(secret not in row.text for row in rows)
    chosen = runtime._context("project-a", [root])
    assert chosen and chosen[0]["source"].startswith("os-receipt:")
    assert chosen[0]["privacy"] == "LOCAL"
    with pytest.raises(ValueError):
        runtime._context("project-b", [root])
    unrelated = mission("other", project="project-b", dependent=False)
    unrelated["context_roots"] = [root]
    with pytest.raises(ValueError):
        runtime.submit(unrelated)


def test_runtime_memory_respects_expired_dependency_of_active_root(runtime):
    with runtime.memory() as engine:
        old = engine.memory.fact("expired supporting observation", project="project-a",
                                 memory_id="expired", source_refs=["fixture:observed"], metadata={"expires_at": 1})
        engine.memory.promote(old.memory_id, verified=True)
        root = engine.memory.fact("decision based on observation", project="project-a",
                                  memory_id="decision", source_refs=["fixture:decision"],
                                  metadata={"depends_on": ["expired"]})
        engine.memory.promote(root.memory_id, verified=True)
    with pytest.raises(ValueError, match="expired"):
        runtime._context("project-a", ["decision"])


def test_evaluated_phase_and_suite_definitions_are_immutable(runtime):
    runtime.submit(mission("case-a", dependent=False))
    baseline_payload = {"suite_id": "fixed-suite", "phase": "baseline", "cases": {"case": "case-a"}}
    baseline = runtime.evaluate(baseline_payload)
    assert baseline["passed"] == 0 and baseline["total"] == 1
    baseline_path = runtime.state_root / "evaluations/fixed-suite.baseline.json"
    original = baseline_path.read_bytes()
    runtime.run("case-a")
    with pytest.raises(ValueError, match="immutable"):
        runtime.evaluate(baseline_payload)
    assert baseline_path.read_bytes() == original
    candidate = runtime.evaluate({**baseline_payload, "phase": "candidate"})
    assert candidate["passed"] == 1 and candidate["release_gate"]["eligible"] is True
    assert candidate["promoted"] is False
    runtime.submit(mission("different", dependent=False, content="different owner contract"))
    runtime.run("different")
    with pytest.raises(ValueError):
        runtime.evaluate({**baseline_payload, "phase": "candidate", "cases": {"case": "different"}})


@pytest.mark.parametrize("invalidation", ["artifact", "receipt"])
def test_invalidated_parent_proof_blocks_dependent_effect(runtime, monkeypatch, invalidation):
    payload = mission(dependent=False)
    payload["steps"].append({"id": "dependent", "depends_on": ["write"], "action": "artifact.write",
                              "path": "dependent.txt", "content": "must require valid parent proof"})
    runtime.submit(payload)
    original_remember = runtime._remember
    def invalidate_after_parent(mid, step, project, receipt):
        original_remember(mid, step, project, receipt)
        if step["id"] != "write":
            return
        if invalidation == "artifact":
            (runtime.artifact_root / mid / step["path"]).write_text("tampered", encoding="utf-8")
        else:
            corrupted = {**receipt, "sig": "0" * 64}
            with runtime.store._connection() as db:
                db.execute("UPDATE steps SET receipt=? WHERE mission=? AND id=?",
                           (json.dumps(corrupted), mid, step["id"]))
    monkeypatch.setattr(runtime, "_remember", invalidate_after_parent)
    try:
        runtime.run("mission")
    except RuntimeError:
        pass  # Explicit refusal or blocked snapshot both preserve the boundary.
    assert not (runtime.artifact_root / "mission/dependent.txt").exists()
    snapshot = runtime.snapshot("mission")
    assert snapshot["done"] is False
    dependent = next(step for step in snapshot["steps"] if step["id"] == "dependent")
    assert dependent["state"] == "ready"
