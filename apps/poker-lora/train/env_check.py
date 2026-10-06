"""Can THIS machine fine-tune a LoRA? Inference working is not evidence that training works. Prints and writes a JSON verdict.
Run on the Windows/AMD PC:  python train/env_check.py --out train_capability.json"""
from __future__ import annotations

import argparse
import importlib
import json
import platform
import sys


def probe() -> dict:
    r = {"platform": platform.platform(), "python": sys.version.split()[0], "modules": {}, "devices": [], "verdict": "NOT_READY", "notes": []}
    for m in ("torch", "transformers", "peft", "accelerate", "bitsandbytes", "torch_directml", "onnxruntime"):
        try:
            r["modules"][m] = getattr(importlib.import_module(m), "__version__", "present")
        except Exception as exc:  # noqa: BLE001
            r["modules"][m] = f"missing ({type(exc).__name__})"
    try:
        import torch
        if torch.cuda.is_available():
            hip = getattr(torch.version, "hip", None)
            for i in range(torch.cuda.device_count()):
                p = torch.cuda.get_device_properties(i)
                r["devices"].append({"index": i, "name": p.name, "backend": "ROCm/HIP" if hip else "CUDA", "vram_gb": round(p.total_memory / 2**30, 1)})
        if "missing" not in r["modules"].get("torch_directml", "missing"):
            r["devices"].append({"backend": "DirectML", "note": "inference-oriented; LoRA training on DirectML is NOT established"})
    except Exception as exc:  # noqa: BLE001
        r["notes"].append(f"torch not usable: {type(exc).__name__}")
    ok_stack = all("missing" not in r["modules"][m] for m in ("torch", "transformers", "peft"))
    gpu = [d for d in r["devices"] if d.get("backend") in ("CUDA", "ROCm/HIP")]
    if ok_stack and gpu:
        r["verdict"] = "CAN_TRY_SMOKE_TRAIN"
        r["notes"].append("a 20-step smoke train on a small model must still pass before any real run; bitsandbytes 4-bit is often unavailable on AMD/Windows (use bf16 LoRA)")
    else:
        r["notes"].append("needs torch + transformers + peft and a CUDA or ROCm device; none of this proves training works until a smoke train completes")
    return r


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--out"); a = ap.parse_args()
    res = probe(); print(json.dumps(res, indent=1))
    if a.out:
        open(a.out, "w", encoding="utf-8").write(json.dumps(res, indent=1))
