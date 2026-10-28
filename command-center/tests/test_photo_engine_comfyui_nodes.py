"""The graphs of art_v2.photo_engine must use ComfyUI node types that EXIST (audit 28.10).

Live check that found the defect: the compiled graphs were POSTed to the installed ComfyUI (v0.37.0, no custom nodes) and
`pose_and_structure` / `outpaint` were refused with «Node 'ApplyControlNetUnion' / 'PadImageForOutpainting' not found»; neither
name exists (real: SetUnionControlNetType + ControlNetApplyAdvanced, ImagePadForOutpaint). character_lock and
detailer_and_upscale need custom node packs (IPAdapter_plus, Impact Pack) that are not installed here, so they are only
checked for the pack they claim, not for presence. These tests read the installed ComfyUI SOURCE (no server needed) and skip
when it is not installed.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from bcc.art_v2.photo_engine import DynamicWorkflowCompiler

COMFY = Path(os.environ.get("BOSSMAN_COMFYUI_DIR", Path.home() / "Bossman" / "media-runtime" / "ComfyUI"))
pytestmark = pytest.mark.skipif(not (COMFY / "nodes.py").is_file(), reason="ComfyUI is not installed on this machine")


@pytest.fixture(scope="module")
def core_source() -> str:
    parts = []
    for root, dirs, files in os.walk(COMFY):
        if any(x in root for x in ("custom_nodes", "venv", "__pycache__", ".git", "node_modules", "tests")):
            continue
        for name in files:
            if name.endswith(".py"):
                parts.append((Path(root) / name).read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts)


def in_core(core_source: str, node: str) -> bool:
    return bool(re.search(r'["\']%s["\']\s*:' % node, core_source) or re.search(r'node_id\s*=\s*["\']%s["\']' % node, core_source)
                or re.search(r"class %s\b" % node, core_source))


def types(wf) -> set[str]:
    return {n["class_type"] for n in wf["nodes"].values()}


def test_core_only_modes_use_nodes_that_exist_in_comfyui(core_source):
    c = DynamicWorkflowCompiler()
    graphs = {
        "txt2img_flux": c.compile_txt2img("a portrait", model_family="flux_dev"),
        "txt2img_sdxl": c.compile_txt2img("a portrait", model_family="sdxl"),
        "pose_and_structure": c.compile_pose_and_structure("on stage", openpose_image="p.png", depth_map_image="d.png", canny_image="c.png"),
        "inpaint": c.compile_inpaint_outpaint("b.png", "m.png", "gold dress", mode="inpaint"),
        "outpaint": c.compile_inpaint_outpaint("b.png", "m.png", "extend", mode="outpaint"),
    }
    for name, wf in graphs.items():
        missing = sorted(t for t in types(wf) if not in_core(core_source, t))
        assert not missing, f"{name} uses ComfyUI node types that do not exist: {missing}"


def test_invented_node_names_are_gone(core_source):
    c = DynamicWorkflowCompiler()
    pose = c.compile_pose_and_structure("on stage", openpose_image="p.png")
    out = c.compile_inpaint_outpaint("b.png", "m.png", "extend", mode="outpaint")
    assert "ApplyControlNetUnion" not in types(pose) and "PadImageForOutpainting" not in types(out)
    assert {"SetUnionControlNetType", "ControlNetApplyAdvanced"} <= types(pose)
    assert "ImagePadForOutpaint" in types(out)


def test_control_stack_threads_positive_and_negative_into_the_sampler():
    wf = DynamicWorkflowCompiler().compile_pose_and_structure("on stage", openpose_image="p.png", depth_map_image="d.png",
                                                              canny_image="c.png")
    nodes = wf["nodes"]
    apps = [i for i, n in nodes.items() if n["class_type"] == "ControlNetApplyAdvanced"]
    assert len(apps) == 3
    sampler = next(n for n in nodes.values() if n["class_type"] == "KSampler")["inputs"]
    assert sampler["positive"] == [apps[-1], 0] and sampler["negative"] == [apps[-1], 1]
    assert nodes[apps[0]]["inputs"]["positive"] == ["2", 0] and nodes[apps[1]]["inputs"]["positive"] == [apps[0], 0]
    assert nodes[apps[1]]["inputs"]["negative"] == [apps[0], 1]
    union_types = [n["inputs"]["type"] for n in nodes.values() if n["class_type"] == "SetUnionControlNetType"]
    assert union_types == ["openpose", "depth", "canny/lineart/anime_lineart/mlsd"]


def test_flux_is_refused_for_the_sdxl_union_controlnet():
    c = DynamicWorkflowCompiler()
    for family in ("flux_dev", "flux_schnell"):
        with pytest.raises(ValueError, match="SDXL ControlNet"):
            c.compile_pose_and_structure("x", openpose_image="p.png", model_family=family)


def test_outpaint_pads_are_pixels_multiple_of_eight():
    wf = DynamicWorkflowCompiler().compile_inpaint_outpaint("b.png", "m.png", "extend", mode="outpaint", outpaint_factor=1.5)
    pad = next(n for n in wf["nodes"].values() if n["class_type"] == "ImagePadForOutpaint")["inputs"]
    for side in ("left", "top", "right", "bottom"):
        assert isinstance(pad[side], int) and pad[side] >= 8 and pad[side] % 8 == 0
    assert "left_pct" not in pad


def test_character_lock_and_detailer_declare_the_custom_packs_they_need():
    """Not checked against ComfyUI here: the packs are not installed. Recorded so nobody reads them as core-only."""
    c = DynamicWorkflowCompiler()
    lock = types(c.compile_character_lock("ref.png", "dancing"))
    det = types(c.compile_detailer_and_upscale("in.png", target_resolution="4k_16:9", upscale_model="RealESRGAN_4x"))
    assert "IPAdapterFaceIDPlusV2" not in lock
    assert {"IPAdapterModelLoader", "IPAdapterInsightFaceLoader", "IPAdapterUnifiedLoaderFaceID", "IPAdapterFaceID"} <= lock
    assert {"FaceDetailer", "UltralyticsDetectorProvider", "SAMLoader"} <= det        # Impact Pack
