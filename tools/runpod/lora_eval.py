#!/usr/bin/env python3
"""Local evaluation of a freshly trained SDXL LoRA: sd-cli (Vulkan) test images + FaceFusion arcface similarity.

Run with the FaceFusion venv python (it provides get_many_faces):

    <facefusion-venv>/python tools/runpod/lora_eval.py --name gfgirl --lora gfgirl-lora.safetensors \
        --model epicrealismXL_pureFix.safetensors --sd-cli sd-cli.exe --facefusion DIR \
        --refs "photos/a.jpg,photos/b.jpg" --prompts prompts.json --scales 0,0.7,0.8,1.0 --out evaldir

prompts.json is a list of strings that already contain the trigger word. For every prompt x scale the script
renders `prompt <lora:NAME:scale>` (scale 0 = base-model control), finds the face with the highest similarity to
the mean reference embedding and writes results.json (+ prints mean cosine per scale).
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

NEG = "blurry, lowres, deformed, extra fingers, bad anatomy, watermark, text, cartoon, painting"


def load_facefusion(ff_dir: str):
    import cv2  # noqa: F401  (needed by facefusion)
    sys.path.insert(0, ff_dir)
    from facefusion import state_manager
    for k, v in {"execution_providers": ["directml"], "execution_device_ids": ["0"], "download_providers": ["github", "huggingface"],
                 "face_detector_model": "yolo_face", "face_detector_size": "640x640", "face_detector_angles": [0],
                 "face_detector_margin": [0, 0, 0, 0], "face_detector_score": 0.5, "face_landmarker_model": "2dfan4",
                 "face_landmarker_score": 0.5, "log_level": "error", "face_selector_mode": "many"}.items():
        state_manager.init_item(k, v)
    from facefusion.face_creator import get_many_faces

    def embs(img):
        out = []
        for f in get_many_faces([img]):
            e = getattr(f, "embedding_norm", None)
            if e is None:
                e = f.embedding / np.linalg.norm(f.embedding)
            out.append(np.asarray(e, dtype=np.float32))
        return out
    return embs


def main():
    import cv2
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--lora", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--sd-cli", required=True)
    ap.add_argument("--facefusion", required=True)
    ap.add_argument("--refs", required=True, help="comma separated reference image paths")
    ap.add_argument("--prompts", required=True)
    ap.add_argument("--scales", default="0,0.7,0.8,1.0")
    ap.add_argument("--out", required=True)
    ap.add_argument("--steps", type=int, default=24)
    ap.add_argument("--size", default="768x1024")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ldir = out / "lora_dir"
    ldir.mkdir(exist_ok=True)
    shutil.copy(a.lora, ldir / f"{a.name}.safetensors")
    embs = load_facefusion(a.facefusion)

    ref = []
    for p in a.refs.split(","):
        img = cv2.imread(p)
        e = embs(img) if img is not None else []
        if e:
            ref.append(e[0] if len(e) == 1 else max(e, key=lambda x: 0))
    ref = np.mean(ref, axis=0)
    ref /= np.linalg.norm(ref)
    prompts = json.loads(Path(a.prompts).read_text(encoding="utf-8"))
    w, h = a.size.split("x")
    results = []
    for si, s in enumerate(a.scales.split(",")):
        for pi, p in enumerate(prompts):
            png = out / f"{a.name}_p{pi + 1}_s{s}.png"
            if not png.exists():
                full = p if float(s) == 0 else f"{p} <lora:{a.name}:{s}>"
                cmd = [a.sd_cli, "-m", a.model, "--lora-model-dir", str(ldir), "-p", full, "-n", NEG, "-W", w, "-H", h,
                       "--steps", str(a.steps), "--cfg-scale", "5.5", "--sampling-method", "euler_a", "-s", str(a.seed + pi),
                       "--vae-tiling", "-o", str(png)]
                r = subprocess.run(cmd, capture_output=True, text=True)
                if r.returncode != 0 or not png.exists():
                    print("sd-cli failed", r.stderr[-300:])
                    continue
            img = cv2.imread(str(png))
            es = embs(img)
            sims = [float(e @ ref) for e in es]
            results.append({"prompt": pi + 1, "scale": float(s), "faces": len(es), "cos": max(sims) if sims else None, "png": png.name})
            print(results[-1], flush=True)
    summary = {}
    for s in sorted({r["scale"] for r in results}):
        v = [r["cos"] for r in results if r["scale"] == s and r["cos"] is not None]
        summary[str(s)] = {"mean_cos": round(float(np.mean(v)), 4) if v else None, "n_face": len(v),
                           "n_total": sum(1 for r in results if r["scale"] == s)}
    (out / "results.json").write_text(json.dumps({"summary": summary, "results": results}, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
