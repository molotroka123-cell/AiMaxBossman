"""ComfyUIVideoClient wire behaviour against httpx.MockTransport (no network, no GPU)."""
from __future__ import annotations

import json

import httpx
import pytest

from bcc.direct_gen.client import ComfyUIVideoClient, apply_ws_message, classify_error, validate_template


def make(handler):
    return ComfyUIVideoClient("http://127.0.0.1:8188", transport=httpx.MockTransport(handler))


async def test_interrupt_targets_only_this_prompt():
    calls = []

    def handler(req: httpx.Request):
        calls.append((req.method, req.url.path, json.loads(req.content or b"{}")))
        return httpx.Response(200, json={})
    await make(handler).interrupt("abc-1")
    assert ("POST", "/interrupt", {"prompt_id": "abc-1"}) in calls
    assert ("POST", "/queue", {"delete": ["abc-1"]}) in calls
    assert all(body != {} for _, path, body in calls)  # never a bare global interrupt


async def test_poll_reports_error_text_and_outputs():
    def handler(req: httpx.Request):
        if req.url.path == "/history/p1":
            return httpx.Response(200, json={"p1": {"status": {"status_str": "error", "messages": [
                ["execution_error", {"exception_message": "HIP out of memory"}]]}}})
        if req.url.path == "/history/p2":
            return httpx.Response(200, json={"p2": {"status": {"status_str": "success", "completed": True},
                                                    "outputs": {"9": {"images": [
                                                        {"filename": "v.mp4", "subfolder": "B", "type": "output"},
                                                        {"filename": "t.png", "subfolder": "", "type": "temp"}]}}}})
        return httpx.Response(200, json={})
    c = make(handler)
    failed = await c.poll("p1")
    assert failed["state"] == "failed" and classify_error(failed["error"]) == "out_of_memory"
    done = await c.poll("p2")
    assert done["state"] == "completed" and [d["filename"] for d in done["outputs"]] == ["v.mp4"]


async def test_poll_never_calls_a_missing_history_completed():
    def handler(req: httpx.Request):
        return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
    assert (await make(handler).poll("zz"))["state"] == "pending"


def test_ws_progress_is_taken_only_from_valid_real_messages():
    state: dict = {}
    apply_ws_message(state, {"type": "progress", "data": {"value": 3, "max": 20, "prompt_id": "p"}})
    assert state == {"p": {"value": 3, "max": 20}}
    apply_ws_message(state, {"type": "progress", "data": {"value": 99, "max": 20, "prompt_id": "p"}})
    apply_ws_message(state, {"type": "progress", "data": {"value": 1, "max": 0, "prompt_id": "p"}})
    apply_ws_message(state, {"type": "status", "data": {"prompt_id": "q"}})
    assert state == {"p": {"value": 3, "max": 20}}


def test_template_validation_is_bounded():
    validate_template({"1": {"class_type": "X", "inputs": {}}})
    for bad in ({}, {"a": {"class_type": "X", "inputs": {}}}, {"1": {"class_type": "X"}}, []):
        with pytest.raises(ValueError):
            validate_template(bad)
