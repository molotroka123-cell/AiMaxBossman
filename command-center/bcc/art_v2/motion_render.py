"""Render a MotionSceneGraph to MP4 (audit 28.10, 2.3 step 2): PIL draws each frame from `frame_state`, FFmpeg encodes.

Rendered layer kinds: `image` (needs "src"), `kinetic_text` (needs "text"; optional "color", "size").
`video`, `voxel3d`, `particles` and `audio` layers are NOT rendered here: they are refused loudly, never skipped,
so a scene can never claim an effect it did not produce. Optional scene keys: "width", "height", "background" (#rrggbb),
"audio" (path muxed under the clip). File paths must stay inside `base_dir`.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from . import motion_timeline as mt

RENDERABLE = {"image", "kinetic_text"}
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _font(size: int):
    from PIL import ImageFont
    for name in ("arial.ttf", "DejaVuSans.ttf", "segoeui.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _inside(base_dir: Path, rel: Any, what: str) -> Path:
    p = (base_dir / str(rel)).resolve()
    if base_dir.resolve() not in p.parents or not p.is_file():
        raise mt.SceneError(f"{what} must be an existing file inside the scene directory")
    return p


def _layer_image(layer: dict[str, Any], base_dir: Path):
    from PIL import Image, ImageDraw
    if layer["kind"] == "image":
        return Image.open(_inside(base_dir, layer.get("src", ""), f"layer {layer['id']!r}: src")).convert("RGBA")
    text = str(layer.get("text", ""))
    if not text:
        raise mt.SceneError(f"layer {layer['id']!r}: kinetic_text needs text")
    color = layer.get("color", "#ffffff")
    if not _HEX.match(str(color)):
        raise mt.SceneError(f"layer {layer['id']!r}: color must be #rrggbb")
    font = _font(int(layer.get("size", 96)))
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    l, t, r, b = probe.textbbox((0, 0), text, font=font)
    img = Image.new("RGBA", (r - l + 8, b - t + 8), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((4 - l, 4 - t), text, font=font, fill=color)
    return img


def render_scene(scene: dict[str, Any], out_path: Path, base_dir: Path, ffmpeg: str) -> dict[str, Any]:
    """Encode the scene to `out_path`; returns {frames, width, height, fps, path}. Raises SceneError on anything unrenderable."""
    from PIL import Image
    mt.validate_scene(scene)
    for layer in scene["layers"]:
        if layer["kind"] not in RENDERABLE:
            raise mt.SceneError(f"layer {layer['id']!r}: kind {layer['kind']!r} is not renderable yet")
    w, h = int(scene.get("width", 640)), int(scene.get("height", 360))
    if not (16 <= w <= 3840 and 16 <= h <= 2160) or w % 2 or h % 2:
        raise mt.SceneError("width/height must be even and within 16..3840 x 16..2160")
    bg = scene.get("background", "#000000")
    if not _HEX.match(str(bg)):
        raise mt.SceneError("background must be #rrggbb")
    fps = scene["fps"]
    n = max(1, round(scene["duration_ms"] * fps / 1000))
    sprites = {l["id"]: _layer_image(l, base_dir) for l in scene["layers"]}
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{w}x{h}", "-r", str(fps), "-i", "-"]
    if scene.get("audio"):
        cmd += ["-i", str(_inside(base_dir, scene["audio"], "audio")), "-shortest", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for frame in range(n):
            canvas = Image.new("RGBA", (w, h), str(bg))
            state = mt.frame_state(scene, frame)
            for layer in scene["layers"]:               # list order = draw order, first is the bottom
                p = state[layer["id"]]
                sp = sprites[layer["id"]]
                scale = max(0.01, p.get("scale", 1.0))
                if scale != 1.0:
                    sp = sp.resize((max(1, round(sp.width * scale)), max(1, round(sp.height * scale))))
                if p.get("rotation"):
                    sp = sp.rotate(-p["rotation"], expand=True)
                if "opacity" in p:
                    o = min(1.0, max(0.0, p["opacity"]))
                    sp = sp.copy()
                    sp.putalpha(sp.getchannel("A").point(lambda v: int(v * o)))
                x = round(p.get("x", 0.5) * w - sp.width / 2)      # x, y: centre as a fraction of the frame
                y = round(p.get("y", 0.5) * h - sp.height / 2)
                canvas.paste(sp, (x, y), sp)                       # opaque canvas: paste-with-mask == alpha blend
            proc.stdin.write(canvas.convert("RGB").tobytes())
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        if proc.wait(timeout=300) != 0:
            raise RuntimeError(f"ffmpeg failed: {err[-400:]}")
    finally:
        if proc.poll() is None:
            proc.kill()
    return {"frames": n, "width": w, "height": h, "fps": fps, "path": str(out_path)}
