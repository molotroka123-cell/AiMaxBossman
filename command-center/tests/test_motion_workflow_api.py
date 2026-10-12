"""Motion Studio workflow API: the whole path over HTTP with a real ffmpeg and real files.

materials -> analysis -> plan -> edit -> run -> progress -> result -> reopen after a restart, plus the
negative controls: path escape, oversize upload, stale version, corrupted output, missing ffmpeg, an
orphaned render, a job that was running when the backend died.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from bcc.features import motion_workflow_api as mwa
from bcc.motion_workflow import analyze as analyze_mod
from bcc.motion_workflow import plan as planmod
from bcc.motion_workflow import render as render_mod
from bcc.motion_workflow import store as store_mod

from .conftest import client_for, start_app

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "motion_workflow"))
import synthetic_package  # noqa: E402

ENV = render_mod.environment()
pytestmark = pytest.mark.skipif(
    not ENV["ready"], reason="ffmpeg / ffprobe with libx264 + aac not available here: " + "; ".join(ENV["problems"]))
BASE = "/api/motion-workflow"
TERMINAL = ("done", "failed", "interrupted", "cancelled", "preview_done")


@pytest.fixture(scope="module")
def package(tmp_path_factory):
    folder = tmp_path_factory.mktemp("mwapi-pkg")
    synthetic_package.make_package(folder, size="640x360", fps=25, scene_seconds=2.0, music_seconds=5.0)
    return folder


@pytest.fixture(scope="module")
def big_package(tmp_path_factory):
    folder = tmp_path_factory.mktemp("mwapi-big")
    synthetic_package.make_package(folder, size="1280x720", fps=30, scene_seconds=6.0, music_seconds=5.0)
    return folder


async def create(client, package: Path, title: str = "api test", clip: str = "source.mp4") -> str:
    raw = (package / clip).read_bytes()
    r = await client.post(f"{BASE}/projects", params={"name": "clip.mp4", "title": title}, content=raw,
                          headers={"content-type": "video/mp4"})
    assert r.status_code == 201, r.text
    return r.json()["project"]["project_id"]


async def analyzed(client, package: Path, **kw) -> tuple[str, dict]:
    pid = await create(client, package, **kw)
    r = await client.post(f"{BASE}/projects/{pid}/analyze")
    assert r.status_code == 200, r.text
    return pid, r.json()


async def ops(client, pid: str, *operations: dict, expect: int = 200, version: int | None = None):
    body: dict = {"ops": list(operations)}
    if version is not None:
        body["expected_version"] = version
    r = await client.post(f"{BASE}/projects/{pid}/ops", json=body)
    assert r.status_code == expect, r.text
    return r.json()


async def wait_job(client, pid: str, jid: str, timeout: float = 180.0, until=TERMINAL) -> dict:
    end = time.monotonic() + timeout
    job: dict = {}
    while time.monotonic() < end:
        job = (await client.get(f"{BASE}/projects/{pid}/jobs/{jid}")).json()
        if job["state"] in until:
            return job
        await asyncio.sleep(0.15)
    raise AssertionError(f"job did not settle: {job.get('state')} {job.get('stage')} {job.get('progress')}")


async def run_full(client, pid: str, **body) -> dict:
    r = await client.post(f"{BASE}/projects/{pid}/jobs", json={"mode": "full", **body})
    assert r.status_code == 202, r.text
    return await wait_job(client, pid, r.json()["job_id"])


# ------------------------------------------------------------------------------------------ status / limits

async def test_status_and_limits_are_available_before_any_run(env):
    status = (await env.client.get(f"{BASE}/status")).json()
    assert status["available"] and status["renderer"]["ready"] and status["renderer"]["ffmpeg"]
    assert status["limits"]["max_duration_seconds"] == planmod.LIMITS["max_duration_seconds"]
    assert status["cloud_statement"].startswith("Cloud spend is 0 USD")
    ids = {c["id"] for c in status["capabilities"]["not_executed"]}
    assert {"face_character", "clothes", "background", "product"} <= ids
    limits = (await env.client.get(f"{BASE}/limits")).json()
    assert limits["limits"]["max_references"] == planmod.LIMITS["max_references"]
    assert limits["cost_model"]["cloud_spend_usd"] == 0.0 and limits["cost_model"]["paid_calls"] == 0


async def test_endpoints_need_the_owner_session(env):
    import httpx
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url="http://test") as anon:
        assert (await anon.get(f"{BASE}/status")).status_code == 401
        assert (await anon.get(f"{BASE}/projects")).status_code == 401
        assert (await anon.post(f"{BASE}/projects", json={})).status_code == 401


# ------------------------------------------------------------------------------------------ create / analyze

async def test_upload_creates_a_project_and_analysis_keeps_unknown_as_unknown(env, package):
    pid = await create(env.client, package, title="Мой ролик")
    opened = (await env.client.get(f"{BASE}/projects/{pid}")).json()
    assert opened["project"]["title"] == "Мой ролик" and opened["source"]["analyzed"] is False
    assert opened["source"]["staged"]["file"] == "clip.mp4"
    assert opened["readiness"]["can_run"] is False                    # nothing to render yet
    # the limits and the estimate are shown before any run, and say that no money is involved
    assert opened["limits"]["limits"]["max_scenes"] == planmod.LIMITS["max_scenes"]
    assert opened["estimate"]["cloud_spend_usd"] == 0.0 and opened["estimate"]["paid_calls"] == 0

    r = await env.client.post(f"{BASE}/projects/{pid}/analyze")
    assert r.status_code == 200, r.text
    view = r.json()
    plan = view["plan"]
    assert len(plan["scenes"]) == 3 and plan["source"]["duration"] == 6.0 and plan["source"]["fps"] == 25.0
    assert (plan["source"]["width"], plan["source"]["height"]) == (640, 360)
    scene = plan["scenes"][0]
    # not measured stays UNKNOWN (text without OCR, speech / music without a separator)
    assert scene["audio"]["speech_present"].startswith("UNKNOWN")
    assert scene["text"]["status"].startswith("UNKNOWN") or scene["text"]["status"] == "measured"
    assert view["unknowns"]["count"] > 0
    assert view["source"]["staged"] is None and view["source"]["file_url"].endswith(f"/projects/{pid}/source")
    assert (await env.client.get(f"{BASE}/projects/{pid}/analysis")).json()["state"] == "done"
    assert (await env.client.get(f"{BASE}/projects/{pid}/source")).status_code == 200
    assert view["readiness"]["can_run"] is True
    listed = (await env.client.get(f"{BASE}/projects")).json()["projects"]
    assert [p["project_id"] for p in listed] == [pid] and listed[0]["scenes"] == 3


async def test_json_create_and_multipart_upload(env, package):
    r = await env.client.post(f"{BASE}/projects", json={"title": "empty", "category": "clipping"})
    assert r.status_code == 201 and r.json()["project"]["category"] == "clipping"
    assert (await env.client.post(f"{BASE}/projects", json={"category": "bogus"})).status_code == 422
    if importlib.util.find_spec("python_multipart") is None:
        pytest.skip("python-multipart is not installed: the multipart path is not available in this environment")
    with open(package / "source.mp4", "rb") as fh:
        r = await env.client.post(f"{BASE}/projects", files={"file": ("my clip.mp4", fh, "video/mp4")},
                                  data={"title": "multipart", "category": "motion"})
    assert r.status_code == 201, r.text
    assert r.json()["source"]["staged"]["file"] == "my clip.mp4"
    with open(package / "music.wav", "rb") as fh:
        r2 = await env.client.post(f"{BASE}/projects", files={"file": ("music.wav", fh, "audio/wav")})
    assert r2.status_code == 422 and r2.json()["error"]["code"] == "BAD_EXTENSION"


async def test_oversize_upload_is_refused_and_leaves_nothing_behind(env, package, monkeypatch):
    monkeypatch.setitem(planmod.LIMITS, "max_source_bytes", 200_000)
    raw = (package / "source.mp4").read_bytes()
    assert len(raw) > 200_000
    r = await env.client.post(f"{BASE}/projects", params={"name": "big.mp4"}, content=raw,
                              headers={"content-type": "video/mp4"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "TOO_LARGE"

    async def chunks():                      # no Content-Length: the limit must hold WHILE receiving
        for i in range(0, len(raw), 64 * 1024):
            yield raw[i:i + 64 * 1024]

    r = await env.client.post(f"{BASE}/projects", params={"name": "big.mp4"}, content=chunks(),
                              headers={"content-type": "video/mp4"})
    assert r.status_code == 413, r.text
    assert (await env.client.get(f"{BASE}/projects")).json()["projects"] == []
    root = Path(env.settings.data_dir) / "motion-workflow"
    assert list((root / ".incoming").iterdir()) == []
    # an upload to an existing project is refused the same way and keeps the project intact
    pid = (await env.client.post(f"{BASE}/projects", json={"title": "x"})).json()["project"]["project_id"]
    r = await env.client.post(f"{BASE}/projects/{pid}/source", params={"name": "big.mp4"}, content=raw)
    assert r.status_code == 413
    assert (await env.client.get(f"{BASE}/projects/{pid}")).json()["source"]["staged"] is None


async def test_bad_names_extensions_and_empty_files_are_refused(env, package):
    pid = (await env.client.post(f"{BASE}/projects", json={"title": "x"})).json()["project"]["project_id"]
    ok = (package / "source.mp4").read_bytes()
    r = await env.client.post(f"{BASE}/projects/{pid}/source", params={"name": "evil.exe"}, content=ok)
    assert r.status_code == 422 and r.json()["error"]["code"] == "BAD_EXTENSION"
    r = await env.client.post(f"{BASE}/projects/{pid}/source", content=ok)
    assert r.status_code == 422 and r.json()["error"]["code"] == "NO_NAME"
    r = await env.client.post(f"{BASE}/projects/{pid}/source", params={"name": "clip.mp4"}, content=b"")
    assert r.status_code == 422 and r.json()["error"]["code"] == "EMPTY_FILE"
    # a name with directories is reduced to its last component, never used as a path
    r = await env.client.post(f"{BASE}/projects/{pid}/source", params={"name": "..\\..\\evil\\clip.mp4"}, content=ok)
    assert r.status_code == 201
    staged = r.json()["source"]["staged"]
    assert staged["file"] == "clip.mp4"
    root = Path(env.settings.data_dir) / "motion-workflow" / pid / "incoming"
    assert sorted(p.name for p in root.iterdir()) == ["clip.mp4", "source.json"]


async def test_a_file_that_is_not_a_video_fails_the_analysis_honestly(env):
    pid = (await env.client.post(f"{BASE}/projects", json={"title": "x"})).json()["project"]["project_id"]
    r = await env.client.post(f"{BASE}/projects/{pid}/source", params={"name": "fake.mp4"}, content=b"not a video" * 500)
    assert r.status_code == 201
    r = await env.client.post(f"{BASE}/projects/{pid}/analyze")
    assert r.status_code == 422 and r.json()["error"]["code"] == "ANALYSIS_FAILED"
    state = (await env.client.get(f"{BASE}/projects/{pid}/analysis")).json()
    assert state["state"] == "failed" and state["error"]


async def test_path_escape_is_refused_everywhere(env, package):
    pid, _ = await analyzed(env.client, package)
    get, post = env.client.get, env.client.post
    for bad in ("a.b", ".hidden", "mw_x..y", "..", "a%2Fb"):
        r = await get(f"{BASE}/projects/{bad}")
        assert r.status_code in (400, 404), (bad, r.status_code)
    r = await get(f"{BASE}/projects/{pid}/jobs/..%5C..%5Cproject.json")
    assert r.status_code in (400, 404)
    r = await get(f"{BASE}/projects/{pid}/jobs/job_zzzzzzzzzzzz")
    assert r.status_code == 400 and r.json()["error"]["code"] == "PATH_ESCAPE"
    job = (await post(f"{BASE}/projects/{pid}/jobs", json={"mode": "preview"})).json()
    await wait_job(env.client, pid, job["job_id"])
    for name in ("../../project.json", "project.json", "preview/../../project.json", "preview/s001_9.png",
                 "..\\x.png", "work/font.ttf", "meta.json", "progress.json", "ffmpeg.pid"):
        r = await get(f"{BASE}/projects/{pid}/jobs/{job['job_id']}/file", params={"name": name})
        assert r.status_code == 404, name
    r = await post(f"{BASE}/projects/{pid}/references", params={"name": "../../evil.png", "kind": "character",
                                                                  "ref": "x"}, content=b"\x89PNG" + b"0" * 100)
    assert r.status_code in (422, 400)
    r = await post(f"{BASE}/projects/{pid}/references", params={"name": "a.png", "kind": "character", "ref": "../bad"},
                   content=b"\x89PNG\r\n\x1a\n" + b"0" * 100)
    assert r.status_code == 422
    r = await env.client.delete(f"{BASE}/projects/{pid}/references/..%2F..%2Fx")
    assert r.status_code in (404, 422)
    r = await get(f"{BASE}/projects/{pid}/versions/0")
    assert r.status_code in (400, 404, 422)


# ------------------------------------------------------------------------------------------ edit

async def test_edit_history_conflict_validation_and_revert(env, package):
    pid, view = await analyzed(env.client, package)
    version = view["version"]
    sid = view["plan"]["scenes"][0]["id"]
    first = await ops(env.client, pid, {"op": "set_title", "title": "Новый заголовок"}, version=version)
    assert first["version"] == version + 1 and first["changed"] and "title" in first["summary"]
    # optimistic concurrency: the same stale expected_version is refused and nothing is written
    stale = await env.client.post(f"{BASE}/projects/{pid}/ops",
                                  json={"ops": [{"op": "set_title", "title": "other"}], "expected_version": version})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"
    assert stale.json()["error"]["current_version"] == version + 1
    assert (await env.client.get(f"{BASE}/projects/{pid}/plan")).json()["plan"]["title"] == "Новый заголовок"
    # invalid operations are rejected whole and name the problem
    bad = await env.client.post(f"{BASE}/projects/{pid}/ops", json={"ops": [
        {"op": "set_title", "title": "ok"}, {"op": "set_policy", "scene": "s999", "element": "text",
                                              "policy": "keep", "reason": "x"}]})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "PLAN_INVALID"
    assert (await env.client.get(f"{BASE}/projects/{pid}/plan")).json()["plan"]["title"] == "Новый заголовок"
    for body in ({"op": "set_cover", "time": -1}, {"op": "set_cover", "time": 999.0},
                 {"op": "set_cover", "time": 1, "position": "left"}, {"op": "set_soundtrack", "mode": "loud"},
                 {"op": "set_soundtrack", "mode": "replace", "reference": "nothing"},
                 {"op": "set_track", "lane": "subtitles", "settings": {"cues": [{"start": 3, "end": 1, "text": "x"}]}},
                 {"op": "nope"}):
        r = await env.client.post(f"{BASE}/projects/{pid}/ops", json={"ops": [body]})
        assert r.status_code == 422, (body, r.text)
    # a good edit of every kind in one atomic version
    edit = await ops(env.client, pid,
                     {"op": "set_cover", "time": 3.0, "title": "Обложка", "position": "top"},
                     {"op": "set_track", "lane": "subtitles", "settings": {"enabled": True, "cues": [
                         {"start": 1.0, "end": 2.0, "text": "b"}, {"start": 0.0, "end": 0.9, "text": "a"}]}},
                     {"op": "set_policy", "scene": sid, "element": "text", "policy": "replace", "reason": "owner"},
                     {"op": "set_prompt", "scene": sid, "prompt": "Привет"})
    plan = edit["plan"]
    assert plan["cover"] == {"time": 3.0, "title": "Обложка", "position": "top"}
    assert [c["text"] for c in plan["tracks"]["subtitles"]["cues"]] == ["a", "b"]      # sorted by start
    assert edit["execution_preview"]["overlays"] == 3 and edit["execution_preview"]["counts"]["applied"] >= 3
    history = (await env.client.get(f"{BASE}/projects/{pid}/history")).json()
    assert history["problems"] == [] and history["history"][0]["version"] == edit["version"]
    version_one = (await env.client.get(f"{BASE}/projects/{pid}/versions/{version}")).json()
    assert version_one["plan"]["title"] == "api test"
    reverted = (await env.client.post(f"{BASE}/projects/{pid}/revert", json={"to_version": version})).json()
    assert reverted["version"] == edit["version"] + 1 and reverted["plan"]["title"] == "api test"
    assert "cover" not in reverted["plan"]


async def test_references_are_bound_listed_limited_and_removed(env, package):
    from PIL import Image
    import io
    pid, _ = await analyzed(env.client, package)
    song = (package / "music.wav").read_bytes()
    r = await env.client.post(f"{BASE}/projects/{pid}/references", params={"name": "music.wav", "kind": "audio",
                                                                             "ref": "song"}, content=song,
                              headers={"content-type": "audio/wav"})
    assert r.status_code == 201, r.text
    ref = next(x for x in r.json()["references"] if x["name"] == "song")
    assert ref["kind"] == "audio" and ref["bytes"] == len(song) and len(ref["sha256"]) == 64
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 200, 30)).save(buf, "PNG")
    r = await env.client.post(f"{BASE}/projects/{pid}/references", params={"name": "a.png", "kind": "character",
                                                                             "ref": "actor"}, content=buf.getvalue())
    assert r.status_code == 201
    # an extension that does not fit the kind, a duplicate name, a text reference
    r = await env.client.post(f"{BASE}/projects/{pid}/references", params={"name": "a.png", "kind": "audio", "ref": "x"},
                              content=buf.getvalue())
    assert r.status_code == 422
    r = await env.client.post(f"{BASE}/projects/{pid}/references", params={"name": "a.png", "kind": "character",
                                                                             "ref": "actor"}, content=buf.getvalue())
    assert r.status_code == 422
    r = await env.client.post(f"{BASE}/projects/{pid}/references", json={"name": "title", "text": "ПРЕМЬЕРА"})
    assert r.status_code == 201
    listed = (await env.client.get(f"{BASE}/projects/{pid}/references")).json()
    assert {x["name"] for x in listed["references"]} == {"song", "actor", "title"}
    # a binding that exists is protected from removal
    sid = (await env.client.get(f"{BASE}/projects/{pid}/plan")).json()["plan"]["scenes"][0]["id"]
    await ops(env.client, pid, {"op": "set_policy", "scene": sid, "element": "text", "policy": "replace",
                                "reason": "owner", "with": "@title"})
    r = await env.client.delete(f"{BASE}/projects/{pid}/references/title")
    assert r.status_code == 422
    await ops(env.client, pid, {"op": "set_policy", "scene": sid, "element": "text", "policy": "keep", "reason": "x"})
    r = await env.client.delete(f"{BASE}/projects/{pid}/references/title")
    assert r.status_code == 200
    assert (await env.client.get(f"{BASE}/projects/{pid}/references/song/file")).content[:4] == b"RIFF"


# ------------------------------------------------------------------------------------------ run

async def test_preview_then_full_render_is_done_only_after_the_file_check(env, package):
    pid, view = await analyzed(env.client, package)
    sid = view["plan"]["scenes"][0]["id"]
    await ops(env.client, pid,
              {"op": "set_policy", "scene": sid, "element": "text", "policy": "replace", "reason": "owner"},
              {"op": "set_prompt", "scene": sid, "prompt": "ПРЕМЬЕРА 100%"},
              {"op": "set_cover", "time": 3.0, "title": "Обложка"})
    started = await env.client.post(f"{BASE}/projects/{pid}/jobs", json={"mode": "preview"})
    assert started.status_code == 202, started.text
    jid = started.json()["job_id"]
    job = await wait_job(env.client, pid, jid)
    assert job["state"] == "preview_done" and job["progress"] > 0.8
    assert job["completed"]["preview"] == job["scene_ids"] and job["completed"]["render"] == []
    assert "output.mp4" not in job["outputs"] and "cover.png" in job["outputs"]
    assert sum(1 for n in job["outputs"] if n.startswith("preview/")) == 9
    frame = await env.client.get(f"{BASE}/projects/{pid}/jobs/{jid}/file", params={"name": "preview/s001_2.png"})
    assert frame.status_code == 200 and frame.content[:4] == b"\x89PNG"
    assert (await env.client.get(f"{BASE}/projects/{pid}/jobs/{jid}/check")).json()["verified"] is False   # no video yet

    resumed = await env.client.post(f"{BASE}/projects/{pid}/jobs/{jid}/render")
    assert resumed.status_code == 202, resumed.text
    job = await wait_job(env.client, pid, jid)
    assert job["state"] == "done", job
    assert job["completed"]["render"] == job["scene_ids"]
    result = job["result"]
    assert result["verified"] is True and result["status"] == "done" and result["mode"] == "full"
    assert result["execution"]["complete"] is True and result["not_executed"] == []
    out = result["output"]
    assert out["video"]["width"] == 640 and out["video"]["height"] == 360 and out["video"]["fps"] == 25.0
    assert out["audio"][0]["codec"] == "aac" and abs(out["format"]["duration"] - 6.0) < 0.1
    assert result["parameters"]["video"]["codec_action"] == "libx264" and result["paid_calls"] == 0
    assert result["ffmpeg"]["version"] and result["timing"]["render_seconds"] > 0
    assert all(e["text_present_heuristic"] for e in result["text_evidence"])
    video = await env.client.get(f"{BASE}/projects/{pid}/jobs/{jid}/file", params={"name": "output.mp4"})
    assert video.status_code == 200 and len(video.content) == out["bytes"]
    report = (await env.client.get(f"{BASE}/projects/{pid}/jobs/{jid}/file", params={"name": "render_report.json"})).json()
    assert report["output"]["sha256"] == out["sha256"] and report["commands"][0]["argv"]
    check = (await env.client.get(f"{BASE}/projects/{pid}/jobs/{jid}/check")).json()
    assert check["verified"] is True and check["matches_report"] and check["sha256"] == out["sha256"]
    cover = await env.client.get(f"{BASE}/projects/{pid}/jobs/{jid}/file", params={"name": "cover.png"})
    assert cover.status_code == 200 and cover.content[:4] == b"\x89PNG"
    # the project view carries the job and the file link used by the page
    reopened = (await env.client.get(f"{BASE}/projects/{pid}")).json()
    assert reopened["jobs"][0]["job_id"] == jid and reopened["jobs"][0]["state"] == "done"
    assert reopened["active_job"] is None


async def test_unsupported_elements_are_reported_not_executed_and_never_faked(env, package):
    from PIL import Image
    import io
    pid, view = await analyzed(env.client, package)
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (200, 100, 50)).save(buf, "PNG")
    r = await env.client.post(f"{BASE}/projects/{pid}/references", params={"name": "a.png", "kind": "character",
                                                                             "ref": "actor"}, content=buf.getvalue())
    assert r.status_code == 201
    plan = (await ops(env.client, pid,
                      {"op": "set_policy", "scene": "s002", "element": "face_character", "policy": "replace",
                       "reason": "owner", "with": "@actor"},
                      {"op": "set_policy", "scene": "s003", "element": "background", "policy": "replace",
                       "reason": "owner"}, {"op": "set_prompt", "scene": "s003", "prompt": "beach"}))
    preview = plan["execution_preview"]
    assert preview["counts"]["not_executed"] == 2 and preview["counts"]["applied"] == 0
    assert {(o["scene_id"], o["element"]) for o in preview["not_executed"]} == {("s002", "face_character"),
                                                                                  ("s003", "background")}
    job = await run_full(env.client, pid)
    assert job["state"] == "done" and job["result"]["verified"] is True
    result = job["result"]
    assert result["execution"]["complete"] is False and result["execution"]["not_executed"] == 2
    got = {(n["scene_id"], n["element"]): n for n in result["not_executed"]}
    assert set(got) == {("s002", "face_character"), ("s003", "background")}
    assert all(n["reason_code"] == "needs_generative_tool" and "left exactly as in the source" in n["reason"]
               for n in got.values())
    report = (await env.client.get(f"{BASE}/projects/{pid}/jobs/{job['job_id']}/file",
                                   params={"name": "render_report.json"})).json()
    s2 = next(s for s in report["scenes"] if s["scene_id"] == "s002")
    face = next(i for i in s2["items"] if i["element"] == "face_character")
    assert face["status"] == "not_executed" and face["with"] == "@actor"
    # the picture really is the source: the rendered scene differs from the source only by encoder noise
    assert report["parameters"]["text_overlays"] == []


async def test_a_plan_that_is_not_ready_cannot_start_a_job(env, package):
    pid, view = await analyzed(env.client, package)
    await ops(env.client, pid, {"op": "set_policy", "scene": "s001", "element": "clothes", "policy": "unknown",
                                "reason": "undecided yet"})
    r = await env.client.post(f"{BASE}/projects/{pid}/jobs", json={"mode": "full"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "NOT_READY"
    assert any("undecided" in p for p in r.json()["error"]["problems"])
    assert (await env.client.get(f"{BASE}/projects/{pid}/jobs")).json()["jobs"] == []
    unready = (await env.client.get(f"{BASE}/projects/{pid}/readiness")).json()
    assert unready["can_run"] is False and unready["ready"] is False


async def test_one_job_at_a_time_cancel_stops_the_render_and_leaves_no_file(env, big_package):
    pid, _ = await analyzed(env.client, big_package, title="slow")
    await ops(env.client, pid, {"op": "set_render", "settings": {"preset": "veryslow", "crf": 0}})
    r = await env.client.post(f"{BASE}/projects/{pid}/jobs", json={"mode": "full"})
    assert r.status_code == 202
    jid = r.json()["job_id"]
    other = await env.client.post(f"{BASE}/projects/{pid}/jobs", json={"mode": "preview"})
    assert other.status_code == 409 and other.json()["error"]["code"] == "JOB_RUNNING"
    end = time.monotonic() + 120
    while time.monotonic() < end:
        job = (await env.client.get(f"{BASE}/projects/{pid}/jobs/{jid}")).json()
        if job["stage"] == "render" and job["progress"] > 0.09:
            break
        assert job["state"] in ("running", "planned"), job
        await asyncio.sleep(0.1)
    cancelled = (await env.client.post(f"{BASE}/projects/{pid}/jobs/{jid}/cancel")).json()
    assert cancelled["state"] == "cancelled", cancelled
    assert cancelled["interrupted"]["reason"] == "cancelled by owner"
    assert "output.mp4" not in cancelled["outputs"] and cancelled["progress_is_live"] is False
    folder = Path(env.settings.data_dir) / "motion-workflow" / pid / "outputs" / jid
    assert not (folder / "output.mp4").exists() and not (folder / "output.part.mp4").exists()
    assert not (folder / "ffmpeg.pid").exists()
    # the slot is free again
    again = await env.client.post(f"{BASE}/projects/{pid}/jobs", json={"mode": "preview"})
    assert again.status_code == 202
    await wait_job(env.client, pid, again.json()["job_id"])


async def test_a_corrupted_output_is_not_reported_done(env, package, monkeypatch):
    pid, _ = await analyzed(env.client, package)
    real = render_mod.run_render

    def corrupting(prepared, out_dir, **kwargs):
        result = real(prepared, out_dir, **kwargs)
        part = Path(result["part"])
        raw = bytearray(part.read_bytes())
        part.write_bytes(bytes(raw[: len(raw) // 2]))        # half a file: a lost tail
        return result

    monkeypatch.setattr(render_mod, "run_render", corrupting)
    job = await run_full(env.client, pid)
    assert job["state"] == "failed", job
    assert "verification" in job["error"] and "outputs" in job
    assert "output.mp4" not in job["outputs"]
    folder = Path(env.settings.data_dir) / "motion-workflow" / pid / "outputs" / job["job_id"]
    assert not (folder / "output.mp4").exists() and not (folder / "output.part.mp4").exists()
    assert job["result"]["verified"] is False and job["result"]["status"] == "failed"
    failed = [c["name"] for c in job["result"]["verification"]["checks"] if not c["ok"]]
    assert failed, "the report must name the failed checks"
    assert (await env.client.get(f"{BASE}/projects/{pid}/jobs/{job['job_id']}/check")).json()["verified"] is False


async def test_a_missing_ffmpeg_is_an_honest_error_not_a_fake_success(env, package, monkeypatch):
    pid, _ = await analyzed(env.client, package)
    monkeypatch.setattr(analyze_mod, "find_ffmpeg", lambda: None)
    monkeypatch.setattr(analyze_mod, "find_ffprobe", lambda ffmpeg=None: None)
    status = (await env.client.get(f"{BASE}/status")).json()
    assert status["renderer"]["ready"] is False and status["renderer"]["problems"]
    r = await env.client.post(f"{BASE}/projects/{pid}/jobs", json={"mode": "full"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "RENDERER_UNAVAILABLE"
    assert (await env.client.get(f"{BASE}/projects/{pid}/jobs")).json()["jobs"] == []
    view = (await env.client.get(f"{BASE}/projects/{pid}")).json()
    assert view["readiness"]["can_run"] is False and view["renderer"]["ready"] is False
    r = await env.client.post(f"{BASE}/projects/{pid}/analyze")
    assert r.status_code == 503 and r.json()["error"]["code"] == "ANALYZER_UNAVAILABLE"


# ------------------------------------------------------------------------------------------ restart / recovery

async def second_instance(env):
    """A new app object over the SAME data directory: what a backend restart looks like."""
    app, svc = await start_app(env.settings, start_workers=False)
    return app, svc, client_for(app, svc)


async def test_after_a_restart_the_project_and_the_finished_job_reopen_and_verify(env, package):
    pid, view = await analyzed(env.client, package, title="survives restart")
    await ops(env.client, pid, {"op": "set_cover", "time": 2.0, "title": "T"})
    job = await run_full(env.client, pid)
    assert job["state"] == "done"
    app2, svc2, client2 = await second_instance(env)
    try:
        assert svc2.motion_workflow_runtime is not env.svc.motion_workflow_runtime
        listed = (await client2.get(f"{BASE}/projects")).json()["projects"]
        assert [p["project_id"] for p in listed] == [pid] and listed[0]["has_output"] is True
        reopened = (await client2.get(f"{BASE}/projects/{pid}")).json()
        assert reopened["plan"]["cover"]["title"] == "T" and reopened["version"] == (await env.client.get(
            f"{BASE}/projects/{pid}")).json()["version"]
        job2 = (await client2.get(f"{BASE}/projects/{pid}/jobs/{job['job_id']}")).json()
        assert job2["state"] == "done" and job2["result"]["output"]["sha256"] == job["result"]["output"]["sha256"]
        check = (await client2.get(f"{BASE}/projects/{pid}/jobs/{job['job_id']}/check")).json()
        assert check["verified"] is True and check["matches_report"]
        video = await client2.get(f"{BASE}/projects/{pid}/jobs/{job['job_id']}/file", params={"name": "output.mp4"})
        assert video.status_code == 200 and len(video.content) == job["result"]["output"]["bytes"]
        assert (await client2.get(f"{BASE}/status")).json()["active_job"] is None
    finally:
        await client2.aclose()
        await svc2.stop()


async def _crash_state(env, pid: str, *, through_render: bool) -> str:
    """A job left RUNNING on disk exactly as a killed backend leaves it (no thread, no process)."""
    rt = env.svc.motion_workflow_runtime
    s = rt.store
    job = s.create_job(pid)
    jid = job["job_id"]
    s.job_start_preview(pid, jid)
    ids = job["scene_ids"]
    if through_render:
        for sid in ids:
            s.job_complete_scene(pid, jid, sid, output={"frames": []})
        s.job_finish_preview(pid, jid)
        s.job_start_render(pid, jid)
        s.job_complete_scene(pid, jid, ids[0], output={"rendered_to": 2.0})
    else:
        s.job_complete_scene(pid, jid, ids[0], output={"frames": []})
    return jid


async def test_a_job_running_when_the_backend_died_is_interrupted_and_can_resume(env, package):
    pid, _ = await analyzed(env.client, package)
    jid = await _crash_state(env, pid, through_render=True)
    assert env.svc.motion_workflow_runtime.store.get_job(pid, jid)["state"] == "RENDER_RUNNING"
    app2, svc2, client2 = await second_instance(env)
    try:
        status = (await client2.get(f"{BASE}/status")).json()
        assert any(i["job_id"] == jid for i in status["recovery"]["interrupted_at_startup"])
        job = (await client2.get(f"{BASE}/projects/{pid}/jobs/{jid}")).json()
        assert job["state"] == "interrupted" and job["interrupted"]["phase"] == "render"
        assert job["interrupted"]["last_completed_scene"] == "s001" and job["next_scene"] == "s002"
        assert "output.mp4" not in job["outputs"]               # never shown as a result
        # nothing stays "running forever"; the project does not list an active job either
        assert (await client2.get(f"{BASE}/projects/{pid}")).json()["active_job"] is None
        r = await client2.post(f"{BASE}/projects/{pid}/jobs/{jid}/resume")
        assert r.status_code == 202, r.text
        done = await wait_job(client2, pid, jid)
        assert done["state"] == "done" and done["attempt"] == 2
        assert done["completed"]["render"] == done["scene_ids"]
        assert done["result"]["verified"] is True
    finally:
        await client2.aclose()
        await svc2.stop()


async def test_a_preview_interrupted_midway_resumes_from_the_next_scene(env, package):
    pid, _ = await analyzed(env.client, package)
    jid = await _crash_state(env, pid, through_render=False)
    app2, svc2, client2 = await second_instance(env)
    try:
        job = (await client2.get(f"{BASE}/projects/{pid}/jobs/{jid}")).json()
        assert job["state"] == "interrupted" and job["interrupted"]["phase"] == "preview"
        assert job["completed"]["preview"] == ["s001"] and job["next_scene"] == "s002"
        r = await client2.post(f"{BASE}/projects/{pid}/jobs/{jid}/resume")
        assert r.status_code == 202
        done = await wait_job(client2, pid, jid)
        assert done["state"] == "preview_done" and done["completed"]["preview"] == ["s001", "s002", "s003"]
        # s001 was "completed" by the dead run and is not redone; its frames never existed, so its
        # files are produced now (a completed scene without files is regenerated)
        assert sum(1 for n in done["outputs"] if n.startswith("preview/s001")) == 3
    finally:
        await client2.aclose()
        await svc2.stop()


async def test_a_verified_render_whose_process_died_before_the_last_write_is_recovered_from_disk(env, package):
    pid, _ = await analyzed(env.client, package)
    rt = env.svc.motion_workflow_runtime
    s = rt.store
    job = s.create_job(pid)
    jid = job["job_id"]
    s.job_start_preview(pid, jid)
    for sid in job["scene_ids"]:
        s.job_complete_scene(pid, jid, sid, output={"frames": []})
    s.job_finish_preview(pid, jid)
    s.job_start_render(pid, jid)
    for sid in job["scene_ids"]:
        s.job_complete_scene(pid, jid, sid, output={})
    # the renderer finished, verified and wrote its report; only job_finish_render never happened
    folder = rt.out_dir(pid, jid)
    folder.mkdir(parents=True, exist_ok=True)
    plan = s.job_plan(pid, jid)
    prepared = render_mod.prepare(plan, source_path=rt.source_path(pid, plan), ref_paths={}, texts={})
    result = render_mod.run_render(prepared, folder)
    verification = render_mod.verify_output(result["part"], prepared["env"], render_mod.expected_output(prepared))
    assert verification["ok"]
    final = folder / "output.mp4"
    render_mod.finalize_output(result["part"], final)
    report = render_mod.build_report(
        prepared=prepared, job={"project_id": pid, "job_id": jid, "plan_version": plan["version"],
                                "plan_content_sha256": job["plan_content_sha256"], "attempt": 1},
        mode="full", status="done", render=result, verification=verification, preview=None, cover=None,
        output_file=final, evidence=[], timings={}, telegram=None)
    render_mod.write_report(report, folder / "render_report.json")
    assert s.get_job(pid, jid)["state"] == "RENDER_RUNNING"

    app2, svc2, client2 = await second_instance(env)
    try:
        status = (await client2.get(f"{BASE}/status")).json()
        assert {"project_id": pid, "job_id": jid} in status["recovery"]["finished_from_disk"]
        job2 = (await client2.get(f"{BASE}/projects/{pid}/jobs/{jid}")).json()
        assert job2["state"] == "done" and job2["output"]["recovered_from_disk"] is True
        assert job2["result"]["verified"] is True
        assert (await client2.get(f"{BASE}/projects/{pid}/jobs/{jid}/check")).json()["verified"] is True
    finally:
        await client2.aclose()
        await svc2.stop()


async def test_a_tampered_output_is_not_recovered_from_disk(env, package):
    pid, _ = await analyzed(env.client, package)
    job = await run_full(env.client, pid)
    assert job["state"] == "done"
    rt = env.svc.motion_workflow_runtime
    jid = job["job_id"]
    # pretend the process died one step earlier: RENDER_DONE cannot go back, so build the state afresh
    second = rt.store.create_job(pid)
    s = rt.store
    sid2 = second["job_id"]
    s.job_start_preview(pid, sid2)
    for sid in second["scene_ids"]:
        s.job_complete_scene(pid, sid2, sid, output={})
    s.job_finish_preview(pid, sid2)
    s.job_start_render(pid, sid2)
    for sid in second["scene_ids"]:
        s.job_complete_scene(pid, sid2, sid, output={})
    folder = rt.out_dir(pid, sid2)
    folder.mkdir(parents=True, exist_ok=True)
    src_folder = rt.out_dir(pid, jid)
    (folder / "output.mp4").write_bytes((src_folder / "output.mp4").read_bytes()[:-5000])     # truncated copy
    report = json.loads((src_folder / "render_report.json").read_text(encoding="utf-8"))
    report["job"]["job_id"] = sid2
    report["job"]["plan_content_sha256"] = second["plan_content_sha256"]
    (folder / "render_report.json").write_text(json.dumps(report), encoding="utf-8")
    app2, svc2, client2 = await second_instance(env)
    try:
        view = (await client2.get(f"{BASE}/projects/{pid}/jobs/{sid2}")).json()
        assert view["state"] == "interrupted"                  # not "done": the bytes do not match the report
        assert (await client2.get(f"{BASE}/status")).json()["recovery"]["finished_from_disk"] == []
    finally:
        await client2.aclose()
        await svc2.stop()


async def test_an_orphaned_ffmpeg_of_an_interrupted_job_is_killed_at_startup(env, package, tmp_path):
    pid, _ = await analyzed(env.client, package)
    jid = await _crash_state(env, pid, through_render=True)
    folder = env.svc.motion_workflow_runtime.out_dir(pid, jid)
    folder.mkdir(parents=True, exist_ok=True)
    # a real, long-running ffmpeg whose command line names this job's folder (what a dead backend leaves)
    argv = [ENV["ffmpeg"], "-hide_banner", "-loglevel", "error", "-y", "-re", "-f", "lavfi", "-i",
            "testsrc=size=160x90:rate=10", "-t", "120", str(folder / "output.part.mp4")]
    orphan = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    stranger = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        (folder / "ffmpeg.pid").write_text(json.dumps({"pid": orphan.pid}), encoding="utf-8")
        time.sleep(0.5)
        assert orphan.poll() is None
        app2, svc2, client2 = await second_instance(env)
        try:
            killed = svc2.motion_workflow_runtime.recovery["orphans_killed"]
            assert {"project_id": pid, "job_id": jid} in killed
            orphan.wait(timeout=15)
            assert orphan.poll() is not None
            assert not (folder / "ffmpeg.pid").exists() and not (folder / "output.part.mp4").exists()
        finally:
            await client2.aclose()
            await svc2.stop()
        # negative control: a pid file that points at an unrelated program is never killed
        (folder / "ffmpeg.pid").write_text(json.dumps({"pid": stranger.pid}), encoding="utf-8")
        app3, svc3, client3 = await second_instance(env)
        try:
            assert stranger.poll() is None
            assert not (folder / "ffmpeg.pid").exists()
        finally:
            await client3.aclose()
            await svc3.stop()
    finally:
        for proc in (orphan, stranger):
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def test_the_feature_registers_under_the_api_prefix_with_a_setup_hook():
    from bcc.features import Feature
    assert isinstance(mwa.FEATURE, Feature) and mwa.FEATURE.name == "motion_workflow_api"
    assert mwa.FEATURE.router.prefix == "/motion-workflow" and mwa.FEATURE.setup is mwa._setup
