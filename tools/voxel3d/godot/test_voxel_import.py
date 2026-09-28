"""Bossman voxel3d objects load in BossBlocks (installed by tools/voxel3d; reuses test_vertical's Godot runner)."""

from test_vertical import run_godot


def test_voxel_assets_import_and_stamp():
    run_godot("--import")
    output = run_godot("--script", "res://tests/voxel_import.gd")
    assert "VOXEL_IMPORT_PASS" in output
