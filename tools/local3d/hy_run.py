"""Hunyuan3D-2.1 SHAPE-only on AMD ROCm: image -> untextured mesh (GLB). usage: hy_run.py out_dir img1 [img2 ...]"""
import sys, time, json, torch
sys.path.insert(0, "D:/Bossman/models3d/hy21/hy3dshape")
from pathlib import Path
from PIL import Image
from hy3dshape.rembg import BackgroundRemover
from hy3dshape.pipelines import Hunyuan3DDiTFlowMatchingPipeline
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
import torch.backends.cuda as _c
_c.enable_flash_sdp(False); _c.enable_mem_efficient_sdp(False); _c.enable_math_sdp(True)  # ROCm gfx1151: only the math SDPA kernel is available
t0 = time.time()
pipe = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained("D:/Bossman/models3d/hy21-weights", device="cuda", dtype=torch.float16)
print("loaded", round(time.time() - t0), "s", flush=True)
rem = BackgroundRemover()
for p in sys.argv[2:]:
    t = time.time(); img = rem(Image.open(p).convert("RGB"))
    mesh = pipe(image=img, num_inference_steps=int(__import__("os").environ.get("HY_STEPS", 30)), octree_resolution=int(__import__("os").environ.get("HY_OCT", 256)), num_chunks=20000, generator=torch.manual_seed(7))[0]
    name = Path(p).stem; mesh.export(str(out / f"{name}_raw.glb"))
    print(json.dumps({"name": name, "faces": len(mesh.faces), "s": round(time.time() - t), "peak_gb": round(torch.cuda.max_memory_allocated() / 1e9, 1)}), flush=True)
