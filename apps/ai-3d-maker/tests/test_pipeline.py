"""End-to-end pipeline behaviour, on the real code path."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ai_3d_maker.cad import csg, primitives
from ai_3d_maker.mesh import sha256_file, write_stl
from ai_3d_maker.pipeline import JobRequest, Pipeline
from ai_3d_maker.spec import DesignSpec
from conftest import make_open_box_mesh, write_garbage_stl

requires_csg = pytest.mark.skipif(not csg.is_available(), reason="no CSG backend installed")


@pytest.fixture
def pipeline(control) -> Pipeline:
    return control.pipeline


def simple_spec(size=(40.0, 30.0, 8.0)) -> DesignSpec:
    return DesignSpec.model_validate({
        "name": "plate",
        "features": [{"primitive": {"id": "b", "kind": "box", "size_mm": list(size)}, "operation": "add"}],
    })


def run(control, payload) -> dict:
    return asyncio.run(control.jobs_create(payload))["result"]


# ------------------------------------------------------------- happy paths
def test_single_primitive_job_produces_a_printable_artifact(control):
    result = run(control, {"kind": "design", "spec": json.loads(simple_spec().canonical_json()), "job_id": "plate"})
    assert result["printable"] is True
    assert result["status"] == "PRINTABLE"
    job_dir = Path(control.store.get("plate").directory)
    assert (job_dir / "model.stl").is_file()
    assert (job_dir / "model.scad").is_file()
    assert (job_dir / "design_spec.json").is_file()
    assert (job_dir / "validation.json").is_file()
    assert (job_dir / "print_report.md").is_file()
    assert (job_dir / "manifest.json").is_file()


@requires_csg
def test_bracket_example_runs_end_to_end(control, bracket_spec):
    result = run(control, {"kind": "design", "spec": bracket_spec, "job_id": "bracket"})
    assert result["printable"] is True
    assert result["mesh"]["is_watertight"]
    assert result["mesh"]["components"] == 1
    assert result["fit"]["fits"]
    assert result["stages"]["printability"] == "ok"


def test_all_stages_are_recorded_in_order(control):
    run(control, {"kind": "design", "spec": json.loads(simple_spec().canonical_json()), "job_id": "stages"})
    names = [s["name"] for s in control.store.get("stages").stages]
    assert names[:6] == [
        "intake", "generate", "inspect_raw", "repair", "transform", "inspect_final",
    ]
    assert "printability" in names
    assert "export" in names
    assert "printer_prepare" in names


def test_the_report_says_no_physical_action_happened(control):
    run(control, {"kind": "design", "spec": json.loads(simple_spec().canonical_json()), "job_id": "report"})
    text = (Path(control.store.get("report").directory) / "print_report.md").read_text(encoding="utf-8")
    assert "No heater, motor or media was touched" in text
    assert "PRINT-CONFIRM-" in text


def test_result_marks_physical_print_as_blocked_by_hardware(control):
    result = run(control, {"kind": "design", "spec": json.loads(simple_spec().canonical_json()), "job_id": "blocked"})
    assert result["physical_print"] == {
        "performed": False,
        "requires_explicit_human_confirmation": True,
        "status": "BLOCKED_BY_HARDWARE",
    }


# ---------------------------------------------------------------- refusals
def test_oversize_design_is_refused_and_not_exported_as_model_stl(control):
    spec = json.loads(simple_spec((400.0, 400.0, 500.0)).canonical_json())
    result = run(control, {"kind": "design", "spec": spec, "job_id": "oversize"})
    assert result["printable"] is False
    job_dir = Path(control.store.get("oversize").directory)
    assert not (job_dir / "model.stl").exists()
    assert (job_dir / "model.rejected.stl").is_file()
    assert any("does not fit" in r for r in result["reasons"])


def test_unresolved_questions_block_before_any_cad_runs(control):
    spec = json.loads(simple_spec().canonical_json())
    spec["unresolved_questions"] = ["Clearance or tapped hole?"]
    result = run(control, {"kind": "design", "spec": spec, "job_id": "gated"})
    assert result["printable"] is False
    assert result["status"] == "NEEDS_CLARIFICATION_OR_PROCESS_CHANGE"
    job_dir = Path(control.store.get("gated").directory)
    assert not (job_dir / "model.stl").exists()
    assert not (job_dir / "model.scad").exists()


def test_tolerance_tighter_than_calibration_is_blocked(control):
    spec = json.loads(simple_spec().canonical_json())
    spec["manufacturing"]["required_tolerance_mm"] = 0.02
    result = run(control, {
        "kind": "design", "spec": spec, "job_id": "tol", "calibrated_tolerance_mm": 0.2,
    })
    assert result["printable"] is False
    assert result["status"] == "NEEDS_CALIBRATION_OR_DIFFERENT_PROCESS"


def test_uncalibrated_tolerance_warns_but_proceeds(control):
    spec = json.loads(simple_spec().canonical_json())
    spec["manufacturing"]["required_tolerance_mm"] = 0.3
    result = run(control, {"kind": "design", "spec": spec, "job_id": "warn"})
    assert result["printable"] is True
    assert any("no measured calibration profile" in w for w in result["warnings"])


# ------------------------------------------------------------------ import
def test_stl_import_of_a_clean_mesh_is_printable(control, cube_stl):
    result = run(control, {"kind": "import", "source_stl": str(cube_stl), "job_id": "imp"})
    assert result["printable"] is True
    assert result["mesh"]["is_watertight"]


def test_stl_import_of_an_open_mesh_is_honestly_refused(control, tmp_path):
    path = write_stl(make_open_box_mesh(), tmp_path / "open.stl")
    result = run(control, {"kind": "import", "source_stl": str(path), "job_id": "open"})
    assert result["printable"] is False
    assert any("watertight" in r for r in result["reasons"])


def test_corrupt_stl_import_fails_cleanly(control, tmp_path):
    path = write_garbage_stl(tmp_path / "bad.stl")
    result = run(control, {"kind": "import", "source_stl": str(path), "job_id": "corrupt"})
    assert result["status"] == "failed"
    assert result["error"] == "MESH_LOAD_FAILED"
    assert control.store.get("corrupt").status == "failed"


def test_missing_source_file_fails_cleanly(control):
    result = run(control, {"kind": "import", "source_stl": "/nonexistent/none.stl", "job_id": "missing"})
    assert result["error"] == "MESH_LOAD_FAILED"


def test_import_copies_the_source_under_a_sanitised_name(control, tmp_path):
    weird = tmp_path / "..weird name!!.stl"
    write_stl(primitives.box((5.0, 5.0, 5.0)), weird)
    run(control, {"kind": "import", "source_stl": str(weird), "job_id": "sanitised"})
    staged = list((Path(control.store.get("sanitised").directory) / "source").iterdir())
    assert len(staged) == 1
    assert staged[0].name == "weirdname.stl"


def test_inch_units_are_converted_on_import(control, tmp_path):
    path = write_stl(primitives.box((1.0, 1.0, 1.0)), tmp_path / "inch.stl")
    result = run(control, {
        "kind": "import", "source_stl": str(path), "source_units": "in", "job_id": "inch",
    })
    assert result["mesh"]["extents_mm"] == pytest.approx([25.4, 25.4, 25.4], rel=1e-4)


def test_scale_to_fit_rescues_an_oversize_import(control, tmp_path):
    path = write_stl(primitives.box((600.0, 600.0, 700.0)), tmp_path / "big.stl")
    result = run(control, {
        "kind": "import", "source_stl": str(path), "scale_to_fit": True, "job_id": "shrink",
    })
    assert result["printable"] is True
    assert any("scaled by" in w for w in result["warnings"])


# ------------------------------------------------------------- determinism
def test_the_same_spec_produces_the_same_stl_checksum(control):
    spec = json.loads(simple_spec().canonical_json())
    run(control, {"kind": "design", "spec": spec, "job_id": "det1"})
    run(control, {"kind": "design", "spec": spec, "job_id": "det2"})
    a = Path(control.store.get("det1").directory) / "model.stl"
    b = Path(control.store.get("det2").directory) / "model.stl"
    assert sha256_file(a) == sha256_file(b)


@requires_csg
def test_the_bracket_is_deterministic_across_jobs(control, bracket_spec):
    run(control, {"kind": "design", "spec": bracket_spec, "job_id": "bd1"})
    run(control, {"kind": "design", "spec": bracket_spec, "job_id": "bd2"})
    a = Path(control.store.get("bd1").directory) / "model.stl"
    b = Path(control.store.get("bd2").directory) / "model.stl"
    assert sha256_file(a) == sha256_file(b)


def test_manifest_checksums_match_the_files_on_disk(control):
    run(control, {"kind": "design", "spec": json.loads(simple_spec().canonical_json()), "job_id": "sums"})
    job_dir = Path(control.store.get("sums").directory)
    manifest = json.loads((job_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["file_count"] > 0
    for entry in manifest["files"]:
        assert sha256_file(job_dir / entry["path"]) == entry["sha256"]


# --------------------------------------------------------------- slicing
def test_slicing_is_reported_as_unavailable_not_faked(control):
    spec = json.loads(simple_spec().canonical_json())
    result = run(control, {"kind": "design", "spec": spec, "job_id": "slice", "slice": True})
    stage = result["stages"].get("slice")
    assert stage in {"not_available", "ok"}
    if stage == "not_available":
        assert not (Path(control.store.get("slice").directory) / "model.gcode").exists()
        assert any("slicing unavailable" in w for w in result["warnings"])


# ------------------------------------------------------ cancel and timeout
def test_cancelling_before_the_run_aborts_it(control):
    spec = json.loads(simple_spec().canonical_json())
    control.store.create("cancelme", "design", {})
    control.store.request_cancel("cancelme")
    request = JobRequest(kind="design", spec=DesignSpec.model_validate(spec))
    result = asyncio.run(control.pipeline.run("cancelme", request))
    assert result["status"] == "cancelled"
    assert result["error"] == "JOB_CANCELLED"


def test_a_zero_timeout_produces_an_honest_timeout(control):
    control.settings.job_timeout_s = 0.000001
    spec = json.loads(simple_spec().canonical_json())
    control.store.create("slow", "design", {})
    request = JobRequest(kind="design", spec=DesignSpec.model_validate(spec))
    result = asyncio.run(control.pipeline.run("slow", request))
    assert result["status"] == "timed_out"
    assert control.store.get("slow").status == "timed_out"


def test_job_disk_quota_stops_a_runaway_job(control):
    control.store.job_quota_bytes = 10
    spec = json.loads(simple_spec().canonical_json())
    result = run(control, {"kind": "design", "spec": spec, "job_id": "quota"})
    assert result["status"] == "failed"
    assert result["error"] == "DISK_QUOTA_EXCEEDED"


def test_oversized_source_file_is_refused(control, tmp_path):
    control.settings.max_upload_bytes = 100
    path = write_stl(primitives.box((10.0, 10.0, 10.0)), tmp_path / "big.stl")
    result = run(control, {"kind": "import", "source_stl": str(path), "job_id": "toobig"})
    assert result["error"] == "DISK_QUOTA_EXCEEDED"


# ---------------------------------------------------------------- evidence
"""A separate verdict per engine.

An STL on disk is not evidence that CAD ran, that the mesh was validated, that
a slicer ran or that anything was printed. The result has to say which of those
actually happened on this host, one label each, and must never collapse them
into a single "it worked".
"""

EVIDENCE_KEYS = {
    "spec_compiled",
    "cad_engine",
    "step_export",
    "openscad_render",
    "mesh_validation",
    "mesh_cross_check",
    "printability",
    "slicer",
    "gcode_safety",
    "physical_printer",
}
ALLOWED_STATUSES = {"PASS", "FAIL", "NOT_RUN"}


def test_the_result_carries_one_label_per_engine(control):
    result = run(control, {"kind": "design", "spec": json.loads(simple_spec().canonical_json()), "job_id": "ev"})
    evidence = result["evidence"]
    assert set(evidence) == EVIDENCE_KEYS
    for name, entry in evidence.items():
        assert entry["status"] in ALLOWED_STATUSES, name
        if entry["status"] == "NOT_RUN":
            assert entry["reason"], f"{name} claims NOT_RUN without saying why"


def test_a_printable_verdict_does_not_imply_a_slicer_or_a_printer_ran(control):
    result = run(control, {"kind": "design", "spec": json.loads(simple_spec().canonical_json()), "job_id": "ev2"})
    assert result["printable"] is True
    assert result["evidence"]["slicer"]["status"] == "NOT_RUN"
    assert result["evidence"]["gcode_safety"]["status"] == "NOT_RUN"
    assert result["evidence"]["physical_printer"]["status"] == "NOT_RUN"


def test_mesh_validation_is_evidence_of_a_check_that_really_ran(control):
    result = run(control, {"kind": "design", "spec": json.loads(simple_spec().canonical_json()), "job_id": "ev3"})
    entry = result["evidence"]["mesh_validation"]
    assert entry["status"] == "PASS"
    assert entry["engine"] == "meshcheck"
    # A check that "ran" without looking at any triangle is not a check.
    assert entry["detail"]["triangles"] > 0


def test_a_rejected_model_reports_a_failed_printability_not_a_missing_one(control):
    spec = json.loads(simple_spec((400.0, 400.0, 500.0)).canonical_json())
    result = run(control, {"kind": "design", "spec": spec, "job_id": "ev4"})
    assert result["evidence"]["printability"]["status"] == "FAIL"
    assert result["evidence"]["mesh_validation"]["status"] in {"PASS", "FAIL"}


def test_step_export_is_not_run_when_cadquery_is_absent(control):
    from ai_3d_maker.cad.external import cadquery_info

    result = run(control, {"kind": "design", "spec": json.loads(simple_spec().canonical_json()), "job_id": "ev5"})
    entry = result["evidence"]["step_export"]
    if cadquery_info()["available"]:
        assert entry["status"] == "PASS"
        assert (Path(control.store.get("ev5").directory) / "model.step").is_file()
    else:
        assert entry["status"] == "NOT_RUN"
        assert "cadquery" in entry["reason"].lower()
        assert not (Path(control.store.get("ev5").directory) / "model.step").exists()


def test_the_cross_check_reports_the_engine_that_actually_ran(control):
    result = run(control, {"kind": "design", "spec": json.loads(simple_spec().canonical_json()), "job_id": "ev6"})
    entry = result["evidence"]["mesh_cross_check"]
    try:
        import trimesh  # noqa: F401
    except Exception:
        assert entry["status"] == "NOT_RUN"
    else:
        assert entry["status"] == "PASS"
        assert entry["engine"].startswith("trimesh")
        assert entry["detail"]["watertight"] is True


def test_evidence_survives_into_the_report_and_validation_file(control):
    run(control, {"kind": "design", "spec": json.loads(simple_spec().canonical_json()), "job_id": "ev7"})
    job_dir = Path(control.store.get("ev7").directory)
    saved = json.loads((job_dir / "validation.json").read_text(encoding="utf-8"))
    assert set(saved["evidence"]) == EVIDENCE_KEYS
    text = (job_dir / "print_report.md").read_text(encoding="utf-8")
    assert "REAL PRINTER PASS" not in text
    for name in EVIDENCE_KEYS:
        assert name in text


def test_a_gated_job_reports_cad_as_not_run_rather_than_silently_absent(control):
    spec = json.loads(simple_spec().canonical_json())
    spec["unresolved_questions"] = ["Clearance or tapped hole?"]
    result = run(control, {"kind": "design", "spec": spec, "job_id": "ev8"})
    assert result["printable"] is False
    assert result["evidence"]["cad_engine"]["status"] == "NOT_RUN"
    assert result["evidence"]["mesh_validation"]["status"] == "NOT_RUN"
    assert result["evidence"]["printability"]["status"] == "NOT_RUN"
