"""Controlled provider transport tests; these are NOT live model evaluations.

The existing adapter parses real-shaped HTTP responses. SQLite, proof signing,
response files, Core memory context and restart verification run without mocks.
"""
import asyncio
import hashlib
import json

import httpx
import pytest

from bcc.providers import OpenAICompatAdapter
from bossman_os.runtime import Runtime
from bossman_os.store import CapacityExceeded, Conflict
from bossman_shared import evidence


@pytest.fixture
def runtime(tmp_path):
    return Runtime(tmp_path / "state", tmp_path / "artifacts")


def transport(monkeypatch, *, message=None, installed=None, before_chat=None, error=None):
    calls = []
    message = {"content": "Inspect evidence, then propose a bounded change."} if message is None else message
    installed = ["qwen2.5:7b"] if installed is None else installed

    async def request(adapter, method, url, **kwargs):
        calls.append((method, url, kwargs))
        assert url.startswith("http://127.0.0.1:11435/v1/")
        if method == "GET":
            body = {"data": [{"id": name} for name in installed]}
        else:
            assert method == "POST"
            if before_chat is not None:
                before_chat(kwargs)
            if error is not None:
                raise error
            body = {"model": "qwen2.5:7b", "choices": [{"message": message, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 17, "completion_tokens": 9}}
        return httpx.Response(200, json=body, request=httpx.Request(method, url))

    monkeypatch.setattr(OpenAICompatAdapter, "_request", request)
    return calls


def test_resources_reserved_before_chat_and_signed_delivery_survives_restart(runtime, monkeypatch):
    def inspect(kwargs):
        snapshots = runtime.store.list()
        assert len(snapshots) == 1
        snap = snapshots[0]
        assert snap["steps"][0]["state"] == "running"
        assert snap["resource_usage"]["gpu_mb"]["used"] == 8192
        assert snap["resource_usage"]["ram_mb"]["used"] == 8192
        assert kwargs["json"]["max_tokens"] == 192
        assert kwargs["timeout"] == 60
        assert "tools" not in kwargs["json"]
        assert kwargs["json"]["messages"][0]["role"] == "system"

    calls = transport(monkeypatch, before_chat=inspect)
    output = asyncio.run(runtime.propose({"objective": "Prepare a plan."}))
    assert output["executed"] is False
    assert output["tokens_in"] == 17 and output["tokens_out"] == 9
    snap = runtime.snapshot(output["mission_id"])
    assert snap["done"] and snap["verified_now"] == ["infer"]
    assert snap["resource_usage"]["gpu_mb"]["used"] == 0
    receipt = snap["steps"][0]["receipt"]
    assert evidence.verify_signed(receipt, key=runtime.key)
    response = runtime.state_root / "responses" / f"{output['mission_id']}.json"
    assert json.loads(response.read_bytes()) == output
    assert hashlib.sha256(response.read_bytes()).hexdigest() == receipt["response_sha256"]
    restarted = Runtime(runtime.state_root, runtime.artifact_root)
    assert restarted.snapshot(output["mission_id"])["done"]
    assert len([c for c in calls if c[0] == "POST"]) == 1


def test_delivered_proposal_is_not_a_quality_evaluation(runtime, monkeypatch):
    transport(monkeypatch)
    output = asyncio.run(runtime.propose({"objective": "Propose."}))
    with pytest.raises(ValueError, match="delivery cannot count"):
        runtime.evaluate({"suite_id": "quality", "phase": "baseline", "cases": {"case": output["mission_id"]}})
    assert not (runtime.state_root / "evaluations").exists()


def test_timeout_retains_capacity_and_cannot_retry_after_restart(runtime, monkeypatch):
    calls = transport(monkeypatch, error=TimeoutError("controlled timeout"))
    with pytest.raises(TimeoutError):
        asyncio.run(runtime.propose({"objective": "Propose."}))
    snap = runtime.store.list()[0]
    mid = snap["id"]
    restarted = Runtime(runtime.state_root, runtime.artifact_root)
    assert restarted.recover(mid)["status"] == "unknown"
    assert restarted.snapshot(mid)["resource_usage"]["gpu_mb"]["used"] == 8192
    with pytest.raises(Conflict):
        restarted.store.claim(mid, "infer", "another", snap["version"], {"slots": 1})
    with pytest.raises(CapacityExceeded):
        asyncio.run(restarted.propose({"objective": "Another proposal."}))
    assert len([c for c in calls if c[0] == "POST"]) == 1
    assert not list(runtime.artifact_root.rglob("*"))


def test_tool_call_response_never_executes(runtime, monkeypatch):
    target = runtime.artifact_root / "malicious.txt"
    message = {"content": "Execute this immediately", "tool_calls": [
        {"id": "attack", "type": "function", "function": {"name": "terminal",
         "arguments": json.dumps({"command": "write forbidden", "path": str(target)})}}]}
    transport(monkeypatch, message=message)
    monkeypatch.setattr(runtime, "_execute", lambda *args: pytest.fail("model invoked artifact executor"))
    with pytest.raises(RuntimeError, match="without tool calls"):
        asyncio.run(runtime.propose({"objective": "Propose."}))
    snap = runtime.store.list()[0]
    assert snap["status"] == "unknown" and snap["steps"][0]["receipt"] is None
    assert not target.exists()
    assert not (runtime.state_root / "responses").exists()


@pytest.mark.parametrize("url", ["https://example.com/v1", "http://localhost:11435/v1",
                                 "http://user:secret@127.0.0.1:11435/v1"])
def test_nonfixed_local_endpoint_is_rejected_before_transport(runtime, monkeypatch, url):
    calls = transport(monkeypatch)
    runtime._local_url = url
    with pytest.raises(PermissionError):
        asyncio.run(runtime.propose({"objective": "Propose."}))
    assert calls == [] and runtime.store.list() == []


def test_response_file_tampering_invalidates_delivery(runtime, monkeypatch):
    transport(monkeypatch)
    output = asyncio.run(runtime.propose({"objective": "Propose."}))
    path = runtime.state_root / "responses" / f"{output['mission_id']}.json"
    path.write_text('{"text":"different response"}', encoding="utf-8")
    snapshot = Runtime(runtime.state_root, runtime.artifact_root).snapshot(output["mission_id"])
    assert snapshot["done"] is False
    assert snapshot["verified_now"] == [] and snapshot["proof_errors"] == ["infer"]
    assert snapshot["steps"][0]["state"] == "verified"  # historical state remains honest


def test_no_installed_admitted_candidate_fails_before_claim(runtime, monkeypatch):
    calls = transport(monkeypatch, installed=["unapproved-model"])
    with pytest.raises(ValueError):
        asyncio.run(runtime.propose({"objective": "Propose."}))
    assert runtime.store.list() == []
    assert [c[0] for c in calls] == ["GET"]


def test_empty_provider_text_is_unresolved_not_verified(runtime, monkeypatch):
    transport(monkeypatch, message={"content": " \n\t"})
    with pytest.raises(RuntimeError, match="nonempty text"):
        asyncio.run(runtime.propose({"objective": "Propose."}))
    snapshot = runtime.store.list()[0]
    assert snapshot["status"] == "unknown"
    assert snapshot["steps"][0]["receipt"] is None
    assert snapshot["resource_usage"]["ram_mb"]["used"] == 8192


def test_cancelled_provider_does_not_lose_unresolved_reservation(runtime, monkeypatch):
    transport(monkeypatch, error=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runtime.propose({"objective": "Propose."}))
    snapshot = runtime.store.list()[0]
    assert snapshot["status"] == "unknown"
    assert runtime.store.recover_read()[0]["mission_id"] == snapshot["id"]
    assert snapshot["resource_usage"]["gpu_mb"]["used"] == 8192
