"""Comprehensive test suite for Art Studio v2.0 «Вундеркинд» & Video Studio 2.0 (7/7 Gates).

Tests all 4 core subsystems:
1. Photo Studio 2.0 + VoxelToRenderBridge (3D passes)
2. Video Studio 2.0 pipelines + 7/7 Quality Gates runner
3. Motion Timeline, Bezier easing, AudioBeatAnalyzer BPM, Kinetic ASS/VTT, 3D LUT .cube
4. VLM Art Critic self-check, auto-correction loop, and signed ArtQCReceipt
5. Catalog integration for Wan 2.1, Wan 2.2, and LTX-Video
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from bcc.art_v2.photo_engine import (
    DynamicWorkflowCompiler,
    ModelFamily,
    VoxelToRenderBridge,
    WorkflowMode,
)
from bcc.art_v2.video_engine import (
    LTXVideoPipeline,
    LivePortraitLipSync,
    QualityGateRecord,
    QualityGatesResult,
    RIFEInterpolator,
    Wan21VACEPipeline,
    Wan22T2VVACEPipeline,
    run_video_studio_v2_all_7_gates,
)
from bcc.art_v2.motion_timeline import (
    AnimatedProperty,
    AudioBeatAnalyzer,
    AudioTrack,
    Easing,
    ImageTrack,
    Keyframe,
    KineticTextTrack,
    LUTPreset,
    MotionSceneGraph,
    ParticleShaderTrack,
    VideoTrack,
    Voxel3DTrack,
    evaluate_easing,
)
from bcc.art_v2.vlm_critic import (
    ArtQCReceipt,
    KeyframeMetrics,
    VLMArtCritic,
)
from bcc.features.motion_workflow import catalog as mw_catalog


# ==============================================================================
# SUBSYSTEM 1: PHOTO STUDIO 2.0 & 3D VOXEL BRIDGE
# ==============================================================================

def test_dynamic_workflow_compiler_5_modes():
    compiler = DynamicWorkflowCompiler()

    # 1. txt2img_studio
    wf1 = compiler.compile_txt2img("gorgeous portrait", aspect_ratio="16:9", guidance=4.0, seed=123)
    assert wf1["mode"] == "txt2img_studio"
    assert wf1["seed"] == 123
    assert wf1["guidance"] == 4.0
    assert compiler.validate_workflow(wf1) is True

    # 2. character_lock
    wf2 = compiler.compile_character_lock("ref_diva.png", "diva dancing", ip_adapter_weight=0.90)
    assert wf2["mode"] == "character_lock"
    assert wf2["ip_adapter_weight"] == 0.90
    assert wf2["min_arcface_gate"] == 0.78
    assert "13" in wf2["nodes"]  # IPAdapterFaceIDPlusV2 node
    assert compiler.validate_workflow(wf2) is True

    # 3. pose_and_structure
    wf3 = compiler.compile_pose_and_structure(
        "diva on stage",
        openpose_image="pose.png",
        depth_map_image="depth.png",
        canny_image="canny.png",
        openpose_weight=0.85,
        depth_weight=0.75,
        canny_weight=0.50,
    )
    assert wf3["mode"] == "pose_and_structure"
    assert wf3["control_weights"]["openpose"] == 0.85
    assert wf3["control_weights"]["depth_anything_v2"] == 0.75
    assert compiler.validate_workflow(wf3) is True

    # 4. inpaint_outpaint_canvas
    wf4 = compiler.compile_inpaint_outpaint("base.png", "mask.png", "change dress color to gold", mode="inpaint")
    assert wf4["mode"] == "inpaint_outpaint_canvas"
    assert "35" in wf4["nodes"]  # DifferentialDiffusion
    assert compiler.validate_workflow(wf4) is True

    # 5. detailer_and_upscale
    wf5 = compiler.compile_detailer_and_upscale("input.png", target_resolution="4k_16:9", upscale_model="RealESRGAN_4x")
    assert wf5["mode"] == "detailer_and_upscale"
    assert wf5["target_resolution"] == [3840, 2160]
    assert wf5["face_detailer_applied"] is True
    assert compiler.validate_workflow(wf5) is True


def test_flux_and_sdxl_families_compilation():
    compiler = DynamicWorkflowCompiler()

    # Flux Dev
    wf_flux = compiler.compile_txt2img("cyber diva", model_family=ModelFamily.FLUX_DEV, guidance=3.5)
    assert wf_flux["model_family"] == "flux_dev"
    assert "UNETLoader" == wf_flux["nodes"]["1"]["class_type"]
    assert "FluxGuidance" == wf_flux["nodes"]["5"]["class_type"]
    assert compiler.validate_workflow(wf_flux) is True

    # SDXL Lightning
    wf_sdxl = compiler.compile_txt2img("cyber diva", model_family=ModelFamily.SDXL_LIGHTNING)
    assert wf_sdxl["model_family"] == "sdxl_lightning"
    assert "CheckpointLoaderSimple" == wf_sdxl["nodes"]["1"]["class_type"]
    assert compiler.validate_workflow(wf_sdxl) is True


def test_character_lock_ipadapter_and_arcface_threshold():
    compiler = DynamicWorkflowCompiler()
    wf = compiler.compile_character_lock(
        reference_image_path="lady_velvet_ref.png",
        prompt="velvet diva singing in neon cabaret",
        character_name="velvet_diva",
        ip_adapter_weight=0.88,
        face_similarity_target=0.84,
    )
    assert wf["character_name"] == "velvet_diva"
    assert wf["arcface_target_cosine"] >= 0.78
    assert wf["nodes"]["13"]["class_type"] == "IPAdapterFaceIDPlusV2"
    assert wf["nodes"]["13"]["inputs"]["weight"] == 0.88


def test_voxel_to_render_bridge_exports_3_passes():
    bridge = VoxelToRenderBridge()

    for char_id in ("velvet_diva", "pool_adonis", "cupid_bouncer", "cyber_siren"):
        passes = bridge.export_all_passes(char_id, width=1280, height=720)
        assert passes["character_id"] == char_id

        # 1. RGB Pass
        rgb = passes["rgb_pass"]
        assert rgb["pass_type"] == "rgb_pass"
        assert rgb["palette"]["outfit"].startswith("#")
        assert len(rgb["objects_in_view"]) >= 2
        assert "pass_hash" in rgb

        # 2. Z-Depth Map
        depth = passes["z_depth_map"]
        assert depth["pass_type"] == "z_depth_map"
        assert 0.0 <= depth["planes"]["actor_depth_normalized"] <= 1.0
        assert depth["depth_range"] == [0.0, 1.0]

        # 3. Skeleton Pose Keypoints
        pose = passes["skeleton_pose_keypoints"]
        assert pose["pass_type"] == "skeleton_pose_keypoints"
        assert pose["keypoint_count"] == 18
        assert all(0.0 <= kp["confidence"] <= 1.0 for kp in pose["keypoints"])
        assert any(kp["name"] == "nose" for kp in pose["keypoints"])


def test_voxel_bridge_compiles_controlnet_render_workflow():
    bridge = VoxelToRenderBridge()
    render_wf = bridge.compile_render_workflow("velvet_diva", style_preset="velvet_cabaret")
    assert render_wf["mode"] == "pose_and_structure"
    assert "voxel_bridge_passes" in render_wf
    assert render_wf["voxel_bridge_passes"]["character_id"] == "velvet_diva"
    assert "20" in render_wf["nodes"]  # ControlNetLoader
    assert bridge.compiler.validate_workflow(render_wf) is True


# ==============================================================================
# SUBSYSTEM 2: VIDEO STUDIO 2.0 PIPELINES & 7/7 QUALITY GATES
# ==============================================================================

def test_video_pipelines_are_described_not_wired_and_never_pretend_to_generate():
    """Audit 28.10: these classes used to return status COMPLETED, a prompt hash and constants (vram 11.8 GB, teacache 2.5x)
    without running anything. They must refuse loudly until a real generator is connected."""
    from bcc.art_v2.video_engine import NOT_WIRED_MESSAGE, WIRED, PipelineNotWired
    assert WIRED is False
    cases = [
        lambda: Wan21VACEPipeline(variant="1.3b", enable_teacache=True).run("cabaret diva walks in slow motion", frames=49, steps=30),
        lambda: Wan21VACEPipeline(variant="14b").run("close-up face lighting", frames=33, steps=25),
        lambda: Wan22T2VVACEPipeline(use_distilled_lora=True).run("neon lights reflection", frames=81, steps=4),
        lambda: LTXVideoPipeline().run("cinematic drone shot", frames=97),
        lambda: LivePortraitLipSync().process("face.png", "vocals.wav", separate_audio=True),
        lambda: RIFEInterpolator().interpolate_24_to_60(input_fps=24, target_fps=60, enable_deflicker=True),
    ]
    for call in cases:
        with pytest.raises(PipelineNotWired) as exc:
            call()
        assert NOT_WIRED_MESSAGE in str(exc.value)
    assert issubclass(PipelineNotWired, NotImplementedError)
    # the description stays: a bad resolution is still a plain input error, not a silent success
    with pytest.raises(ValueError):
        LTXVideoPipeline().run("x", resolution="not-a-size")


def test_video_studio_v2_gates_are_reported_as_not_passed_with_the_last_real_observations(tmp_path: Path):
    """Audit 28.10: run_video_studio_v2_all_7_gates used to return 7/7 PASS from hard-coded numbers (ArcFace 0.842, LPIPS
    0.138, sync 0.995 ...). No video had been measured. Now every gate is NOT passed and carries what was really observed."""
    result = run_video_studio_v2_all_7_gates("test_proj_v2", output_dir=tmp_path)
    assert result["overall_pass"] is False and result["gates_passed"] == 0 and result["total_gates"] == 7
    assert result["status"] == "NOT_MEASURED" and result["wired"] is False
    gates = result["gates"]
    assert set(gates) == {"face_identity", "clothes_body", "hair_contour", "mimic_lipsync", "backgrounds_7_of_7",
                          "sharpness_laplacian", "temporal_deflicker"}
    for g in gates.values():
        assert g["passed"] is False and g["measured"] is None and g["margin_ratio"] is None
        assert g["details"]["measured_here"] is False and g["details"]["last_observed"]["source"]
    assert gates["face_identity"]["details"]["last_observed"]["status"] == "FAIL"
    assert "0.516" in gates["face_identity"]["details"]["last_observed"]["observed"]
    assert gates["clothes_body"]["details"]["last_observed"]["status"] == "FAIL"
    assert gates["hair_contour"]["details"]["last_observed"]["status"] == "NOT_MEASURED"
    for invented in ("0.842", "0.138", "0.995", "0.961", "0.916", "0.982"):
        assert invented not in json.dumps(result), f"invented number {invented} is back"
    saved = json.loads((tmp_path / "receipt.json").read_text(encoding="utf-8"))
    assert saved["overall_pass"] is False and saved["provenance_hash"] == result["provenance_hash"]


# ==============================================================================
# SUBSYSTEM 3: MOTION TIMELINE & BEAT-SYNC BPM
# ==============================================================================

def test_motion_scene_graph_tracks_and_bezier_curves():
    sg = MotionSceneGraph(project_id="kinetic_promo", width=1920, height=1080, fps=60)
    assert len(sg.tracks) >= 6

    # Test Bezier curves
    t_vals = [0.0, 0.25, 0.5, 0.75, 1.0]
    for t in t_vals:
        v_expo = evaluate_easing(t, Easing.EASE_OUT_EXPO)
        v_back = evaluate_easing(t, Easing.EASE_IN_OUT_BACK)
        v_spring = evaluate_easing(t, Easing.SPRING)
        assert 0.0 <= v_expo <= 1.0
        assert -0.5 <= v_back <= 1.5  # Back easing overshoots intentionally
        assert -0.5 <= v_spring <= 1.5

    # Animate property on KineticTextTrack
    txt_track = sg.get_track("kinetic_text_0")
    assert txt_track is not None
    prop = txt_track.properties["scale"]
    prop.add_keyframe(0.0, 0.0, Easing.EASE_OUT_EXPO)
    prop.add_keyframe(500.0, 1.2, Easing.EASE_IN_OUT_BACK)
    prop.add_keyframe(1000.0, 1.0, Easing.SPRING)

    assert prop.evaluate(0.0) == 0.0
    assert prop.evaluate(250.0) > 0.0
    assert prop.evaluate(1000.0) == 1.0

    plan = sg.render_plan()
    assert plan["render_fps"] == 60
    assert plan["dimensions"] == [1920, 1080]


def test_audio_beat_analyzer_bpm_and_cut_points():
    analyzer = AudioBeatAnalyzer(default_bpm=128.0)
    res = analyzer.analyze_audio_bytes(b"dummy_short_audio")
    assert isinstance(res["bpm_measured"], float)
    assert res["bpm_measured"] == 128.0
    assert len(res["beat_timestamps_ms"]) >= 8

    # Beat sync cut points (e.g. cut every 4 beats at 120 BPM -> 2000ms intervals)
    cuts = analyzer.generate_beat_sync_cut_points(duration_ms=10000.0, bpm=120.0, interval_beats=4)
    assert cuts == [2000.0, 4000.0, 6000.0, 8000.0]


def test_kinetic_subtitles_and_3d_lut_cube_generation():
    analyzer = AudioBeatAnalyzer()

    subs = [
        {"start_s": 0.5, "end_s": 2.5, "text": "Bossman Video Studio 2.0"},
        {"start_s": 2.6, "end_s": 4.8, "text": "Семь из семи гейтов пройдены"},
    ]

    # 1. ASS with karaoke
    ass_text = analyzer.generate_ass(subs, style_name="NeonCyber")
    assert "[Script Info]" in ass_text
    assert "Style: NeonCyber" in ass_text
    assert r"{\blur2}{\k" in ass_text
    assert "Bossman" in ass_text and "Video" in ass_text and "Studio" in ass_text and "2.0" in ass_text

    # 2. WebVTT
    vtt_text = analyzer.generate_webvtt(subs)
    assert "WEBVTT" in vtt_text
    assert "00:00:00.500 --> 00:00:02.500" in vtt_text

    # 3. 3D LUT Presets (.cube)
    for preset in (LUTPreset.TEAL_AND_ORANGE, LUTPreset.CYBERPUNK_NEON, LUTPreset.VELVET_FILM_35MM):
        cube = analyzer.generate_lut_cube(preset, size=17)
        assert f"TITLE \"{preset.value}\"" in cube
        assert "LUT_3D_SIZE 17" in cube
        assert "DOMAIN_MIN 0.0 0.0 0.0" in cube
        assert "DOMAIN_MAX 1.0 1.0 1.0" in cube
        # Check that cube has 17^3 data lines
        data_lines = [l for l in cube.splitlines() if l and not l.startswith("#") and not l.startswith("TITLE") and not l.startswith("LUT_") and not l.startswith("DOMAIN_")]
        assert len(data_lines) == 17 * 17 * 17


# ==============================================================================
# SUBSYSTEM 4: VLM ART CRITIC & QC SELF-REPAIR GATE
# ==============================================================================

def test_vlm_art_critic_deterministic_metrics_and_checklist():
    critic = VLMArtCritic()
    metrics = critic.compute_deterministic_metrics()

    assert metrics.non_black_ratio >= 0.90
    assert metrics.face_cosine_score >= 0.78
    assert metrics.laplacian_sharpness >= 10.0
    assert len(metrics.artifacts_detected) == 0

    keyframes = critic.extract_keyframes(None)
    assert len(keyframes) == 5
    score = critic.calculate_aggregate_score(keyframes)
    assert score >= 0.90


def test_vlm_art_critic_auto_correction_loop_and_signed_receipt():
    critic = VLMArtCritic()

    # Pass 1: simulate initial defect on 50% keyframe
    receipt = critic.inspect_and_certify(
        project_id="proj_art_auto_correct",
        prompt="velvet diva high fashion",
        seed=100,
        controlnet_weight=0.85,
        simulate_initial_flaw=True,
    )

    assert receipt.status == "PASS"
    assert receipt.score >= 0.90
    assert receipt.retries_used == 1  # Successfully repaired on retry!
    assert len(receipt.corrections_applied) == 1
    assert receipt.corrections_applied[0]["old_seed"] == 100
    assert receipt.corrections_applied[0]["new_seed"] == 1101
    assert receipt.non_black_pass is True
    assert receipt.face_cosine_pass is True
    assert receipt.artifacts_clean_pass is True
    assert len(receipt.signature) == 64  # SHA-256 signature


# ==============================================================================
# SUBSYSTEM 5: CATALOG INTEGRATION
# ==============================================================================

def test_motion_workflow_catalog_describes_wan_and_ltx_but_claims_no_support_without_a_generator():
    # Audit 28.10: the catalog used to say «supported» for these models through a facade engine.
    for model in ("wan2.1-t2v-1.3b", "wan21-vace-1.3b", "wan21-vace-14b", "wan2.2-t2v-vace-a14b", "wan2.2-animate-14b",
                  "ltx-video", "ltx-video-0.9.5"):
        assert mw_catalog.is_supported(model) is False, f"{model} cannot be supported while no generator is wired"

    # the pipeline objects still resolve (the catalog describes them) but they refuse to run
    assert isinstance(mw_catalog.resolve_pipeline("wan2.1-t2v-1.3b"), Wan21VACEPipeline)
    assert isinstance(mw_catalog.resolve_pipeline("wan2.2-t2v-vace-a14b"), Wan22T2VVACEPipeline)
    assert isinstance(mw_catalog.resolve_pipeline("ltx-video"), LTXVideoPipeline)

    cat = mw_catalog.load_catalog()
    assert cat["engine"] == "art_v2.video_engine"
    ids = {m["id"] for m in cat["models"]}
    assert {"wan2.1-t2v-1.3b", "wan2.2-t2v-vace-a14b", "ltx-video"} <= ids
    for m in cat["models"]:
        assert m["supported"] is False and "no video generator connected" in m["unsupported"]
