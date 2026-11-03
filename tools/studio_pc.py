"""CLI for pc.studio (bcc/features/studio_pc.py): drive Roblox Studio on a local build, with receipts.

usage: python tools/studio_pc.py status
       python tools/studio_pc.py proof <place.rbxlx> <out_dir>   # open -> play -> run_lua (marker) -> output -> shot -> stop -> close
       python tools/studio_pc.py <action> [--place P] [--lua CODE] [--out FILE]
Every acting step refuses while a Studio it did not launch is open (another session's play-test)."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "command-center"))
from bcc.features import studio_pc  # noqa: E402

STATE = Path(os.environ.get("LOCALAPPDATA", ".")) / "Bossman" / "CommandCenter" / "studio" / "owned.json"


def proof(place: str, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    s = studio_pc.StudioPC(state_file=STATE)
    st = s.status()
    if st["busy"] or st["studio"]:
        print(json.dumps({"verdict": "BLOCKED", "reason": st["busy_reason"] or "Studio already open", "status": st},
                         ensure_ascii=False))
        return 2
    try:
        s.open(place)
        s.play()
        time.sleep(20)
        s.run_lua('print("players", #game:GetService("Players"):GetPlayers())', out_file=str(out_dir / "output.txt"))
        s.screenshot(str(out_dir / "studio_play.png"))
        s.stop()
    finally:
        if s._owned():
            s.close()
    ok = all(r["ok"] for r in s.receipts)
    (out_dir / "receipt.json").write_text(json.dumps({"verdict": "PASS" if ok else "FAIL", "steps": s.receipts},
                                                     ensure_ascii=False, indent=1), encoding="utf-8")
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
