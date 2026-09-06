"""Frame-aligned linked editing through commands, canonical history and HTTP."""
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import shutil

import pytest

from bcc.db import Database
from bcc.video_studio.commands import apply_command, interpolate
from bcc.video_studio.model import (Conflict, EditLocked, StudioError, TICKS,
    clip, clip_duration, frame_ticks, new_project, sequence_duration)
from bcc.video_studio.store import ProjectStore


def linked_project():
    p = new_project("linked-project", "Linked frame editing")
    p["media"]["source"] = {"id": "source", "name": "fixture.mp4", "relative_path": "media/fixture.mp4",
        "sha256": "0" * 64, "duration_ticks": 10 * TICKS, "has_video": True, "has_audio": True}
    video, audio = p["sequences"][0]["tracks"]
    p = apply_command(p, {"type": "clip.add", "track_id": video["id"], "clip": {
        "id": "video", "media_id": "source", "start": 0, "source_in": 0, "source_out": 4 * TICKS}})[0]
    p = apply_command(p, {"type": "clip.detach_audio", "clip_id": "video", "track_id": audio["id"]})[0]
    return p


def split(p, **args):
    return apply_command(p, {"type": "clip.split", "clip_id": "video", **args})[0]


def pair_assertions(p, at):
    video, audio = p["sequences"][0]["tracks"]
    assert len(video["clips"]) == len(audio["clips"]) == 2
    vl, vr = video["clips"]
    al, ar = audio["clips"]
    for left, right in ((vl, vr), (al, ar)):
        assert left["start"] + clip_duration(left) == right["start"] == at
        assert left["source_out"] == right["source_in"]
        assert right["start"] + clip_duration(right) == 4 * TICKS
    for v, a in ((vl, al), (vr, ar)):
        assert v["linked_id"] == a["id"] and a["linked_id"] == v["id"]
        assert v["group_id"] == a["group_id"]
        assert v["audio_disabled"] and a["audio_only"]
    assert vl["group_id"] != vr["group_id"]


@pytest.mark.parametrize("fps,frame", [({"num": 25, "den": 1}, 37), ({"num": 24000, "den": 1001}, 37),
    ({"num": 30000, "den": 1001}, 37), ({"num": 60000, "den": 1001}, 37)])
def test_linked_split_at_rational_frame_preserves_coverage_and_original(fps, frame):
    p = linked_project()
    p["sequences"][0]["fps"] = fps
    before = deepcopy(p)
    result = split(p, frame=frame)
    exact = Fraction(frame * TICKS * fps["den"], fps["num"])
    at = int(exact + Fraction(1, 2))
    pair_assertions(result, at)
    assert p == before
    assert result["media"] == before["media"]
    assert sequence_duration(result["sequences"][0]) == 4 * TICKS


def test_right_pair_moves_together_without_dragging_left_halves():
    p = split(linked_project(), frame=25)
    video, audio = p["sequences"][0]["tracks"]
    right = video["clips"][1]["id"]
    changed = apply_command(p, {"type": "clip.move", "clip_id": right, "start": 2 * TICKS})[0]
    assert [c["start"] for c in changed["sequences"][0]["tracks"][0]["clips"]] == [0, 2 * TICKS]
    assert [c["start"] for c in changed["sequences"][0]["tracks"][1]["clips"]] == [0, 2 * TICKS]


def test_reverse_linked_split_preserves_source_order():
    p = linked_project()
    for tr in p["sequences"][0]["tracks"]:
        tr["clips"][0]["reverse"] = True
    q = split(p, frame=25)
    for tr in q["sequences"][0]["tracks"]:
        left, right = tr["clips"]
        assert (left["source_in"], left["source_out"]) == (3 * TICKS, 4 * TICKS)
        assert (right["source_in"], right["source_out"]) == (0, 3 * TICKS)
        assert left["start"] + clip_duration(left) == right["start"] == TICKS


@pytest.mark.parametrize("args", [{"frame": True}, {"frame": -1}, {"frame": 1.5}, {"frame": 10**30},
    {"frame": 0}, {"frame": 100}, {"frame": 25, "at": TICKS}, {}, {"frame": 25, "with_links": "false"},
    {"frame": 25, "with_links": False}])
def test_bad_split_has_no_partial_edits(args):
    p = linked_project()
    before = deepcopy(p)
    with pytest.raises(StudioError):
        split(p, **args)
    assert p == before


@pytest.mark.parametrize("defect", ["locked", "one-way", "self-link", "missing-link", "group", "outside-partner"])
def test_linked_partner_constraints_block_atomic_edit(defect):
    p = linked_project()
    video, audio = p["sequences"][0]["tracks"]
    if defect == "locked":
        audio["locked"] = True
    elif defect == "one-way":
        audio["clips"][0]["linked_id"] = None
    elif defect == "self-link":
        video["clips"][0]["linked_id"] = "video"
    elif defect == "missing-link":
        video["clips"][0]["linked_id"] = "absent"
    elif defect == "group":
        audio["clips"][0]["group_id"] = "different"
    else:
        audio["clips"][0]["start"] = 2 * TICKS
    before = deepcopy(p)
    with pytest.raises(StudioError):
        split(p, frame=25)
    assert p == before


def test_held_keyframe_after_cut_is_not_changed_to_a_ramp():
    p = linked_project()
    c = clip(p, "video")[2]
    keys = [{"t": 0, "value": 0, "easing": "hold"}, {"t": 3 * TICKS, "value": 1, "easing": "linear"}]
    c["keyframes"] = {"opacity": keys}
    q = split(p, frame=25)
    left, right = q["sequences"][0]["tracks"][0]["clips"]
    for t in (0, 500000, 1999999, 2000000, 2500000):
        assert interpolate(right["keyframes"]["opacity"], t) == interpolate(keys, t + TICKS)


def test_unsupported_eased_curve_does_not_silently_change_animation():
    p = linked_project()
    clip(p, "video")[2]["keyframes"] = {"x": [
        {"t": 0, "value": 0, "easing": "ease_in"}, {"t": 4 * TICKS, "value": 100}]}
    with pytest.raises(StudioError, match="eased"):
        split(p, frame=25)


@pytest.mark.parametrize("speed", [{"num": 3, "den": 2}, {"num": 1001, "den": 1000}, {"num": 2, "den": 1}])
def test_constant_speed_split_preserves_exact_timeline_ticks(speed):
    p = linked_project()
    for tr in p["sequences"][0]["tracks"]:
        tr["clips"][0]["speed"] = speed
    q = split(p, frame=25)
    for old, new in zip(p["sequences"][0]["tracks"], q["sequences"][0]["tracks"]):
        assert sum(clip_duration(c) for c in new["clips"]) == clip_duration(old["clips"][0])
        assert new["clips"][0]["start"] + clip_duration(new["clips"][0]) == new["clips"][1]["start"]


@pytest.fixture
async def linked_store(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'linked.sqlite'}")
    await db.create_all()
    store = ProjectStore(db)
    await store.create("linked-project", "Linked", "create")
    await store.apply("linked-project", 0, "seed", {"type": "project.import", "project": linked_project()})
    yield store
    await db.close()


async def test_split_undo_redo_dryrun_and_replay_use_one_atomic_revision(linked_store):
    store = linked_store
    before = await store.get("linked-project")
    cmd = {"type": "clip.split", "clip_id": "video", "frame": 25}
    dry = await store.apply("linked-project", 1, "split", cmd, dry_run=True)
    pair_assertions(dry["project"], TICKS)
    assert await store.get("linked-project") == before
    changed = await store.apply("linked-project", 1, "split", cmd)
    assert changed["revision"] == 2 and len(changed["changed_ids"]) == 4
    assert await store.apply("linked-project", 1, "split", cmd) == changed
    with pytest.raises(Conflict):
        await store.apply("linked-project", 1, "stale", cmd)
    undone = (await store.apply("linked-project", 2, "undo", {"type": "history.undo"}))["project"]
    undone["revision"] = before["revision"]
    assert undone == before
    redone = (await store.apply("linked-project", 3, "redo", {"type": "history.redo"}))["project"]
    redone["revision"] = changed["project"]["revision"]
    assert redone == changed["project"]


async def test_partner_lease_blocks_agent_and_does_not_consume_revision(linked_store):
    store = linked_store
    p = await store.get("linked-project")
    partner = clip(p, "video")[2]["linked_id"]
    await store.lease("linked-project", [partner], actor="human")
    with pytest.raises(EditLocked):
        await store.apply("linked-project", 1, "agent", {"type": "clip.split", "clip_id": "video", "frame": 25}, actor="agent:1")
    assert await store.get("linked-project") == p


async def test_authenticated_http_split_and_existing_native_tool_share_behavior(env):
    p = linked_project()
    store = env.svc.video_studio.store
    await store.create(p["id"], p["name"], "linked-create")
    await store.apply(p["id"], 0, "linked-seed", {"type": "project.import", "project": p})
    body = {"project_id": p["id"], "expected_revision": 1, "operation_id": "http-split",
            "command": {"type": "clip.split", "clip_id": "video", "frame": 25}}
    response = await env.client.post("/api/video-studio/commands", json=body)
    assert response.status_code == 200, response.text
    pair_assertions(response.json()["project"], TICKS)
    from bcc.tools import REGISTRY, ToolContext
    ctx = ToolContext(svc=env.svc, task={"id": 998, "meta": {"video_project_id": p["id"]}}, run_id=0, agent={"tools": [], "permissions": {}})
    result = await REGISTRY.get("video.history.undo").handler({**body, "expected_revision": 2,
        "operation_id": "tool-undo", "command": {}}, ctx)
    assert not result.error
    result = await REGISTRY.get("video.clip.split").handler({**body, "expected_revision": 3,
        "operation_id": "tool-split"}, ctx)
    assert not result.error
    pair_assertions((await store.get(p["id"])), TICKS)


def test_unrepresentable_slow_source_boundary_rejected_without_tick_gap():
    p = linked_project()
    p["sequences"][0]["fps"] = {"num": 24000, "den": 1001}
    for tr in p["sequences"][0]["tracks"]:
        tr["clips"][0]["speed"] = {"num": 1, "den": 100}
    before = deepcopy(p)
    with pytest.raises(StudioError, match="exact tick coverage"):
        split(p, frame=1)
    assert p == before


async def _render_split_fixture(tmp_path):
    from bcc.video_studio.media import MediaLibrary, binary, process
    from bcc.video_studio.render import render_project
    path = tmp_path / "source.mp4"
    await process([binary("ffmpeg"), "-v", "error", "-nostdin", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)])
    original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    media = await MediaLibrary(tmp_path).import_file(path)
    p = new_project("render-project", "Frame split render")
    p["media"] = {media["id"]: media}
    seq = p["sequences"][0]
    seq.update(width=160, height=90)
    video, audio = seq["tracks"]
    p = apply_command(p, {"type": "clip.add", "track_id": video["id"], "clip": {
        "id": "video", "media_id": media["id"], "source_out": TICKS}})[0]
    p = apply_command(p, {"type": "clip.detach_audio", "clip_id": "video", "track_id": audio["id"]})[0]
    baseline = tmp_path / "baseline-output.mp4"
    await render_project(p, tmp_path, baseline)
    baseline_raw = await process([binary("ffprobe"), "-v", "error", "-select_streams", "v:0",
        "-count_frames", "-show_entries", "stream=nb_read_frames", "-of", "json", str(baseline)])
    baseline_frames = int(json.loads(baseline_raw[0])["streams"][0]["nb_read_frames"])
    q = split(p, frame=13)
    output = tmp_path / "split-output.mp4"
    result = await render_project(q, tmp_path, output)
    assert result["verification"]["passed"] and result["verification"]["decoded"]
    assert result["verification"]["has_audio"]
    raw = await process([binary("ffprobe"), "-v", "error", "-select_streams", "v:0",
        "-show_frames", "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(output)])
    frames = json.loads(raw[0])["frames"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original_hash
    return baseline_frames, frames


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="local FFmpeg/ffprobe required")
async def test_real_linked_split_render_matches_baseline_without_source_mutation(tmp_path):
    baseline_frames, frames = await _render_split_fixture(tmp_path)
    # Relative edit regression only; the exact 25-frame oracle below remains
    # open because the unchanged baseline renderer itself emits 24 frames.
    assert len(frames) == baseline_frames
    assert [float(frame["best_effort_timestamp_time"]) for frame in frames] == pytest.approx([i / 25 for i in range(len(frames))])


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="local FFmpeg/ffprobe required")
@pytest.mark.xfail(strict=True, reason="BASE ac2cde8 renderer emits 24/25 frames for both unsplit and split one-second CFR fixture; export qualification remains blocked")
async def test_open_baseline_exact_export_frame_count(tmp_path):
    baseline_frames, frames = await _render_split_fixture(tmp_path)
    assert len(frames) == baseline_frames == 25, f"baseline={baseline_frames}, split={len(frames)}, expected=25"


def test_split_at_hold_jump_preserves_new_value_and_following_curve():
    p = linked_project()
    keys = [{"t": 0, "value": 0, "easing": "hold"},
            {"t": TICKS, "value": 1, "easing": "linear"},
            {"t": 3 * TICKS, "value": 2, "easing": "linear"}]
    clip(p, "video")[2]["keyframes"] = {"x": keys}
    q = split(p, frame=25)
    left, right = q["sequences"][0]["tracks"][0]["clips"]
    assert right["keyframes"]["x"][0]["value"] == 1
    assert interpolate(left["keyframes"]["x"], TICKS - 1) == 0
    for offset in (100000, 500000, 1000000, 2000000):
        assert interpolate(right["keyframes"]["x"], offset) == interpolate(keys, TICKS + offset)
