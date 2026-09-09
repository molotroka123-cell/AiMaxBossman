"""MF-033: real route contract and explicit refusal before native video queueing."""
from __future__ import annotations

import pytest

from .test_video_studio_integration import BASE, execute_task, op


@pytest.mark.parametrize("prompt,reason", [
    ("Склей эти два видео", "VIDEO_MEDIA_REQUIRED"),
    ("Сделай Reels из этого ролика", "VIDEO_MEDIA_REQUIRED"),
    ("Добавь русские субтитры", "VIDEO_COMMAND_REQUIRED"),
    ("Открой этот проект и замени музыку", "VIDEO_COMMAND_REQUIRED"),
    ("Склей видео и добавь русские субтитры", "VIDEO_COMMAND_REQUIRED"),
])
async def test_video_chat_preflight_refuses_without_queueing_or_mutation(env, prompt, reason):
    response = await env.client.post(BASE + "/chat", json={"text": prompt, "operation_id": op()})
    assert response.status_code == 200, response.text
    chat = response.json()
    before = (await env.client.get(BASE + "/projects/" + chat["project_id"])).json()
    run = await env.client.post(BASE + f"/chat/{chat['task_id']}/run")
    assert run.status_code == 422, run.text
    assert reason in run.text
    state = (await env.client.get(f"/api/tasks/{chat['task_id']}")).json()
    assert state["task"]["status"] == "draft"
    assert state["runs"] == []
    assert (await env.client.get(BASE + "/projects/" + chat["project_id"])).json() == before


async def test_video_generic_queue_rechecks_readiness_with_safe_reason(env):
    chat = (await env.client.post(BASE + "/chat", json={
        "text": "Склей эти два видео", "operation_id": op()})).json()
    # The ordinary task route cannot bypass the native executor's readiness check.
    await env.svc.engine.enqueue(chat["task_id"])
    result = await execute_task(env, chat["task_id"])
    assert result["task"]["status"] == "failed"
    assert "VIDEO_MEDIA_REQUIRED" in result["error"], result
    assert "ValueError" not in result["error"]
    assert result["result"] is None


async def test_video_commands_are_registered_and_return_typed_validation(env):
    feature = next(feature for feature in env.svc.features if feature.name == "video_studio")
    routes = [route for route in feature.router.routes
              if getattr(route, "path", None) == "/video-studio/commands"
              and "POST" in getattr(route, "methods", set())]
    assert len(routes) == 1
    # The historical URL remains the canonical command endpoint, not obsolete.
    invalid = await env.client.post(BASE + "/commands", json={})
    assert invalid.status_code == 422, invalid.text


async def test_preflight_recovery_uses_original_draft_after_actual_media_upload(env, tmp_path):
    """Recover by attaching real bytes, without creating another task or run."""
    import subprocess
    fixture = tmp_path / "recovery.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                    "color=blue:s=160x90:r=25:d=0.4", "-c:v", "libx264",
                    "-threads", "1", "-pix_fmt", "yuv420p", str(fixture)],
                   check=True, timeout=20)
    payload = {"text": "Склей эти два видео", "operation_id": op()}
    chat = (await env.client.post(BASE + "/chat", json=payload)).json()
    endpoint = BASE + f"/chat/{chat['task_id']}/run"
    assert (await env.client.post(endpoint)).status_code == 422
    assert (await env.client.post(BASE + "/chat", json=payload)).json() == chat
    uploaded = await env.client.post(BASE + "/media", params={
        "project_id": chat["project_id"], "filename": fixture.name,
        "expected_revision": 0, "operation_id": op()}, content=fixture.read_bytes())
    assert uploaded.status_code == 200, uploaded.text
    assert (await env.client.post(endpoint)).status_code == 200
    assert (await env.client.post(endpoint)).status_code == 200
    result = await execute_task(env, chat["task_id"])
    assert result["task"]["status"] == "completed", result
    assert len(result["runs"]) == 1
    project = (await env.client.get(BASE + "/projects/" + chat["project_id"])).json()
    assert len(project["sequences"][0]["tracks"][0]["clips"]) == 1


async def test_concurrent_video_start_does_not_create_duplicate_runs(env, monkeypatch):
    import asyncio
    chat = (await env.client.post(BASE + "/chat", json={
        "text": "Открой этот проект", "operation_id": op()})).json()
    ready = asyncio.Event()
    arrivals = 0
    original = env.svc.video_studio.check_edit_ready

    async def barrier(task):
        nonlocal arrivals
        await original(task)
        arrivals += 1
        if arrivals == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), 5)

    monkeypatch.setattr(env.svc.video_studio, "check_edit_ready", barrier)
    responses = await asyncio.gather(*(
        env.client.post(BASE + f"/chat/{chat['task_id']}/run") for _ in range(2)))
    assert all(response.status_code == 200 for response in responses)
    state = (await env.client.get(f"/api/tasks/{chat['task_id']}")).json()
    assert len(state["runs"]) == 1, state


async def test_video_start_cannot_resurrect_owner_stopped_draft(env, monkeypatch):
    chat = (await env.client.post(BASE + "/chat", json={
        "text": "Открой этот проект", "operation_id": op()})).json()
    original = env.svc.video_studio.check_edit_ready

    async def stop_after_preflight(task):
        await original(task)
        await env.svc.engine.stop(task["id"])

    monkeypatch.setattr(env.svc.video_studio, "check_edit_ready", stop_after_preflight)
    response = await env.client.post(BASE + f"/chat/{chat['task_id']}/run")
    assert response.status_code == 409, response.text
    state = (await env.client.get(f"/api/tasks/{chat['task_id']}")).json()
    assert state["task"]["status"] == "stopped" and state["runs"] == [], state
