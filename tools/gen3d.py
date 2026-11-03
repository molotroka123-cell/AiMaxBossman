"""gen3d — one entry point for Bossman's LOCAL text/image -> 3D tools (02.11.2026, owner: «лучшая локальная модель для 3д»).

Everything runs offline on this machine (AMD Radeon 8060S, ROCm torch from apps-local/blender-lab/venv-ai, models on D:).
Install root: D:\\Bossman\\models3d  (venv, hy21/, cube/, weights, js/).  Helper sources live in tools/local3d/.

  python tools/gen3d.py cube  "a velvet curved luxury sofa"  --out OUT_DIR [--color 120,40,60 --height 2.2]
        Roblox cube3d-v0.5 (text -> OBJ -> game GLB <=18k faces + preview PNG).   ~8 min / object.
  python tools/gen3d.py hy    concept.png [more.png]  --out OUT_DIR
        Hunyuan3D-2.1 shape-only (image -> untextured mesh, ROCm, math SDPA).      ~10 min / object.
  python tools/gen3d.py qwen  jobs.json --out OUT_DIR
        Qwen3.5-4B-3D-Coder (Ollama `qwen35-3d-coder`) writes Three.js -> GLB, 3 retries with the error text.
  python tools/gen3d.py post  mesh.(obj|glb) out.glb [--color r,g,b --height H]   decimate + colour + stats
  python tools/gen3d.py preview model.glb out.png                                  Blender headless render

Bossman agent path: create a task for a terminal-capable agent (e.g. Kev 79) whose prompt is the exact command above;
results are GLB + PNG files in OUT_DIR. Rules: cartoon/stylised only, no real-person likeness, no gambling props."""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path("D:/Bossman/models3d")
PY = str(ROOT / "venv/Scripts/python.exe")
BLENDER = "C:/Users/asd/Bossman/apps-local/blender-lab/blender-5.2.2-windows-x64/blender.exe"
HERE = Path(__file__).resolve().parent / "local3d"


def sh(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def post(src, dst, color="200,200,200", height="6"):
    sh([PY, HERE / "postproc.py", src, dst, color, height])


def preview(glb, png):
    sh([BLENDER, "-b", "-P", HERE / "render_preview.py", "--", Path(glb).resolve(), Path(png).resolve()], stdout=subprocess.DEVNULL)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tool", choices=["cube", "hy", "qwen", "post", "preview"])
    ap.add_argument("src", nargs="+")
    ap.add_argument("--out", default=".")
    ap.add_argument("--color", default="200,200,200")
    ap.add_argument("--height", default="6")
    a = ap.parse_args()
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    env = {"PYTHONPATH": str(ROOT / "cube"), "HF_HOME": str(ROOT / "hf")}
    if a.tool == "cube":
        for i, prompt in enumerate(a.src):
            d = out / f"cube_{i}"
            d.mkdir(exist_ok=True)
            import os
            sh([PY, "-m", "cube3d.generate", "--gpt-ckpt-path", ROOT / "cube-weights/shape_gpt.safetensors",
                "--shape-ckpt-path", ROOT / "cube-weights/shape_tokenizer.safetensors",
                "--config-path", ROOT / "cube/cube3d/configs/open_model_v0.5.yaml", "--prompt", prompt,
                "--output-dir", d, "--resolution-base", "7.5"], env={**os.environ, **env})
            post(d / "output.obj", d / "model_game.glb", a.color, a.height)
            preview(d / "model_game.glb", d / "preview.png")
    elif a.tool == "hy":
        sh([PY, HERE / "hy_run.py", out, *a.src])
    elif a.tool == "qwen":
        sh([PY, HERE / "gen_qwen3d.py", out, a.src[0]])
    elif a.tool == "post":
        post(a.src[0], a.src[1], a.color, a.height)
    elif a.tool == "preview":
        preview(a.src[0], a.src[1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
