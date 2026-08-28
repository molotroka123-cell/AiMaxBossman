"""The control contract BOSSMAN drives, and the independence rules around it."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from ai_3d_maker.control import CONTRACT_VERSION, OPERATIONS, ControlPlane
from ai_3d_maker.errors import InvalidSpecError, JobNotFoundError, UnsafePathError

APP_ROOT = Path(__file__).resolve().parents[1]
SRC = APP_ROOT / "src"


def simple_payload(job_id: str) -> dict:
    return {
        "kind": "design",
        "job_id": job_id,
        "spec": {
            "name": "plate",
            "features": [{"primitive": {"id": "b", "kind": "box", "size_mm": [40, 30, 8]}, "operation": "add"}],
        },
    }


# ---------------------------------------------------------------- contract
def test_all_required_operations_exist(control):
    for name in ("health", "capabilities", "jobs.create", "jobs.status", "jobs.cancel",
                 "artifacts.list", "metrics"):
        assert name in OPERATIONS
    assert callable(control.health)
    assert callable(control.capabilities)
    assert callable(control.jobs_create)
    assert callable(control.jobs_status)
    assert callable(control.jobs_cancel)
    assert callable(control.artifacts_list)
    assert callable(control.metrics)


def test_health_reports_the_profile_and_the_physical_switch(control):
    health = control.health()
    assert health["status"] == "ok"
    assert health["contract"] == CONTRACT_VERSION
    assert health["printer_profile"]["model"] == "Neptune 3 Plus"
    assert health["physical_printing_enabled"] is False


def test_capabilities_are_honest_about_what_is_missing(control):
    caps = control.capabilities()
    by_name = {c["name"]: c for c in caps["capabilities"]}
    assert by_name["mesh-core"]["available"] is True
    assert by_name["physical-printer"]["available"] is False
    assert "BLOCKED BY HARDWARE" in by_name["physical-printer"]["reason"]
    for cap in caps["capabilities"]:
        if not cap["available"]:
            assert cap["reason"], f"{cap['name']} is unavailable without saying why"
    assert caps["features"]["physical_print"] is False
    assert caps["physical_actions"]["requires_human_confirmation"] is True


def test_capabilities_keep_the_two_profile_sections_separate(control):
    profile = control.capabilities()["printer_profile"]
    assert profile["verified_machine_limits"]["build_x_mm"] == 320.0
    assert "nominal_layer_height_mm" in profile["process_defaults_unverified"]


def test_metrics_report_resources_and_limits(control):
    metrics = control.metrics()
    assert metrics["process"]["pid"] > 0
    assert metrics["jobs"]["disk_quota_bytes"] > 0
    assert metrics["limits"]["job_timeout_s"] > 0
    assert "max_rss_kb" in metrics["process"]


# -------------------------------------------------------------------- jobs
def test_job_lifecycle_through_the_contract(control):
    created = asyncio.run(control.jobs_create(simple_payload("lifecycle")))
    assert created["accepted"] is True
    status = control.jobs_status("lifecycle")
    assert status["status"] == "succeeded"
    assert status["terminal"] is True
    listing = control.jobs_list()
    assert any(j["id"] == "lifecycle" for j in listing["jobs"])


def test_jobs_create_generates_an_id_when_none_is_given(control):
    payload = simple_payload("ignored")
    payload.pop("job_id")
    result = asyncio.run(control.jobs_create(payload))
    assert result["job_id"].startswith("job-")


def test_jobs_create_sanitises_a_hostile_id(control):
    payload = simple_payload("x")
    payload["job_id"] = "../../etc/passwd"
    with pytest.raises(UnsafePathError):
        asyncio.run(control.jobs_create(payload))


def test_background_job_can_be_awaited(control):
    async def scenario():
        payload = simple_payload("background")
        payload["wait"] = False
        accepted = await control.jobs_create(payload)
        assert accepted["waited"] is False
        for _ in range(200):
            if control.jobs_status("background")["status"] in {"succeeded", "failed"}:
                break
            await asyncio.sleep(0.02)
        return control.jobs_status("background")

    assert asyncio.run(scenario())["status"] == "succeeded"


def test_cancel_of_an_unknown_job_raises(control):
    with pytest.raises(JobNotFoundError):
        control.jobs_cancel("does-not-exist")


def test_cancel_marks_the_job(control):
    control.store.create("tocancel", "design", {})
    result = control.jobs_cancel("tocancel")
    assert result["cancel_requested"] is True


# --------------------------------------------------------------- artifacts
def test_artifacts_list_reports_checksums(control):
    asyncio.run(control.jobs_create(simple_payload("arts")))
    listing = control.artifacts_list("arts")
    assert listing["count"] > 0
    names = {a["path"] for a in listing["artifacts"]}
    assert "model.stl" in names
    for entry in listing["artifacts"]:
        assert len(entry["sha256"]) == 64
        assert entry["bytes"] > 0
        assert entry["kind"]


def test_artifact_path_refuses_traversal(control):
    asyncio.run(control.jobs_create(simple_payload("trav")))
    with pytest.raises(UnsafePathError):
        control.artifact_path("trav", "../../../etc/passwd")


def test_artifact_path_returns_a_real_file(control):
    asyncio.run(control.jobs_create(simple_payload("real")))
    path = control.artifact_path("real", "model.stl")
    assert path.is_file()


# ---------------------------------------------------------------- physical
def test_printer_confirm_without_a_token_is_refused(control):
    asyncio.run(control.jobs_create(simple_payload("confirm")))
    result = control.printer_confirm({"job_id": "confirm", "action": "start_print"})
    assert result["error"] == "PHYSICAL_CONFIRMATION_REQUIRED"
    assert "expected_confirmation" in result["detail"]


def test_printer_confirm_with_the_token_stays_in_the_simulator(control):
    asyncio.run(control.jobs_create(simple_payload("confirm2")))
    token = control.confirmation_for("confirm2")["confirmation"]
    result = control.printer_confirm({
        "job_id": "confirm2", "action": "start_print", "confirmation": token,
    })
    assert result["status"] == "SIMULATED"
    assert result["performed_physical_action"] is False


def test_confirmation_changes_when_the_artifact_changes(control):
    asyncio.run(control.jobs_create(simple_payload("c3")))
    first = control.confirmation_for("c3")["confirmation"]
    path = control.artifact_path("c3", "model.stl")
    path.write_bytes(path.read_bytes() + b"tampered")
    assert control.confirmation_for("c3")["confirmation"] != first


def test_gcode_scan_is_exposed_on_the_contract(control):
    assert control.gcode_scan("M104 S400")["status"] == "FAILED"


# ------------------------------------------------------------ independence
def test_the_app_never_imports_bossman():
    forbidden = ("bcc", "bossman", "command_center")
    offenders = []
    for path in (SRC / "ai_3d_maker").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                for name in forbidden:
                    if f" {name}" in f" {stripped} " or stripped.startswith(f"from {name}"):
                        offenders.append(f"{path.name}: {stripped}")
    assert offenders == []


def test_the_package_imports_without_optional_dependencies():
    """Core must work on a bare interpreter: no fastapi, numpy, trimesh needed."""
    code = (
        "import sys\n"
        "for blocked in ('fastapi','uvicorn','trimesh','numpy','manifold3d','cadquery','pydantic'):\n"
        "    sys.modules[blocked] = None\n"
        "import ai_3d_maker, ai_3d_maker.mesh, ai_3d_maker.meshcheck, ai_3d_maker.repair\n"
        "import ai_3d_maker.gcode, ai_3d_maker.printability, ai_3d_maker.printer, ai_3d_maker.paths\n"
        "from ai_3d_maker.cad import primitives\n"
        "from ai_3d_maker.meshcheck import inspect_mesh\n"
        "assert inspect_mesh(primitives.box((10,10,10))).is_watertight\n"
        "print('OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, cwd=str(SRC), timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


def test_cli_capabilities_runs_as_a_subprocess(tmp_path):
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "PYTHONPATH": str(SRC),
        "AI3D_DATA_DIR": str(tmp_path / "data"),
        "AI3D_PRINTER_PROFILE": str(APP_ROOT / "profiles" / "elegoo_neptune_3_plus.json"),
    }
    proc = subprocess.run(
        [sys.executable, "-m", "ai_3d_maker.main", "capabilities"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["app"] == "ai-3d-maker"
    assert payload["features"]["physical_print"] is False


def _cli(tmp_path, *args, data_dir: str | None = None) -> subprocess.CompletedProcess:
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "PYTHONPATH": str(SRC),
        "AI3D_DATA_DIR": data_dir or str(tmp_path / "data"),
        "AI3D_PRINTER_PROFILE": str(APP_ROOT / "profiles" / "elegoo_neptune_3_plus.json"),
    }
    return subprocess.run(
        [sys.executable, "-m", "ai_3d_maker.main", *args],
        capture_output=True, text=True, env=env, timeout=300,
    )


def test_the_same_spec_is_deterministic_across_separate_processes(tmp_path):
    """Determinism has to survive a fresh interpreter, not just a warm one."""
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(simple_payload("x")["spec"]), encoding="utf-8")
    digests = []
    for run in ("a", "b"):
        proc = _cli(tmp_path, "build", str(spec_path), "--job-id", "det", data_dir=str(tmp_path / run))
        assert proc.returncode == 0, proc.stderr
        digests.append(json.loads(proc.stdout)["result"]["detail"]["export"]["sha256"])
    assert digests[0] == digests[1]
    assert (tmp_path / "a" / "jobs" / "det" / "model.stl").read_bytes() == \
           (tmp_path / "b" / "jobs" / "det" / "model.stl").read_bytes()


def test_cli_validate_rejects_a_corrupt_stl(tmp_path):
    bad = tmp_path / "bad.stl"
    bad.write_bytes(b"definitely not an stl")
    proc = _cli(tmp_path, "validate", str(bad))
    assert proc.returncode == 1
    assert json.loads(proc.stdout)["error"] == "MESH_LOAD_FAILED"


def test_cli_has_no_verb_that_starts_a_physical_print(tmp_path):
    proc = _cli(tmp_path, "--help")
    assert proc.returncode == 0
    for forbidden in ("print ", "start-print", "preheat", "home"):
        assert forbidden not in proc.stdout


def test_control_plane_construction_is_cheap_and_repeatable(settings):
    a = ControlPlane(settings)
    b = ControlPlane(settings)
    assert a.profile.id == b.profile.id


def test_the_control_plane_scans_machine_instructions_by_content_not_extension(control):
    """A print program dropped into a job under a harmless name is still scanned."""
    asyncio.run(control.jobs_create(simple_payload("sniff")))
    job_dir = Path(control.store.get("sniff").directory)
    (job_dir / "notes.txt").write_text(
        "G90\nM82\nG28\nM104 S400\nG1 X10 Y10 Z0.2 E1\n", encoding="utf-8"
    )
    token = control.confirmation_for("sniff", "notes.txt")["confirmation"]
    result = control.printer_confirm({
        "job_id": "sniff", "artifact": "notes.txt",
        "action": "transfer_to_media", "confirmation": token,
    })
    assert result["error"] == "UNSAFE_GCODE"


def test_the_control_plane_reports_whether_the_artifact_was_scanned(control):
    asyncio.run(control.jobs_create(simple_payload("scanned")))
    token = control.confirmation_for("scanned")["confirmation"]
    result = control.printer_confirm({
        "job_id": "scanned", "action": "transfer_to_media", "confirmation": token,
    })
    assert result["gcode_scan"] is None
    assert result["artifact_is_machine_instructions"] is False


def test_slicer_settings_are_bounded_at_intake_not_at_the_subprocess(control):
    """No slicer is installed here, so a bad setting must be caught before that."""
    payload = simple_payload("badsettings")
    payload["slice"] = True
    payload["slicer_settings"] = {"--infill-overlap": 30}
    with pytest.raises(InvalidSpecError):
        asyncio.run(control.jobs_create(payload))


def test_ordinary_slicer_settings_pass_intake(control):
    payload = simple_payload("goodsettings")
    payload["slice"] = True
    payload["slicer_settings"] = {"layer_height": 0.2, "wall_line_count": 3}
    result = asyncio.run(control.jobs_create(payload))["result"]
    assert result["evidence"]["slicer"]["status"] == "NOT_RUN"
