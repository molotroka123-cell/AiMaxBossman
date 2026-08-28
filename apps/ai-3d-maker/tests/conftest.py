from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
SRC = APP_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_3d_maker.cad import primitives  # noqa: E402
from ai_3d_maker.config import Settings  # noqa: E402
from ai_3d_maker.control import ControlPlane  # noqa: E402
from ai_3d_maker.mesh import Mesh, write_stl  # noqa: E402
from ai_3d_maker.profile import PrinterProfile  # noqa: E402

PROFILE_PATH = APP_ROOT / "profiles" / "elegoo_neptune_3_plus.json"
EXAMPLES = APP_ROOT / "examples"


@pytest.fixture
def profile() -> PrinterProfile:
    return PrinterProfile.load(PROFILE_PATH)


@pytest.fixture
def settings(tmp_path) -> Settings:
    s = Settings()
    s.printer_profile = PROFILE_PATH
    s.material_profile = APP_ROOT / "profiles" / "material_defaults.json"
    s.data_dir = tmp_path / "data"
    s.job_timeout_s = 60.0
    s.allow_physical_print = False
    s.printer_transport = "simulator"
    s.ensure_dirs()
    return s


@pytest.fixture
def control(settings) -> ControlPlane:
    return ControlPlane(settings)


@pytest.fixture
def bracket_spec() -> dict:
    import json

    return json.loads((EXAMPLES / "bracket.design.json").read_text(encoding="utf-8"))


@pytest.fixture
def cube_mesh() -> Mesh:
    return primitives.box((20.0, 20.0, 20.0))


@pytest.fixture
def cube_stl(tmp_path, cube_mesh) -> Path:
    path = tmp_path / "cube.stl"
    write_stl(cube_mesh, path)
    return path


# ----------------------------------------------------------------- factories
def make_open_box_mesh() -> Mesh:
    """A cube with its top face removed: watertight-looking file, open surface."""
    mesh = primitives.box((10.0, 10.0, 10.0))
    faces = [f for i, f in enumerate(mesh.faces) if i not in (2, 3)]  # drop +Z pair
    return Mesh(list(mesh.vertices), faces)


def make_degenerate_mesh() -> Mesh:
    """A valid cube plus two zero-area triangles."""
    mesh = primitives.box((10.0, 10.0, 10.0))
    verts = list(mesh.vertices)
    faces = list(mesh.faces)
    faces.append((0, 1, 1))            # repeated index
    collinear = len(verts)
    verts.extend([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)])
    faces.append((collinear, collinear + 1, collinear + 2))  # zero area
    return Mesh(verts, faces)


def make_two_component_mesh() -> Mesh:
    a = primitives.box((5.0, 5.0, 5.0))
    b = primitives.box((5.0, 5.0, 5.0)).translated((20.0, 0.0, 0.0))
    offset = len(a.vertices)
    return Mesh(
        a.vertices + b.vertices,
        list(a.faces) + [(x + offset, y + offset, z + offset) for x, y, z in b.faces],
    )


def make_inverted_mesh() -> Mesh:
    mesh = primitives.box((10.0, 10.0, 10.0))
    return Mesh(list(mesh.vertices), [(a, c, b) for a, b, c in mesh.faces])


def write_truncated_stl(path: Path) -> Path:
    """Binary STL whose header claims more triangles than the file contains."""
    payload = bytearray(b"x" * 80)
    payload += struct.pack("<I", 100)
    payload += b"\0" * 50  # only one triangle worth of data
    path.write_bytes(bytes(payload))
    return path


def write_garbage_stl(path: Path) -> Path:
    path.write_bytes(b"this is definitely not an STL file, not even close" * 3)
    return path


def write_nan_stl(path: Path) -> Path:
    payload = bytearray(b"n" * 80)
    payload += struct.pack("<I", 1)
    payload += struct.pack("<12fH", *([float("nan")] * 12), 0)
    path.write_bytes(bytes(payload))
    return path


SAFE_GCODE = """
; generated for test
G21
G90
M82
M140 S60
M104 S205
G28
M190 S60
M109 S205
G92 E0
G1 Z0.2 F600
G1 X20 Y20 F3000
G1 X40 Y20 E1.2 F1200
G1 X40 Y40 E2.4
G1 X20 Y40 E3.6
M107
M104 S0
M140 S0
M84
"""
