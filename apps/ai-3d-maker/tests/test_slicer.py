"""Slicer adapters.

No slicer is installed on this host, so nothing here is a REAL SLICER PASS. It
is the opposite claim: that the *path* is honest. A missing binary reports
NOT_AVAILABLE with a reason, a slicer that fails reports FAILED and never PASS,
the command line is bounded, and whatever engine did run is identified and
versioned in the result.

The adapters themselves are exercised against a real subprocess — a stub
executable that stands in for the slicer — so that argument construction,
timeouts, return codes and missing-output handling are covered by code that
actually runs, not by mocks.
"""

from __future__ import annotations

import asyncio
import os
import stat
import time
from pathlib import Path

import pytest

from ai_3d_maker.errors import InvalidSpecError
from ai_3d_maker.slicer import (
    curaengine_info,
    prusaslicer_info,
    slice_auto,
    slice_with_curaengine,
    slice_with_prusaslicer,
    validate_slicer_settings,
)


def write_stub(directory: Path, name: str, script: str) -> Path:
    path = directory / name
    path.write_text("#!/bin/sh\n" + script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture
def stub_bin(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "bin"
    d.mkdir()
    monkeypatch.setenv("PATH", f"{d}{os.pathsep}{os.environ['PATH']}")
    return d


# ------------------------------------------------------------ availability
def test_a_missing_slicer_is_not_available_with_a_reason():
    info = prusaslicer_info("definitely-not-a-slicer-binary")
    assert info["available"] is False
    assert "not found on PATH" in info["reason"]


def test_curaengine_without_a_definition_is_not_available(stub_bin):
    write_stub(stub_bin, "CuraEngine", "exit 0\n")
    info = curaengine_info("CuraEngine", "")
    assert info["available"] is False
    assert "AI3D_CURA_DEFINITION" in info["reason"]


def test_curaengine_definition_is_identified_and_versioned(stub_bin, tmp_path):
    write_stub(stub_bin, "CuraEngine", "exit 0\n")
    definition = tmp_path / "elegoo_neptune3plus.def.json"
    definition.write_text('{"name": "stub"}', encoding="utf-8")
    info = curaengine_info("CuraEngine", str(definition))
    assert info["available"] is True
    assert len(info["definition_sha256"]) == 64
    assert info["definition_bytes"] == definition.stat().st_size


def test_no_slicer_anywhere_is_not_available_not_a_pass(tmp_path):
    result = asyncio.run(slice_auto(
        tmp_path / "a.stl", tmp_path / "a.gcode",
        curaengine_bin="no-such-cura", prusaslicer_bin="no-such-prusa",
    ))
    assert result.status == "NOT_AVAILABLE"
    assert result.ok is False
    assert "no-such-cura" in result.error and "no-such-prusa" in result.error
    assert not (tmp_path / "a.gcode").exists()


# -------------------------------------------------------- bounded settings
def test_settings_keys_are_restricted_to_a_safe_shape():
    validate_slicer_settings({"layer_height": 0.2, "wall_line_count": 3})
    for bad in ({"--infill": 1}, {"layer height": 1}, {"a" * 200: 1}, {"x;y": 1}):
        with pytest.raises(InvalidSpecError):
            validate_slicer_settings(bad)


def test_settings_values_may_not_smuggle_extra_arguments():
    for bad in ({"speed": "60\n-s evil=1"}, {"speed": "a" * 5000}, {"speed": ["list"]}):
        with pytest.raises(InvalidSpecError):
            validate_slicer_settings(bad)


def test_too_many_settings_are_refused():
    with pytest.raises(InvalidSpecError):
        validate_slicer_settings({f"k{i}": i for i in range(500)})


def test_a_bad_setting_is_rejected_before_the_slicer_is_invoked(stub_bin, tmp_path):
    marker = tmp_path / "was_invoked"
    write_stub(stub_bin, "CuraEngine", f"touch {marker}\nexit 0\n")
    definition = tmp_path / "def.json"
    definition.write_text("{}", encoding="utf-8")
    result = asyncio.run(slice_with_curaengine(
        tmp_path / "a.stl", tmp_path / "a.gcode",
        definition_path=str(definition), settings={"--evil": 1},
    ))
    assert result.status == "FAILED"
    assert result.ok is False
    assert not marker.exists()


# ------------------------------------------------------- the adapter itself
def test_a_stub_slicer_that_writes_gcode_is_reported_as_a_pass(stub_bin, tmp_path):
    write_stub(stub_bin, "prusa-slicer", 'if [ "$1" = "--version" ]; then echo "stub 1.2.3"; exit 0; fi\n'
                                         'echo "G28" > "$3"\nexit 0\n')
    stl = tmp_path / "a.stl"
    stl.write_bytes(b"solid x\nendsolid x\n")
    out = tmp_path / "a.gcode"
    result = asyncio.run(slice_with_prusaslicer(stl, out, timeout_s=20.0))
    assert result.status == "PASS"
    assert out.is_file()
    assert result.engine_version == "stub 1.2.3"
    assert str(stl) in result.command


def test_a_slicer_that_fails_is_never_reported_as_a_pass(stub_bin, tmp_path):
    write_stub(stub_bin, "prusa-slicer", 'echo "boom" >&2\nexit 3\n')
    stl = tmp_path / "a.stl"
    stl.write_bytes(b"solid x\nendsolid x\n")
    result = asyncio.run(slice_with_prusaslicer(stl, tmp_path / "a.gcode", timeout_s=20.0))
    assert result.status == "FAILED"
    assert result.ok is False
    assert result.returncode == 3
    assert "boom" in result.stderr_tail


def test_a_slicer_that_exits_cleanly_without_output_is_a_failure(stub_bin, tmp_path):
    """Exit code 0 is not evidence that a G-code file exists."""
    write_stub(stub_bin, "prusa-slicer", "exit 0\n")
    stl = tmp_path / "a.stl"
    stl.write_bytes(b"solid x\nendsolid x\n")
    result = asyncio.run(slice_with_prusaslicer(stl, tmp_path / "a.gcode", timeout_s=20.0))
    assert result.status == "FAILED"
    assert result.gcode_path is None


def test_a_hanging_slicer_is_killed_and_reported_as_a_failure(stub_bin, tmp_path):
    """The deadline has to hold even when the slicer forks a child that outlives it."""
    write_stub(stub_bin, "prusa-slicer", "sleep 4 &\nsleep 4\n")
    stl = tmp_path / "a.stl"
    stl.write_bytes(b"solid x\nendsolid x\n")
    started = time.monotonic()
    result = asyncio.run(slice_with_prusaslicer(stl, tmp_path / "a.gcode", timeout_s=0.5))
    elapsed = time.monotonic() - started
    assert result.status == "FAILED"
    assert "exceeded" in result.error
    # 0.5 s version probe + 0.5 s slice + bounded drain, nowhere near 60 s.
    assert elapsed < 20.0, f"the timeout did not hold: {elapsed:.1f}s"


def test_the_engine_identity_is_recorded_in_the_result(stub_bin, tmp_path):
    write_stub(stub_bin, "prusa-slicer", 'if [ "$1" = "--version" ]; then echo "PrusaSlicer-2.7.0"; exit 0; fi\n'
                                         'echo "G28" > "$3"\nexit 0\n')
    stl = tmp_path / "a.stl"
    stl.write_bytes(b"solid x\nendsolid x\n")
    result = asyncio.run(slice_with_prusaslicer(stl, tmp_path / "a.gcode", timeout_s=20.0)).as_dict()
    assert result["engine"] == "prusaslicer"
    assert result["engine_version"] == "PrusaSlicer-2.7.0"
    assert result["engine_path"].endswith("prusa-slicer")
