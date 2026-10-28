from pathlib import Path

from bcc.features.game_bootstrap_v16 import (
    BootstrapRequest,
    GodotExportRequest,
    VoxelDirectorRequest,
    WINDOWS_EDITOR,
    WINDOWS_TEMPLATE,
    apply_voxel_director_command,
    bossblocks_bootstrap_plan,
    export_godot_voxel_project,
    render_voxel_scene_screenshot_png,
    render_voxel_web_game_html,
)


def test_windows_plan_is_pinned_finite_and_has_fallback():
    plan = bossblocks_bootstrap_plan(BootstrapRequest())
    assert plan["status"] == "PLAN_ONLY"
    assert plan["primary"]["editor"]["sha256"] == WINDOWS_EDITOR["sha256"]
    assert plan["primary"]["template"]["sha256"] == WINDOWS_TEMPLATE["sha256"]
    assert len(plan["steps"]) == 7
    assert plan["fallback"]["trigger"].startswith("primary bootstrap/plugin path not READY")
    assert plan["hard_deadline_minutes"] == 20


def test_plan_never_claims_download_happened():
    plan = bossblocks_bootstrap_plan(BootstrapRequest())
    assert "PLAN_ONLY" in plan["truth_rule"]
    assert all(step["verify"] for step in plan["steps"])


def test_telegram_control_keeps_owner_authority():
    plan = bossblocks_bootstrap_plan(BootstrapRequest())
    commands = plan["telegram_control"]["commands"]
    assert "/approvals" in commands
    assert "/stop" in commands
    assert "/resume" in commands
    assert "cannot approve" in commands["/jev <request>"]


def test_non_windows_does_not_invent_windows_success():
    plan = bossblocks_bootstrap_plan(BootstrapRequest(platform="linux"))
    assert plan["status"] == "FALLBACK"
    assert plan["steps"] == []


def test_existing_engine_can_skip_primary_download_plan():
    plan = bossblocks_bootstrap_plan(BootstrapRequest(already_has_compatible_engine=True))
    assert plan["steps"] == []
    assert plan["status"] == "PLAN_ONLY"


def test_voxel_director_live_scene_mutation_weather_camera_structures_npcs_and_sound():
    # 1. Инициализация мира и построек по текстовому промпту
    res1 = apply_voxel_director_command(
        VoxelDirectorRequest(
            world_id="test-genie-world",
            prompt="Смени погоду на грозу, переставь камеру на дрон-облёт, построй башню и дом, добавь торговца и стражника",
            reset_world=True,
            use_local_llm=False,
        )
    )
    assert res1["ok"] is True
    assert res1["cost_usd"] == 0.0
    assert res1["weather"]["id"] == "storm"
    assert res1["weather"]["particles"] == "rain_heavy"
    assert res1["camera"]["mode"] == "drone_orbit"
    assert res1["camera"]["orbit"] is True
    assert {s["kind"] for s in res1["structures"]} >= {"tower", "house"}
    assert len(res1["npcs"]) == 2
    assert any("Торговец" in n["dialogue"] for n in res1["npcs"])
    assert res1["soundscape"]["ambient_track"] == "thunder_wind"
    assert len(res1["soundscape"]["music_scale_hz"]) >= 5

    # 2. Живое изменение погоды и камеры на ходу без сброса построенных зданий
    blocks_before = res1["block_count"]
    res2 = apply_voxel_director_command(
        VoxelDirectorRequest(
            world_id="test-genie-world",
            prompt="Смени погоду на снегопад, переставь камеру от третьего лица и построй пирамиду",
            reset_world=False,
            use_local_llm=False,
        )
    )
    assert res2["weather"]["id"] == "snow"
    assert res2["camera"]["mode"] == "third_person"
    assert res2["block_count"] > blocks_before
    assert any("Снегопад" in n["dialogue"] for n in res2["npcs"])


def test_voxel_web_game_html_and_godot_export(tmp_path: Path):
    html = render_voxel_web_game_html("test-genie-world")
    assert "BossBlocks Genie-Local" in html
    assert "/api/game-bootstrap/voxel-director" in html

    export_res = export_godot_voxel_project(
        GodotExportRequest(
            world_id="test-godot-export",
            prompt="Смени погоду на грозу, поставь камеру от третьего лица, построй башню и дом, добавь стражника",
            target_dir=str(tmp_path / "godot-voxel"),
            run_headless_smoke=False,
            use_local_llm=False,
        )
    )
    assert export_res["ok"] is True
    proj = Path(export_res["project_dir"])
    assert (proj / "project.godot").is_file()
    assert (proj / "scripts" / "main.gd").is_file()
    assert (proj / "bossblocks_world.json").is_file()
    assert (proj / "PLAY-BOSSBLOCKS.cmd").is_file()


def test_comedy_3d_models_structures_and_screenshot_png(tmp_path: Path):
    state = apply_voxel_director_command(
        VoxelDirectorRequest(
            world_id="test-comedy-3d",
            prompt="Смени погоду на ночь, построй джакузи, кровать-сердце, неоновое кабаре и статую Венеры, добавь шуточные интим 3D модели: Леди Вельвет, Мачо Адонис, Купидон-Вышибала и Кибер-Сирена",
            reset_world=True,
            use_local_llm=False,
        )
    )
    assert state["ok"] is True
    struct_kinds = {s["kind"] for s in state["structures"]}
    assert {"hot_tub", "heart_bed", "neon_cabaret", "statue_venus"} <= struct_kinds
    npc_roles = {n["role"] for n in state["npcs"]}
    assert {"velvet_diva", "pool_adonis", "cupid_bouncer", "cyber_siren"} <= npc_roles
    for npc in state["npcs"]:
        assert isinstance(npc.get("rig"), list)
        assert len(npc["rig"]) >= 8
        parts = {p["part"] for p in npc["rig"]}
        assert {"leg_l", "leg_r", "head"} <= parts

    shot = render_voxel_scene_screenshot_png(
        state,
        tmp_path / "comedy_3d_shot.png",
        wave_label="Wave 01 Test",
        agent_summary="3D-модели и неоновое кабаре готовы",
    )
    assert shot.is_file()
    assert shot.stat().st_size > 15000
