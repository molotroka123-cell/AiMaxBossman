"""OrcaSlicer adapter.

The real-slice test runs the actual orca-slicer binary on a 20 mm cube with
bundled ELEGOO Neptune 3 Plus presets and is skipped where Orca is absent.
Everything else runs without Orca: input validation, preset flattening against
a synthetic profiles tree, and G-code comment parsing.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ai_3d_maker import main as cli
from ai_3d_maker import orca
from ai_3d_maker.errors import (
    CapabilityUnavailableError,
    MeshLoadError,
    ProfileNotFoundError,
    SlicerFailedError,
)

ORCA = orca.locate_orca()
requires_orca = pytest.mark.skipif(ORCA is None, reason="orca-slicer binary not installed on this host")

PRINTER = "Elegoo Neptune 3 Plus 0.4 nozzle"
PROCESS = "0.20mm Standard @Elegoo N3Plus 0.4 nozzle"
FILAMENT = "Elegoo PLA @EN3 Series"


# ------------------------------------------------------------ real slicing
@requires_orca
def test_real_orca_slices_a_20mm_cube_and_the_scan_verdict_is_attached(cube_stl, tmp_path):
    out = tmp_path / "out"
    result = orca.slice(cube_stl, PRINTER, PROCESS, FILAMENT, out, timeout_s=180)

    gcode = Path(result["gcode_path"])
    assert gcode.is_file() and gcode.stat().st_size > 10_000
    assert result["sha256"] == hashlib.sha256(gcode.read_bytes()).hexdigest()
    assert result["generator"].startswith("OrcaSlicer")
    assert result["layers"] == 100  # 20 mm at 0.20 mm layers
    assert result["max_z_height"] == pytest.approx(20.0)
    assert result["estimated_time_s"] and result["estimated_time_s"] > 0
    assert result["filament_used"]["g"] and result["filament_used"]["g"] > 0
    scan = result["gcode_safety"]
    assert scan["status"] in {"PASS", "WARN", "FAILED"}
    assert scan["status"] != "FAILED", scan["issues"]
    assert scan["max_nozzle_target_c"] > 0
    # The run is traceable: flattened presets and Orca's own output are kept.
    run_dir = Path(result["orca"]["run_dir"])
    assert (run_dir / "profiles" / "machine.json").is_file()
    assert json.loads((run_dir / "result.json").read_text(encoding="utf-8"))["sha256"] == result["sha256"]


@requires_orca
def test_real_orca_missing_profile_is_a_clear_error(cube_stl, tmp_path):
    with pytest.raises(ProfileNotFoundError) as exc:
        orca.slice(cube_stl, PRINTER, "0.20mm No Such Process", FILAMENT, tmp_path / "out")
    assert "No Such Process" in exc.value.message
    assert not (tmp_path / "out").exists(), "nothing may be written before inputs are valid"


@requires_orca
def test_real_orca_incompatible_process_is_a_failure_not_a_pass(cube_stl, tmp_path):
    """Negative control: Orca refuses an N3Pro process on an N3Plus printer (exit -17)."""
    with pytest.raises(SlicerFailedError) as exc:
        orca.slice(cube_stl, PRINTER, "0.20mm Standard @Elegoo N3Pro 0.4 nozzle", FILAMENT,
                   tmp_path / "out", timeout_s=180)
    assert exc.value.detail["returncode"] == -17  # signed, not 4294967279
    assert "not compatible" in exc.value.message
    assert any("compatible" in w for w in exc.value.detail["warnings"])
    # Orca's own error text, written to its cwd (the run dir, not the caller's cwd).
    assert any("not compatible" in t for t in exc.value.detail["orca_error_log"].values())
    assert not list((tmp_path / "out").glob("*.gcode"))


@requires_orca
def test_real_orca_bundled_printer_list_contains_the_neptune(tmp_path):
    names = {p["name"] for p in orca.list_printer_profiles(vendor="Elegoo")}
    assert PRINTER in names
    processes = {p["name"] for p in orca.list_profiles("process", printer=PRINTER)}
    assert PROCESS in processes


# ------------------------------------------------------- input validation
def test_missing_stl_is_refused_before_orca_is_looked_up(tmp_path):
    with pytest.raises(MeshLoadError) as exc:
        orca.slice(tmp_path / "nope.stl", PRINTER, PROCESS, FILAMENT, tmp_path / "out",
                   orca=tmp_path / "no-orca.exe")
    assert "not found" in exc.value.message


def test_a_text_file_named_stl_is_refused(tmp_path):
    fake = tmp_path / "model.stl"
    fake.write_text("this is not a mesh\n" * 20, encoding="utf-8")
    with pytest.raises(MeshLoadError) as exc:
        orca.check_stl(fake)
    assert "not a valid binary STL" in exc.value.message or "not an STL" in exc.value.message


def test_a_non_stl_extension_is_refused(tmp_path):
    other = tmp_path / "model.obj"
    other.write_bytes(b"\0" * 200)
    with pytest.raises(MeshLoadError):
        orca.check_stl(other)


def test_the_size_limit_holds(cube_stl):
    with pytest.raises(MeshLoadError) as exc:
        orca.check_stl(cube_stl, max_bytes=100)
    assert "limit" in exc.value.message


def test_a_truncated_ascii_stl_is_refused(tmp_path):
    p = tmp_path / "a.stl"
    p.write_text("solid x\n facet normal 0 0 1\n outer loop\n vertex 0 0 0\n", encoding="utf-8")
    with pytest.raises(MeshLoadError):
        orca.check_stl(p)


def test_valid_binary_and_ascii_stl_pass_the_header_check(cube_stl, tmp_path):
    assert orca.check_stl(cube_stl)["format"] == "binary"
    p = tmp_path / "a.stl"
    p.write_text(
        "solid x\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\n"
        "endloop\nendfacet\nendsolid x\n", encoding="utf-8")
    assert orca.check_stl(p)["format"] == "ascii"


def test_missing_orca_is_capability_unavailable(cube_stl, tmp_path):
    with pytest.raises(CapabilityUnavailableError):
        orca.slice(cube_stl, PRINTER, PROCESS, FILAMENT, tmp_path / "out", orca=tmp_path / "no-orca.exe")


def test_cli_reports_errors_as_json_with_nonzero_exit(tmp_path, capsys):
    code = orca.main(["slice", str(tmp_path / "nope.stl"), "--printer", "p", "--process", "q",
                      "--filament", "f", "--out", str(tmp_path / "o")])
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "MESH_LOAD_FAILED"


def test_main_cli_exposes_orca_without_a_print_verb():
    parser = cli.build_parser()
    verbs = set()
    for action in parser._actions:
        if getattr(action, "choices", None):
            verbs.update(action.choices)
    assert "orca" in verbs
    orca_verbs = set()
    for action in orca.build_parser()._actions:
        if getattr(action, "choices", None):
            orca_verbs.update(action.choices)
    assert orca_verbs == {"locate", "profiles", "slice", "open"}


# --------------------------------------------- preset flattening (no Orca)
@pytest.fixture
def fake_orca(tmp_path) -> Path:
    """A synthetic Orca install: an exe placeholder plus a two-vendor profiles tree."""
    root = tmp_path / "orca"
    exe = root / "orca-slicer.exe"
    profiles = root / "resources" / "profiles"

    def put(vendor, kind, data):
        d = profiles / vendor / kind
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{data['name']}.json").write_text(json.dumps(data), encoding="utf-8")

    put("Acme", "machine", {"type": "machine", "name": "base_m", "printer_settings_id": "Acme",
                            "layer_gcode": "G92 E0", "bed_shape": "a"})
    put("Acme", "machine", {"type": "machine", "name": "Acme One 0.4 nozzle", "inherits": "base_m",
                            "instantiation": "true", "bed_shape": "b", "printer_model": "Acme One"})
    put("Acme", "process", {"type": "process", "name": "0.2 @Acme", "instantiation": "true",
                            "compatible_printers": ["Acme One 0.4 nozzle"]})
    put("OrcaFilamentLibrary", "filament", {"type": "filament", "name": "lib_pla", "density": "1.24"})
    put("Acme", "filament", {"type": "filament", "name": "Acme PLA", "inherits": "lib_pla",
                             "instantiation": "true"})
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"")
    orca._vendor_index.cache_clear()
    return exe


def test_flattening_merges_the_inheritance_chain_leaf_wins(fake_orca):
    flat, prov = orca.resolve_profile("machine", "Acme One 0.4 nozzle", orca=fake_orca)
    assert flat["bed_shape"] == "b"            # leaf overrides parent
    assert flat["layer_gcode"] == "G92 E0"     # parent value inherited
    assert "inherits" not in flat
    assert flat["from"] == "system"
    assert flat["printer_settings_id"] == "Acme One 0.4 nozzle"  # not the parent's "Acme"
    assert prov["vendor"] == "Acme" and len(prov["chain"]) == 2


def test_filament_parents_fall_back_to_the_shared_library(fake_orca):
    flat, prov = orca.resolve_profile("filament", "Acme PLA", vendor="Acme", orca=fake_orca)
    assert flat["density"] == "1.24"
    assert flat["filament_settings_id"] == ["Acme PLA"]
    assert "OrcaFilamentLibrary" in prov["chain"][-1]


def test_missing_profile_and_missing_parent_are_clear_errors(fake_orca):
    with pytest.raises(ProfileNotFoundError) as exc:
        orca.resolve_profile("process", "0.2 @Nobody", orca=fake_orca)
    assert "not found" in exc.value.message
    broken = fake_orca.parent / "resources" / "profiles" / "Acme" / "process" / "broken.json"
    broken.write_text(json.dumps({"type": "process", "name": "broken", "inherits": "ghost"}), encoding="utf-8")
    orca._vendor_index.cache_clear()
    with pytest.raises(ProfileNotFoundError) as exc:
        orca.resolve_profile("process", "broken", orca=fake_orca)
    assert "ghost" in exc.value.message


def test_a_preset_of_the_wrong_kind_is_refused(fake_orca):
    path = fake_orca.parent / "resources" / "profiles" / "Acme" / "process" / "0.2 @Acme.json"
    with pytest.raises(ProfileNotFoundError):
        orca.resolve_profile("machine", str(path), orca=fake_orca)


def test_process_listing_is_filtered_by_printer(fake_orca):
    rows = orca.list_profiles("process", printer="Acme One 0.4 nozzle", orca=fake_orca)
    assert [r["name"] for r in rows] == ["0.2 @Acme"]
    assert orca.list_profiles("process", printer="Acme One 0.4 nozzle", vendor="Acme", orca=fake_orca)


# ---------------------------------------------------------- G-code parsing
def test_gcode_summary_parses_orca_comments():
    text = "\n".join([
        "; HEADER_BLOCK_START",
        "; generated by OrcaSlicer 2.4.2 on 2026-10-06 at 12:32:49",
        "; total layer number: 100",
        "; max_z_height: 20.00",
        "G28",
        "; filament used [mm] = 1174.80",
        "; filament used [cm3] = 2.83",
        "; filament used [g] = 3.53",
        "; estimated printing time (normal mode) = 1h 19m 52s",
        "; printer_settings_id = Elegoo Neptune 3 Plus 0.4 nozzle",
    ])
    s = orca.parse_gcode_summary(text)
    assert s["generator"] == "OrcaSlicer 2.4.2"
    assert s["layers"] == 100
    assert s["estimated_time"] == "1h 19m 52s" and s["estimated_time_s"] == 4792
    assert s["filament_used"] == {"mm": 1174.8, "cm3": 2.83, "g": 3.53}
    assert s["printer_settings_id"] == "Elegoo Neptune 3 Plus 0.4 nozzle"


def test_gcode_summary_leaves_unknowns_as_none():
    s = orca.parse_gcode_summary("G28\nG1 X1\n")
    assert s["layers"] is None and s["estimated_time_s"] is None and s["filament_used"]["g"] is None
