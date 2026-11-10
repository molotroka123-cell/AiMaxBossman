"""Prepare the flash drive for a second PC: copies worker.py and writes mesh.json (coordinator address + pairing token).

    python -m apps.compute_mesh.make_drive E:/ --coordinator http://192.168.1.10:8850 --token-file mesh.token

The same token file is then given to the coordinator. The drive holds the token: treat it like a key.
"""
from __future__ import annotations

import argparse
import json
import secrets
import shutil
from pathlib import Path


def prepare(drive: str, coordinator: str, token_file: str) -> dict:
    if not coordinator.startswith(("http://", "https://")):
        raise ValueError("coordinator must be an http(s) URL of the main PC on the local network")
    tf = Path(token_file)
    if not tf.exists():
        tf.write_text(secrets.token_urlsafe(48), encoding="utf-8")
    token = tf.read_text(encoding="utf-8").strip()
    out = Path(drive) / "bossman-worker"
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__).with_name("worker.py"), out / "worker.py")
    (out / "mesh.json").write_text(json.dumps({"coordinator": coordinator, "token": token}, indent=2), encoding="utf-8")
    eol = chr(13) + chr(10)
    lines = ["@echo off", "cd /d %~dp0", "python worker.py", "pause", ""]
    (out / "START_WORKER.cmd").write_text(eol.join(lines), encoding="utf-8", newline="")
    return {"folder": str(out), "files": sorted(p.name for p in out.iterdir())}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("drive")
    ap.add_argument("--coordinator", required=True)
    ap.add_argument("--token-file", default="mesh.token")
    a = ap.parse_args()
    print(prepare(a.drive, a.coordinator, a.token_file))
