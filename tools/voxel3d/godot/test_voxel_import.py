"""Bossman voxel3d objects load in BossBlocks (installed by tools/voxel3d).

Reuses test_vertical's Godot binary lookup. The editor's --import step prints localized UTF-8
progress text, so this runner decodes UTF-8 explicitly (test_vertical.run_godot uses the ANSI
code page and is left untouched).
"""

import subprocess

from test_vertical import ROOT, godot_binary


def _godot(*args: str, timeout: int = 180) -> str:
    result = subprocess.run([godot_binary(), "--headless", "--path", str(ROOT), *args], cwd=ROOT,
                            capture_output=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "SCRIPT ERROR" not in output and "ERROR:" not in output, output
    return output


def test_voxel_assets_import_and_stamp():
    _godot("--import")
    output = _godot("--script", "res://tests/voxel_import.gd")
    assert "VOXEL_IMPORT_PASS" in output
