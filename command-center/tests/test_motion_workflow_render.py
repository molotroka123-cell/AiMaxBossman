"""Motion workflow renderer (bcc/motion_workflow/render.py): real ffmpeg, real files, negative controls.

Pure decision tests (what is executed, what is ``not_executed`` and why) need no ffmpeg.  Everything that
renders uses the machine's ffmpeg / ffprobe and skips with an explicit reason when they are missing.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from bcc.motion_workflow import analyze as analyze_mod
from bcc.motion_workflow import plan as planmod
from bcc.motion_workflow import render
from bcc.motion_workflow import store as store_mod

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "motion_workflow"))
import synthetic_package  # noqa: E402

ENV = render.environment()
NEEDS_FFMPEG = pytest.mark.skipif(
    not ENV["ready"], reason="ffmpeg / ffprobe with libx264 + aac not available here: " + "; ".join(ENV["problems"]))
NEEDS_TEXT = pytest.mark.skipif(
    not ENV["text_ready"], reason="drawtext or a font file is not available here: " + "; ".join(ENV["warnings"]))


# ------------------------------------------------------------------------------------------ pure decisions

def _plan(scenes: int = 3, *, has_audio: bool = True) -> dict:
    plan = planmod.new_plan(title="pure", project_id="mw_pure0000000a")
    plan["source"] = {"asset": "a" * 64, "ext": ".mp4", "file_name": "x.mp4", "bytes": 1000, "duration": 6.0,
                      "width": 640, "height": 360, "display_width": 640, "display_height": 360,
                      "aspect_ratio": "16:9", "fps": 25.0, "frames": 150, "rotation": 0, "has_audio": has_audio,
                      "audio_codec": "aac", "video_codec": "h264", "pix_fmt": "yuv420p"}
    for index in range(scenes):
        plan["scenes"].append(planmod.make_scene(index, index * 2.0, index * 2.0 + 2.0))
    return plan


def test_generative_replacements_are_reported_not_executed_with_a_reason_per_scene():
    plan = _plan()
    plan["prompt_note"] = "x"
    for scene in plan["scenes"]:
        for name in ("face_character", "clothes", "background", "product"):
            scene["elements"][name].update(policy="replace", reason="owner")
        scene["prompt"] = "make it so"
    resolved = render.resolve(plan)
    assert resolved["counts"]["requested"] == 12 and resolved["counts"]["applied"] == 0
    assert resolved["counts"]["not_executed"] == 12 and resolved["counts"]["complete"] is False
    for scene in resolved["scenes"]:
        items = {i["element"]: i for i in scene["items"]}
        for name in ("face_character", "clothes", "background", "product"):
            assert items[name]["status"] == "not_executed"
            assert items[name]["reason_code"] == "needs_generative_tool"
            assert "left exactly as in the source" in items[name]["reason"]
        assert items["text"]["status"] == "kept" and items["music"]["status"] == "kept"
    assert "PARTIAL" in render.summary_line(resolved["counts"])


def test_text_replace_uses_the_bound_text_then_the_prompt_and_says_when_there_is_none():
    plan = _plan()
    first, second, third = plan["scenes"]
    first["elements"]["text"].update(policy="replace", reason="owner", **{"with": "@title"})
    second["elements"]["text"].update(policy="replace", reason="owner")
    second["prompt"] = "  Привет @title  "
    third["elements"]["text"].update(policy="replace", reason="owner")        # no text at all
    resolved = render.resolve(plan, {"title": "ПРЕМЬЕРА"})
    by_scene = {s["scene_id"]: {i["element"]: i for i in s["items"]} for s in resolved["scenes"]}
    assert by_scene["s001"]["text"]["status"] == "applied"
    assert by_scene["s002"]["text"]["status"] == "applied"
    assert by_scene["s003"]["text"]["status"] == "not_executed" and by_scene["s003"]["text"]["reason_code"] == "no_text"
    texts = {o["scene_id"]: o["text"] for o in resolved["overlays"]}
    assert texts == {"s001": "ПРЕМЬЕРА", "s002": "Привет ПРЕМЬЕРА"}
    # a reference that cannot be read is reported, not silently dropped
    missing = render.resolve(plan, {})
    assert next(i for i in missing["scenes"][0]["items"] if i["element"] == "text")["reason_code"] == "text_reference_unreadable"


@pytest.mark.parametrize("speech, music, action, blocked_lanes", [
    (("keep", None), ("keep", None), "keep", []),
    (("mute", None), ("mute", None), "mute", []),
    (("mute", None), ("replace", "@song"), "replace", []),
    (("replace", "@song"), ("replace", "@song"), "replace", []),
    (("keep", None), ("replace", "@song"), "keep", ["music"]),        # needs speech/music separation
    (("keep", None), ("mute", None), "keep", ["music"]),
    (("mute", None), ("keep", None), "keep", ["speech"]),
    (("replace", "@a"), ("replace", "@b"), "replace", ["speech"]),    # two different replacements
])
def test_soundtrack_decision_never_pretends_to_separate_a_mixed_stream(speech, music, action, blocked_lanes):
    plan = _plan()
    for lane, (mode, source) in (("speech", speech), ("music", music)):
        plan["tracks"][lane].update(mode=mode, source=source or "original")
    decision = render.audio_decision(plan, source_has_audio=True)
    assert decision["action"] == action
    assert [n["lane"] for n in decision["not_executed"]] == blocked_lanes
    assert all(n["reason_code"] in ("cannot_separate_mixed_stream", "two_references_not_supported")
               for n in decision["not_executed"])


def test_a_source_without_audio_keeps_none_and_a_disabled_lane_is_mute():
    plan = _plan(has_audio=False)
    assert render.audio_decision(plan, source_has_audio=False)["action"] == "none"
    plan = _plan()
    plan["tracks"]["speech"]["enabled"] = False
    plan["tracks"]["music"]["enabled"] = False
    assert render.audio_decision(plan, source_has_audio=True)["action"] == "mute"


def test_cues_text_wrapping_and_cover_defaults():
    assert render.cue_problems({"start": 0.5, "end": 2.0, "text": "ok"}) == []
    assert render.cue_problems({"start": 3, "end": 2, "text": "x"})
    assert render.cue_problems({"start": 0, "end": 1, "text": "   "})
    assert render.cue_problems("nope")
    wrapped, truncated = render.wrap_text("слово " * 80, 400, 40)
    assert truncated and wrapped.count("\n") == render.MAX_TEXT_LINES - 1 and wrapped.endswith("…")
    one, cut = render.wrap_text("short", 400, 40)
    assert one == "short" and cut is False
    plan = _plan()
    default = render.cover_spec(plan, 6.0)
    assert default["requested"] is False and default["time"] == 3.0 and "default" in default["time_source"]
    plan["cover"] = {"time": 99.0, "title": "T", "position": "top"}
    chosen = render.cover_spec(plan, 6.0)
    assert chosen["requested"] and chosen["time"] < 6.0 and chosen["title"] == "T" and chosen["position"] == "top"


def test_geometry_changes_are_explicit_in_the_plan_only():
    plan = _plan()
    geo = render.source_geometry(plan)
    assert (geo["width"], geo["height"]) == (640, 360) and not geo["size_changed"] and not geo["fps_changed"]
    plan["render"].update(width=320, height=240, fps=10)
    geo = render.source_geometry(plan)
    assert (geo["width"], geo["height"], geo["fps"]) == (320, 240, 10.0)
    assert geo["size_changed"] and geo["fps_changed"]


# ------------------------------------------------------------------------------------------ real ffmpeg

@pytest.fixture(scope="module")
def package(tmp_path_factory):
    if not ENV["ready"]:
        pytest.skip("ffmpeg not available")
    folder = tmp_path_factory.mktemp("mwpkg")
    synthetic_package.make_package(folder, size="640x360", fps=25, scene_seconds=2.0, music_seconds=5.0)
    return folder


class Project:
    """A store project over the synthetic package, with helpers to edit it and build a ``prepared`` run."""

    def __init__(self, folder: Path, package: Path) -> None:
        self.package = package
        self.store = store_mod.Store(folder / "data")
        self.pid = self.store.create_project(title="render test")["project_id"]
        self.store.analyze_source(self.pid, package / "source.mp4")
        self.store.add_reference(self.pid, "song", "audio", package / "music.wav")

    def edit(self, *ops: dict) -> None:
        self.store.edit(self.pid, list(ops))

    def prepared(self) -> dict:
        plan = self.store.get_project(self.pid)
        refs = {n: self.store.asset_path(self.pid, r["sha256"], r["ext"]) for n, r in plan["references"].items()
                if r["kind"] == "audio"}
        texts = {n: self.store.asset_path(self.pid, r["sha256"], r["ext"]).read_text(encoding="utf-8")
                 for n, r in plan["references"].items() if r["kind"] == "text"}
        src = self.store.asset_path(self.pid, plan["source"]["asset"], plan["source"]["ext"])
        return render.prepare(plan, source_path=src, ref_paths=refs, texts=texts)

    def render(self, out: Path, **kwargs) -> tuple[dict, dict, dict]:
        prepared = self.prepared()
        result = render.run_render(prepared, out, **kwargs)
        verification = render.verify_output(result["part"], prepared["env"], render.expected_output(prepared))
        return prepared, result, verification


@pytest.fixture
def project(tmp_path, package):
    return Project(tmp_path, package)


@NEEDS_FFMPEG
def test_environment_probes_are_recorded(package):
    assert ENV["ffmpeg"] and ENV["ffprobe"] and ENV["version"]
    assert ENV["filter_script_option"] in ("-/filter_complex", "-filter_complex_script")
    assert ENV["passthrough_option"] in (["-fps_mode", "passthrough"], ["-vsync", "passthrough"])
    assert ENV["has_libx264"] and ENV["has_aac"]


@NEEDS_FFMPEG
def test_plain_render_preserves_resolution_fps_duration_frames_and_is_verified(project, tmp_path):
    prepared, result, verification = project.render(tmp_path / "out")
    assert verification["ok"], verification["checks"]
    video = verification["video"]
    assert (video["width"], video["height"], video["fps"]) == (640, 360, 25.0)
    assert video["codec"] == "h264" and video["pix_fmt"] == "yuv420p"
    assert verification["audio"][0]["codec"] == "aac"
    assert abs(verification["format"]["duration"] - 6.0) < 0.1
    frames = next(c for c in verification["checks"] if c["name"] == "frame_count")
    assert frames["actual"] == frames["expected"] == 150
    # nothing was requested: the run is "complete" and the plan says exactly that
    assert prepared["resolved"]["counts"]["requested"] == 0 and prepared["resolved"]["counts"]["complete"]


@NEEDS_FFMPEG
@NEEDS_TEXT
def test_text_and_subtitles_are_burned_in_and_measured_against_a_no_text_render(project, tmp_path):
    first = "s001"
    project.edit(
        {"op": "set_policy", "scene": first, "element": "text", "policy": "replace", "reason": "owner"},
        {"op": "set_prompt", "scene": first, "prompt": "ПРЕМЬЕРА 100% \"quote\" it's"},
        {"op": "set_track", "lane": "subtitles", "settings": {"enabled": True, "cues": [
            {"start": 2.2, "end": 3.8, "text": "Вторая сцена: реплика"}]}})
    prepared, result, verification = project.render(tmp_path / "with-text")
    assert verification["ok"], verification["checks"]
    ids = [o["id"] for o in prepared["resolved"]["overlays"]]
    assert ids == ["scene_s001", "cue_0000"]
    graph = result["graph"]["filter_complex"]
    assert "fontfile=font.ttf" in graph and "expansion=none" in graph and graph.count("drawtext=") == 2
    assert result["graph"]["textfiles"]["scene_s001.txt"] == "ПРЕМЬЕРА 100% \"quote\" it's"
    evidence = render.overlay_evidence(prepared, Path(result["part"]), tmp_path / "ev")
    assert evidence and all(e["text_present_heuristic"] for e in evidence), evidence
    assert all(e["rest_changed_pixel_ratio"] < 0.001 for e in evidence)

    # negative control: a render of the same source WITHOUT any text must not be measured as having text
    plain = Project(tmp_path / "plain", project.package)
    plain_prepared, plain_result, _ = plain.render(tmp_path / "no-text")
    control = render.overlay_evidence(prepared, Path(plain_result["part"]), tmp_path / "ev-control")
    assert control and not any(e["text_present_heuristic"] for e in control), control


@NEEDS_FFMPEG
@NEEDS_TEXT
def test_cover_is_a_frame_at_the_chosen_time_with_the_title(project, tmp_path):
    from PIL import Image, ImageChops
    project.edit({"op": "set_title", "title": "cover"})
    no_title = tmp_path / "plain"
    prepared = project.prepared()
    plain = render.make_cover(prepared, no_title, cancel=None)
    assert plain["time_source"].startswith("default") and plain["bytes"] > 1000
    project.store.update(project.pid, lambda p: {**p, "cover": {"time": 3.0, "title": "МОЯ ОБЛОЖКА", "position": "center"}})
    prepared = project.prepared()
    titled = render.make_cover(prepared, tmp_path / "titled")
    assert titled["time"] == 3.0 and titled["title"] == "МОЯ ОБЛОЖКА"
    with Image.open(tmp_path / "titled" / "cover.png") as a, Image.open(tmp_path / "plain" / "cover.png") as b:
        assert a.size == b.size == (640, 360)
        assert ImageChops.difference(a.convert("L"), b.convert("L")).getbbox() is not None


@NEEDS_FFMPEG
def test_audio_keep_mute_replace(project, tmp_path):
    # keep: the source AAC stream is copied, nothing re-encoded
    prepared, result, verification = project.render(tmp_path / "keep")
    assert result["audio"]["action"] == "keep" and "copy" in result["audio"].get("codec_action", "")
    assert verification["ok"]
    # mute: no audio stream at all, and the gate expects exactly that
    project.edit({"op": "set_track", "lane": "speech", "settings": {"mode": "mute"}},
                 {"op": "set_track", "lane": "music", "settings": {"mode": "mute"}})
    prepared, result, verification = project.render(tmp_path / "mute")
    assert verification["ok"] and verification["audio"] == []
    # replace: the 5 s reference is trimmed / padded to the 6 s video, faded and loudness-normalised
    project.edit({"op": "set_track", "lane": "music", "settings": {"mode": "replace", "source": "@song"}})
    prepared, result, verification = project.render(tmp_path / "replace")
    assert verification["ok"], verification["checks"]
    audio = result["audio"]
    assert audio["action"] == "replace" and audio["reference"] == "song"
    assert audio["applied_seconds"] == 5.0 and audio["padded_silence_seconds"] == 1.0
    assert audio["loudnorm"]["mode"] == "two-pass (linear)"
    assert abs(float(verification["audio"][0]["duration"]) - 6.0) < 0.1
    measured = render.measure_loudness(prepared["env"], result["part"])
    assert abs(float(measured["input_i"]) - render.LOUDNORM_TARGET["I"]) < 1.5, measured


@NEEDS_FFMPEG
def test_a_replace_lane_without_separation_keeps_the_original_sound_and_says_so(project, tmp_path):
    project.edit({"op": "set_track", "lane": "music", "settings": {"mode": "replace", "source": "@song"}})
    prepared = project.prepared()
    assert prepared["resolved"]["audio"]["action"] == "keep"
    assert prepared["resolved"]["counts"]["not_executed"] == 1
    op = next(o for o in prepared["resolved"]["operations"] if o["status"] == "not_executed")
    assert op["lane"] == "music" and op["reason_code"] == "cannot_separate_mixed_stream"


@NEEDS_FFMPEG
def test_explicit_size_and_fps_changes_fit_and_pad_without_stretching(project, tmp_path):
    project.edit({"op": "set_render", "settings": {"width": 320, "height": 320, "fps": 10}})
    prepared, result, verification = project.render(tmp_path / "small")
    assert verification["ok"], verification["checks"]
    assert (verification["video"]["width"], verification["video"]["height"]) == (320, 320)
    assert verification["video"]["fps"] == 10.0
    chain = " ".join(result["video"]["filters"])
    assert "force_original_aspect_ratio=decrease" in chain and "pad=320:320" in chain and "fps=fps=10" in chain
    assert {o["what"] for o in prepared["resolved"]["operations"] if o["kind"] == "export"} == {"size", "fps"}


@NEEDS_FFMPEG
def test_a_corrupted_or_wrong_file_never_passes_the_gate(project, tmp_path):
    prepared, result, verification = project.render(tmp_path / "ok")
    assert verification["ok"]
    good = Path(result["part"])
    expect = render.expected_output(prepared)
    # truncated file
    cut = tmp_path / "truncated.mp4"
    cut.write_bytes(good.read_bytes()[: good.stat().st_size // 3])
    assert render.verify_output(cut, prepared["env"], expect)["ok"] is False
    # garbage with a plausible size
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"\x00" * 5000)
    assert render.verify_output(junk, prepared["env"], expect)["ok"] is False
    # a valid file that is not what the plan promised
    wrong = dict(expect, width=expect["width"] * 2)
    checks = render.verify_output(good, prepared["env"], wrong)
    assert checks["ok"] is False and [c["name"] for c in checks["checks"] if not c["ok"]] == ["width"]
    # bytes flipped in the middle: the full decode (not just ffprobe) is what catches it
    flipped = tmp_path / "flipped.mp4"
    raw = bytearray(good.read_bytes())
    mid = len(raw) // 2
    raw[mid:mid + 4000] = bytes((b ^ 0xFF) for b in raw[mid:mid + 4000])
    flipped.write_bytes(bytes(raw))
    result_flipped = render.verify_output(flipped, prepared["env"], expect)
    assert result_flipped["ok"] is False


@NEEDS_FFMPEG
def test_cancel_before_start_and_timeout_stop_the_process(project, tmp_path):
    prepared = project.prepared()
    cancelled = threading.Event()
    cancelled.set()
    started = time.monotonic()
    with pytest.raises(render.RenderCancelled):
        render.run_render(prepared, tmp_path / "c1", cancel=cancelled)
    assert time.monotonic() - started < 10
    assert not (tmp_path / "c1" / "ffmpeg.pid").exists()
    with pytest.raises(render.RenderTimeout):
        render.run_render(prepared, tmp_path / "c2", timeout=0.01)
    assert not (tmp_path / "c2" / "ffmpeg.pid").exists()


@NEEDS_FFMPEG
def test_cancel_in_the_middle_of_a_slow_render(tmp_path):
    big = tmp_path / "big"
    synthetic_package.make_package(big, size="1280x720", fps=30, scene_seconds=8.0, music_seconds=5.0)
    slow = Project(tmp_path / "slow", big)
    slow.edit({"op": "set_render", "settings": {"preset": "veryslow", "crf": 0}})
    prepared = slow.prepared()
    cancel = threading.Event()
    seen: list[float] = []

    def on_progress(stage: str, fraction: float, detail: dict) -> None:
        seen.append(fraction)
        if detail.get("out_time") and detail["out_time"] > 0.2:
            cancel.set()

    started = time.monotonic()
    with pytest.raises(render.RenderCancelled):
        render.run_render(prepared, tmp_path / "out", cancel=cancel, on_progress=on_progress)
    assert time.monotonic() - started < 60
    assert seen and max(seen) < 1.0
    assert not (tmp_path / "out" / "ffmpeg.pid").exists()


@NEEDS_FFMPEG
def test_missing_ffmpeg_is_reported_not_faked(project, monkeypatch):
    monkeypatch.setattr(analyze_mod, "find_ffmpeg", lambda: None)
    monkeypatch.setattr(analyze_mod, "find_ffprobe", lambda ffmpeg=None: None)
    env = render.environment(refresh=True)
    assert env["ready"] is False and any("ffmpeg not found" in p for p in env["problems"])
    plan = project.store.get_project(project.pid)
    with pytest.raises(render.RenderUnavailable):
        render.prepare(plan, source_path=project.package / "source.mp4", ref_paths={}, texts={}, env=env)


@NEEDS_FFMPEG
def test_missing_font_blocks_only_the_text_not_the_render(project, monkeypatch):
    monkeypatch.setattr(render, "find_font", lambda: None)
    env = render.environment()
    assert env["ready"] is True and env["text_ready"] is False and env["font"] is None
    prepared = project.prepared()                 # no text in the plan: fine
    assert prepared["font"] is None
    project.edit({"op": "set_policy", "scene": "s001", "element": "text", "policy": "replace", "reason": "owner"},
                 {"op": "set_prompt", "scene": "s001", "prompt": "needs a font"})
    with pytest.raises(render.RenderUnavailable):
        project.prepared()


@NEEDS_FFMPEG
def test_the_report_records_actual_parameters_and_no_cloud_spend(project, tmp_path):
    project.edit({"op": "set_prompt", "scene": "s002", "prompt": "red jacket"},
                 {"op": "set_policy", "scene": "s002", "element": "clothes", "policy": "replace", "reason": "owner"})
    prepared, result, verification = project.render(tmp_path / "out")
    final = Path(result["part"]).with_name("output.mp4")
    render.finalize_output(result["part"], final)
    report = render.build_report(
        prepared=prepared, job={"project_id": project.pid, "job_id": "job_000000000001", "plan_version": 9},
        mode="full", status="done", render=result, verification=verification, preview=None, cover=None,
        output_file=final, evidence=[], timings={"render_seconds": result["wall_seconds"]}, telegram=None)
    path = tmp_path / "out" / "render_report.json"
    render.write_report(report, path)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["status"] == "done" and loaded["verified"] is True
    assert loaded["environment"]["ffmpeg"]["version"] and loaded["environment"]["font"]["path"]
    assert loaded["input"]["sha256"] == prepared["source"]["sha256"] and len(loaded["output"]["sha256"]) == 64
    assert loaded["commands"][0]["argv"][0].lower().endswith("ffmpeg.exe") or "ffmpeg" in loaded["commands"][0]["argv"][0].lower()
    assert loaded["execution"]["not_executed"] == 1 and loaded["execution"]["every_requested_operation_applied"] is False
    s002 = next(s for s in loaded["scenes"] if s["scene_id"] == "s002")
    clothes = next(i for i in s002["items"] if i["element"] == "clothes")
    assert clothes["status"] == "not_executed" and clothes["reason_code"] == "needs_generative_tool"
    assert loaded["cloud_spend_usd"] == 0.0 and loaded["paid_calls"] == 0
    assert loaded["output"]["video"]["width"] == 640 and loaded["output"]["format"]["bytes"] == final.stat().st_size


def test_a_plan_is_not_mutated_by_resolving_it():
    plan = _plan()
    plan["scenes"][0]["elements"]["text"].update(policy="replace", reason="owner")
    plan["scenes"][0]["prompt"] = "hello"
    before = copy.deepcopy(plan)
    render.resolve(plan)
    assert plan == before
