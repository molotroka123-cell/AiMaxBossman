"""CLI for pc.studio (bcc/features/studio_pc.py): drive Roblox Studio on a local build, with receipts.

usage: python tools/studio_pc.py status
       python tools/studio_pc.py proof <place.rbxlx> <out_dir>   # open -> play -> run_lua (marker) -> output -> shot -> stop -> close
       python tools/studio_pc.py <action> [--place P] [--lua CODE] [--out FILE]
Every acting step refuses while a Studio it did not launch is open (another session's play-test)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "command-center"))
from bcc.features import studio_pc  # noqa: E402

STATE = Path(os.environ.get("LOCALAPPDATA", ".")) / "Bossman" / "CommandCenter" / "studio" / "owned.json"


def _git_head(path: Path) -> str:
    try:
        r = subprocess.run(["git", "-C", str(path.parent), "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=10, check=False)
        return (r.stdout or "").strip() if r.returncode == 0 else ""
    except OSError:
        return ""


def _sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def proof(place: str, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    place_path = Path(place).resolve()
    build_sha = _git_head(place_path)
    place_sha256 = _sha256(place_path)
    s = studio_pc.StudioPC(state_file=STATE)
    st = s.status()
    if st["busy"] or st["studio"]:
        blocked_shot = out_dir / "desktop_state_while_blocked.png"
        shot_rec = s.screenshot(str(blocked_shot))
        rec = {
            "capability": "pc.studio",
            "verdict": "BLOCKED",
            "simulated": False,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "place": str(place_path),
            "place_sha256": place_sha256,
            "build_sha": build_sha,
            "reason": st["busy_reason"] or "Studio already open; refusing to interfere with foreign Studio process",
            "status": st,
            "read_only_screenshot_receipt": shot_rec,
        }
        (out_dir / "receipt.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(rec, ensure_ascii=False))
        return 2
    owned_pid = None
    shot_file = out_dir / "studio_play.png"
    try:
        r_open = s.open(str(place_path))
        owned_pid = r_open.get("pid")
        s.play()
        time.sleep(20)
        s.run_lua('print("players", #game:GetService("Players"):GetPlayers())', out_file=str(out_dir / "output.txt"))
        s.screenshot(str(shot_file))
        s.stop()
    finally:
        if s._owned():
            s.close()
    img_info = studio_pc.inspect_png(shot_file)
    ok = all(r["ok"] for r in s.receipts) and img_info["valid_png"] and not img_info["is_stub_1x1"]
    rec = {
        "capability": "pc.studio",
        "verdict": "PASS" if ok else "FAIL",
        "ok": ok,
        "simulated": False,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "place": str(place_path),
        "place_sha256": place_sha256,
        "build_sha": build_sha,
        "pid": owned_pid,
        "screenshot": str(shot_file),
        "image": img_info,
        "steps": s.receipts,
    }
    (out_dir / "receipt.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PASS" if ok else "FAIL", out_dir / "receipt.json")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=studio_pc.ACTIONS + ("proof",))
    ap.add_argument("args", nargs="*")
    ap.add_argument("--place", default="")
    ap.add_argument("--lua", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    if a.action == "proof":
        return proof(a.args[0], Path(a.args[1]))
    s = studio_pc.StudioPC(state_file=STATE)
    fn = {"status": s.status, "open": lambda: s.open(a.place), "front": s.front, "play": s.play, "stop": s.stop,
          "run_lua": lambda: s.run_lua(a.lua, out_file=a.out), "copy_output": lambda: s.copy_output(a.out),
          "screenshot": lambda: s.screenshot(a.out), "close": s.close}[a.action]
    try:
        print(json.dumps(fn(), ensure_ascii=False))
    except (PermissionError, RuntimeError, ValueError, FileNotFoundError) as exc:
        print(json.dumps({"action": a.action, "ok": False, "refused": str(exc)}, ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
