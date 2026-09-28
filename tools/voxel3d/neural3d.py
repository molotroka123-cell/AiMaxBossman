"""NEURAL local route (Meshy-like): text -> local image -> image->3D -> 5-10k triangle GLB.

    python tools/voxel3d/neural3d.py "a red toadstool mushroom" --name nm_mushroom --height 1.6 --out <dir>

1. text -> image: Z-Image-Turbo (Apache-2.0) through stable-diffusion.cpp `sd-cli.exe` (Vulkan,
   the Studio engine), 768x768, 8 steps, cfg 1.0. The GPU is shared: waits while another
   `sd-cli.exe` runs and while the Bossman PAUSE file exists.
2. cut-out: rembg with BiRefNet-general-lite (MIT; rembg's own default bria-rmbg-2.0 is
   non-commercial, so it is never used), largest blob kept, holes filled.
3. image -> 3D: TripoSR (MIT code + weights) on CPU via the patched runner from the RC19 H
   evaluation (`BOSSMAN_TRIPOSR_RUNNER`, default evidence\\rc19\\h\\image3d\\run_triposr_cpu.py).
4. mesh -> prop: `hipoly.from_mesh_file` (drop floaters, quadric decimation to the budget, vertex
   colour transfer, Y-up, pivot/scale) -> `mesh_check.check_prop_glb`.
Everything runs locally; no cloud call.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voxel3d.generate import wait_if_paused  # noqa: E402

BOSSMAN = Path(os.environ.get("BOSSMAN_HOME", r"C:\Users\asd\Bossman"))
SD_CLI = Path(os.environ.get("BOSSMAN_SDCLI", BOSSMAN / "media-runtime" / "sdcpp" / "vulkan" / "sd-cli.exe"))
ZIMAGE = Path(os.environ.get("BOSSMAN_ZIMAGE_DIR", BOSSMAN / "models" / "media" / "z-image-turbo"))
TRIPOSR_RUNNER = Path(os.environ.get("BOSSMAN_TRIPOSR_RUNNER",
                                     BOSSMAN / "evidence" / "rc19" / "h" / "image3d" / "run_triposr_cpu.py"))
PROMPT_TEMPLATE = ("{subject}, a single stylized 3D game asset, the whole object fully visible and centered, "
                   "three-quarter front view, plain pure white background, soft even studio lighting, "
                   "no shadow, no ground, no text, vivid colours")


def gpu_busy() -> bool:
    """True while another stable-diffusion.cpp process holds the shared GPU."""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq sd-cli.exe", "/NH"], capture_output=True,
                             text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "sd-cli.exe" in out


def wait_for_gpu(log=print, poll: float = 20.0, max_wait: float = 3 * 3600) -> float:
    waited = 0.0
    while gpu_busy() and waited < max_wait:
        if waited % 120 == 0:
            log(f"GPU busy (sd-cli.exe running); waited {waited:.0f} s")
        time.sleep(poll)
        waited += poll
    return waited


def text_to_image(subject: str, out_png: Path, seed: int = 7, size: int = 768, steps: int = 8,
                  log=print) -> dict:
    prompt = PROMPT_TEMPLATE.format(subject=subject)
    argv = [str(SD_CLI), "--diffusion-model", str(ZIMAGE / "z_image_turbo-Q8_0.gguf"), "--vae", str(ZIMAGE / "ae.safetensors"),
            "--llm", str(ZIMAGE / "Qwen3-4B-Instruct-2507-Q8_0.gguf"), "--cfg-scale", "1.0",
            "-p", prompt, "-W", str(size), "-H", str(size), "--steps", str(steps), "-s", str(seed), "-o", str(out_png)]
    waited = wait_for_gpu(log) + wait_if_paused(log)
    t0 = time.perf_counter()
    with open(out_png.with_suffix(".sd.log"), "w", encoding="utf-8", errors="replace") as fh:
        rc = subprocess.run(argv, stdout=fh, stderr=subprocess.STDOUT, timeout=1800).returncode
    rec = {"engine": "sd.cpp z-image-turbo Q8_0 (Vulkan)", "prompt": prompt, "seed": seed, "size": size, "steps": steps,
           "seconds": round(time.perf_counter() - t0, 1), "gpu_wait_s": waited, "returncode": rc}
    if rc != 0 or not out_png.is_file():
        raise RuntimeError(f"sd-cli failed (rc={rc}); see {out_png.with_suffix('.sd.log')}")
    return rec


def _largest_filled(fg):
    from scipy import ndimage

    lab, n = ndimage.label(fg)
    if n == 0:
        raise RuntimeError("cut-out found no foreground object")
    sizes = ndimage.sum(fg, lab, range(1, n + 1))
    return ndimage.binary_fill_holes(lab == (int(sizes.argmax()) + 1))


def cutout(in_png: Path, out_png: Path, tol: float = 38.0, model: str = "birefnet-general-lite") -> dict:
    """Background -> transparent. Default: rembg with BiRefNet-lite (MIT weights, CPU onnxruntime;
    one-time 224 MB download). Fallback when rembg is missing: flood fill from the border by colour
    distance (keeps soft cast shadows, so it is worse). Largest blob kept, holes filled (BiRefNet
    sometimes punches out white details such as mushroom spots)."""
    import numpy as np
    from PIL import Image
    from scipy import ndimage

    src = Image.open(in_png).convert("RGB")
    rgb = np.asarray(src).astype(np.float32)
    method = f"rembg {model}"
    try:
        import rembg

        alpha = np.asarray(rembg.remove(src, session=rembg.new_session(model), only_mask=True)).astype(np.float32) / 255.0
        fg = alpha > 0.5
    except ImportError:
        method = "border flood fill"
        border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
        bg = np.median(border, axis=0)
        near = np.linalg.norm(rgb - bg, axis=2) < tol
        lab, _ = ndimage.label(near)
        edge_labels = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
        fg = ndimage.binary_opening(~np.isin(lab, edge_labels[edge_labels > 0]), iterations=2)
    fg = _largest_filled(fg)
    alpha = ndimage.gaussian_filter(fg.astype(np.float32), 0.8)
    rgba = np.dstack([rgb, np.clip(alpha * 255, 0, 255)]).astype(np.uint8)
    Image.fromarray(rgba, "RGBA").save(out_png)
    return {"method": method, "foreground_fraction": round(float(fg.mean()), 4),
            "touches_border": bool(fg[0].any() or fg[-1].any() or fg[:, 0].any() or fg[:, -1].any())}


def image_to_mesh(rgba_png: Path, out_dir: Path, python: str = sys.executable, log=print) -> tuple[Path, dict]:
    if not TRIPOSR_RUNNER.is_file():
        raise FileNotFoundError(f"TripoSR runner not found: {TRIPOSR_RUNNER} (set BOSSMAN_TRIPOSR_RUNNER)")
    t0 = time.perf_counter()
    env = dict(os.environ, OMP_NUM_THREADS="8", PYTHONIOENCODING="utf-8")
    with open(out_dir / "triposr.log", "w", encoding="utf-8", errors="replace") as fh:
        rc = subprocess.run([python, str(TRIPOSR_RUNNER), "--device", "cpu", "--threads", "8", "--mc-res", "256",
                             "--out", str(out_dir), "--tag", "tsr", str(rgba_png)], stdout=fh, stderr=subprocess.STDOUT,
                            env=env, timeout=3600).returncode
    mesh = out_dir / f"{rgba_png.stem}_tsr" / "mesh.glb"
    if rc != 0 or not mesh.is_file():
        raise RuntimeError(f"TripoSR failed (rc={rc}); see {out_dir / 'triposr.log'}")
    return mesh, {"engine": "TripoSR (MIT) CPU, marching cubes 256", "seconds": round(time.perf_counter() - t0, 1)}


def make_neural(subject: str, name: str, out: Path, height: float, target: int = 8000, seed: int = 7,
                log=print, reuse_image: bool = False) -> dict:
    from voxel3d import hipoly, mesh_check

    out.mkdir(parents=True, exist_ok=True)
    rep: dict = {"name": name, "subject": subject, "method": "NEURAL: z-image-turbo -> TripoSR -> quadric decimation"}
    img = out / f"{name}.image.png"
    prev = out / f"{name}.report.json"
    if reuse_image and img.is_file() and prev.is_file():  # re-run later stages on the same local image
        rep["text_to_image"] = dict(json.loads(prev.read_text(encoding="utf-8"))["text_to_image"], reused=True)
    else:
        rep["text_to_image"] = text_to_image(subject, img, seed, log=log)
    rgba = out / f"{name}.rgba.png"
    rep["cutout"] = cutout(img, rgba)
    mesh, rep["image_to_3d"] = image_to_mesh(rgba, out, log=log)
    glb, stats = hipoly.from_mesh_file(mesh, name, target, height, up="z")
    stats["method"] = rep["method"]
    path = out / f"{name}.glb"
    path.write_bytes(glb)
    stats["glb"] = str(path)
    stats["label"] = subject
    stats["check"] = mesh_check.check_prop_glb(path, expect_height=height)
    hipoly.render_preview(path, out / f"{name}.preview.png")
    stats["preview"] = str(out / f"{name}.preview.png")
    rep["prop"] = stats
    (out / f"{name}.report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("subject")
    ap.add_argument("--name", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--height", type=float, default=1.2)
    ap.add_argument("--target", type=int, default=8000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--reuse-image", action="store_true", help="keep an already generated image, redo cut-out/3D")
    args = ap.parse_args(argv)
    rep = make_neural(args.subject, args.name, args.out, args.height, args.target, args.seed,
                      reuse_image=args.reuse_image)
    c = rep["prop"]["check"]
    print(json.dumps({"name": args.name, "ok": c["ok"], "triangles": c["triangles"], "sat": c["mean_saturation"],
                      "image_s": rep["text_to_image"]["seconds"], "tsr_s": rep["image_to_3d"]["seconds"]}))
    return 0 if c["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
