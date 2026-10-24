r"""Owner 24.10 «все модели должны работать с диска D»: verify the D: copy of the ollama store byte-for-byte, then point
OLLAMA_MODELS at it and restart ollama. Waits for an idle machine first. C: stays as the reserve (nothing deleted).

    python tools/d_drive_cutover.py --receipt docs/audits/2026-10-24-ssd/ollama-cutover.json
Rollback: delete the user variable OLLAMA_MODELS and restart ollama (it falls back to C:\Users\<u>\.ollama\models).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import archive_migrate as am  # noqa: E402
import model_tournament as mt  # noqa: E402

SRC = Path.home() / ".ollama" / "models"
DST = Path(r"D:\ollama\models")
OLLAMA = Path.home() / "AppData/Local/Programs/Ollama"


def restart_ollama() -> None:
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-Process 'ollama app','ollama' -ErrorAction SilentlyContinue | Stop-Process -Force"], check=False)
    time.sleep(3)
    env = dict(os.environ, OLLAMA_MODELS=str(DST))
    subprocess.Popen([str(OLLAMA / "ollama.exe"), "serve"], env=env, creationflags=0x00000008 | 0x00000200,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--receipt", required=True)
    a = ap.parse_args(argv)
    mt.wait_idle()
    rec = am.migrate(SRC, DST, pointer=None, gate=mt.wait_idle, workers=3)
    out = {"migrate": {k: rec[k] for k in ("verdict", "before", "after", "comparison", "reason") if k in rec}}
    if rec["verdict"] == "PASS":
        mt.wait_idle()
        subprocess.run(["setx", "OLLAMA_MODELS", str(DST)], check=True, capture_output=True)
        restart_ollama()
        time.sleep(8)
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open("http://127.0.0.1:11434/api/tags", timeout=30) as r:
            tags = json.load(r)["models"]
        out["after_restart"] = {"models_listed": len(tags), "OLLAMA_MODELS": str(DST)}
        out["verdict"] = "PASS" if tags else "FAIL: ollama lists no models from D:"
    else:
        out["verdict"] = "FAIL: copy differs; OLLAMA_MODELS NOT changed"
    Path(a.receipt).parent.mkdir(parents=True, exist_ok=True)
    Path(a.receipt).write_text(json.dumps(out, indent=1), encoding="utf-8", newline="\n")
    print(json.dumps(out))
    return 0 if out["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
