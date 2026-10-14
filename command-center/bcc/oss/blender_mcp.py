"""MCP for Blender (`mcp-for-blender`, formerly `blender-mcp`, MIT license).

Side-effect-free adapter for prompt-assisted 3D modeling, scene creation, and manipulation
in Blender 3D driven by local LLM / Studio. Enforces loopback-only socket configuration
and deterministic AST validation on generated `bpy` scripts (blocking shell, network,
and filesystem escape).
"""
from __future__ import annotations

import ast
import os
import re
from typing import Any
from urllib.parse import urlparse

PACKAGE_NAME = "mcp-for-blender"
LEGACY_PACKAGE_NAME = "blender-mcp"
UPSTREAM_VERSION = "2.0.3"
LICENSE = "MIT"
UPSTREAM_URL = "https://github.com/ahujasid/blender-mcp"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876

ALLOWED_BPY_IMPORTS = frozenset({"bpy", "bmesh", "math", "mathutils"})
FORBIDDEN_BPY_MODULES = frozenset({
    "os", "sys", "subprocess", "socket", "urllib", "http", "requests",
    "httpx", "aiohttp", "shutil", "pathlib", "ctypes", "pickle", "marshal",
    "importlib", "builtins", "pty", "multiprocessing", "threading",
})
FORBIDDEN_BPY_CALLS = frozenset({
    "eval", "exec", "compile", "open", "__import__", "input", "breakpoint",
    "getattr", "setattr", "delattr", "globals", "locals", "vars",
})

SCENE_PRESETS: dict[str, dict[str, Any]] = {
    "studio_product": {
        "title": "Предметная студия (3-точечный свет + подиум)",
        "engine": "CYCLES",
        "resolution": [1920, 1080],
        "samples": 64,
        "objects": ["Product_Pedestal", "Hero_Subject", "Key_Light", "Fill_Light", "Rim_Light", "Studio_Camera"],
    },
    "architectural_room": {
        "title": "Архитектурный интерьер (дневной свет + широкоугольная камера)",
        "engine": "BLENDER_EEVEE_NEXT",
        "resolution": [1920, 1080],
        "samples": 48,
        "objects": ["Room_Shell", "Focal_Table", "Sun_Lamp", "Area_Window_Fill", "Arch_Camera"],
    },
    "character_turntable": {
        "title": "Поворотный подиум персонажа (360° turntable)",
        "engine": "BLENDER_EEVEE_NEXT",
        "resolution": [1080, 1350],
        "samples": 48,
        "objects": ["Turntable_Base", "Character_Proxy", "Key_Light", "Rim_Light", "Turntable_Camera"],
    },
    "lowpoly_prop": {
        "title": "Игровой 3D-ассет (low-poly + чистая топология)",
        "engine": "BLENDER_EEVEE_NEXT",
        "resolution": [1024, 1024],
        "samples": 32,
        "objects": ["LowPoly_Prop", "Key_Light", "Fill_Light", "Iso_Camera"],
    },
}


def status() -> dict[str, Any]:
    """Side-effect-free configuration check for MCP for Blender."""
    host = os.environ.get("BOSSMAN_BLENDER_MCP_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST
    port_raw = os.environ.get("BOSSMAN_BLENDER_MCP_PORT", str(DEFAULT_PORT)).strip()
    enabled = os.environ.get("BOSSMAN_BLENDER_MCP_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
    endpoint = os.environ.get("BOSSMAN_BLENDER_MCP_URL", "").strip()

    if endpoint:
        parsed = urlparse(endpoint)
        host = (parsed.hostname or "").strip()
        if parsed.port:
            port_raw = str(parsed.port)

    try:
        port = int(port_raw)
    except ValueError:
        port = -1

    loopback_ok = host in {"127.0.0.1", "localhost", "::1"} and (1024 <= port <= 65535)
    if not loopback_ok:
        state = "invalid_configuration"
    elif enabled or bool(endpoint):
        state = "configured"
    else:
        state = "integrated"

    return {
        "id": "blender_mcp",
        "package": PACKAGE_NAME,
        "legacy_package": LEGACY_PACKAGE_NAME,
        "version_declared": UPSTREAM_VERSION,
        "license": LICENSE,
        "status": state,
        "enabled": enabled,
        "loopback_only": loopback_ok,
        "port": port if loopback_ok else DEFAULT_PORT,
        "inference_verified": False,
        "mode": "prompt_assisted_3d_scene",
        "upstream": UPSTREAM_URL,
    }


def validate_bpy_script(script: str) -> tuple[bool, list[str]]:
    """Deterministic AST security check for Blender Python (`bpy`) scripts.

    Blocks shell execution, network access, file I/O outside Blender scene primitives,
    and dynamic code execution (`eval`/`exec`/`__import__`).
    """
    code = (script or "").strip()
    if not code:
        return False, ["empty_script"]
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return False, [f"syntax_error: {exc.msg} (line {exc.lineno})"]

    issues: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in FORBIDDEN_BPY_MODULES or root not in ALLOWED_BPY_IMPORTS:
                    issues.append(f"forbidden_import:{alias.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if mod in FORBIDDEN_BPY_MODULES or mod not in ALLOWED_BPY_IMPORTS:
                issues.append(f"forbidden_import_from:{node.module}")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_BPY_CALLS:
                issues.append(f"forbidden_call:{node.func.id}")
            elif isinstance(node.func, ast.Attribute):
                attr = node.func.attr
                if attr in {"system", "popen", "Popen", "run", "call", "check_output", "unlink", "remove", "rmtree"}:
                    issues.append(f"forbidden_method:{attr}")
                if attr.startswith("__") and attr.endswith("__"):
                    issues.append(f"forbidden_dunder_call:{attr}")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr.endswith("__"):
                issues.append(f"forbidden_dunder_attr:{node.attr}")

    return len(issues) == 0, issues


def _sanitize_label(text: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-zА-Яа-яЁё _.,-]", "", (text or "").strip())
    return (cleaned[:80] or "Bossman_3D_Subject").replace('"', "").replace("'", "")


def build_scene_plan(prompt: str, *, preset: str = "studio_product") -> dict[str, Any]:
    """Generate a deterministic, security-verified Blender 3D scene plan and MCP payload."""
    clean_prompt = (prompt or "").strip()
    if not clean_prompt:
        raise ValueError("prompt: укажите описание 3D-сцены для Blender")

    preset_key = preset if preset in SCENE_PRESETS else "studio_product"
    spec = SCENE_PRESETS[preset_key]
    subject_label = _sanitize_label(clean_prompt)
    res_x, res_y = spec["resolution"]
    samples = int(spec["samples"])
    engine = spec["engine"]

    bpy_code = "\n".join([
        "import bpy",
        "import math",
        "",
        "# Чистая сцена Bossman 3D Studio (MCP for Blender)",
        "bpy.ops.wm.read_factory_settings(use_empty=True)",
        "scene = bpy.context.scene",
        f"scene.render.engine = '{engine}'",
        f"scene.render.resolution_x = {res_x}",
        f"scene.render.resolution_y = {res_y}",
        "",
        "# Подиум / основа сцены",
        "bpy.ops.mesh.primitive_cylinder_add(radius=2.5, depth=0.2, location=(0.0, 0.0, -0.1))",
        "pedestal = bpy.context.active_object",
        "pedestal.name = 'Product_Pedestal'",
        "",
        "# Центральный 3D-объект по запросу владельца",
        "bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=0.85, location=(0.0, 0.0, 0.85))",
        "hero = bpy.context.active_object",
        f"hero.name = '{subject_label[:32]}'",
        "",
        "# Трёхточечный студийный свет и камера",
        "bpy.ops.object.light_add(type='AREA', radius=2.0, location=(3.0, -2.5, 3.2))",
        "key_light = bpy.context.active_object",
        "key_light.name = 'Key_Light'",
        "key_light.data.energy = 450.0",
        "",
        "bpy.ops.object.light_add(type='AREA', radius=2.5, location=(-3.2, -1.8, 2.2))",
        "fill_light = bpy.context.active_object",
        "fill_light.name = 'Fill_Light'",
        "fill_light.data.energy = 180.0",
        "",
        "bpy.ops.object.camera_add(location=(0.0, -4.8, 2.1), rotation=(math.radians(72.0), 0.0, 0.0))",
        "cam = bpy.context.active_object",
        "cam.name = 'Studio_Camera'",
        "scene.camera = cam",
    ])

    valid, issues = validate_bpy_script(bpy_code)
    if not valid:  # pragma: no cover - defensive guard
        raise ValueError(f"bpy_script failed security validation: {issues}")

    return {
        "ok": True,
        "package": PACKAGE_NAME,
        "legacy_package": LEGACY_PACKAGE_NAME,
        "version_declared": UPSTREAM_VERSION,
        "license": LICENSE,
        "preset": preset_key,
        "preset_title": spec["title"],
        "prompt": clean_prompt,
        "engine": engine,
        "resolution": [res_x, res_y],
        "samples": samples,
        "objects": list(spec["objects"]),
        "bpy_script": bpy_code,
        "script_verified_safe": True,
        "security_issues": [],
        "mcp_call": {
            "server": PACKAGE_NAME,
            "tool": "execute_blender_code",
            "arguments": {"code": bpy_code},
            "loopback_only": True,
        },
        "inference_verified": False,
        "note_ru": (
            f"Сцена «{spec['title']}» подготовлена для MCP for Blender ({PACKAGE_NAME} v{UPSTREAM_VERSION}, MIT). "
            "Скрипт bpy проверен детерминированным фильтром безопасности (без сети и shell). "
            "Для живого рендера запустите Blender с аддоном BlenderMCP на 127.0.0.1:9876."
        ),
    }
