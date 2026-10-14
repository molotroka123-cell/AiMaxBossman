"""Open-source GTO solve of one postflop spot with TexasSolver (AGPL-3.0, local, free) — Bossman's stand-in for GTO Wizard.

    python tools/gto_solve.py spot.json            # -> prints the first player's strategy per hand, JSON to stdout
    spot.json = {"pot": 10, "stack": 40, "board": "Qs,8d,7h,2c,3s", "ip": "AA,KK,AQs,...", "oop": "QQ,88,...",
                 "bets": [33, 75], "raise": [60], "accuracy": 0.3, "iterations": 300, "hands": ["QhQd", "AhQh"]}

Measured 14.10 on the owner PC (16 threads): a river spot with ~20-hand ranges solved to 0.29% exploitability in
~10 s. Study and post-session review only — never as real-time assistance at a real table (poker-room rules).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SOLVER = Path(os.environ.get("BOSSMAN_TEXASSOLVER", r"C:\Users\asd\Bossman\apps-local\texassolver\TexasSolver-v0.2.0-Windows"))
STREETS = ("flop", "turn", "river")


def build_input(spot: dict, out_json: Path) -> str:
    bets = ",".join(str(b) for b in spot.get("bets", [33, 75]))
    raises = ",".join(str(r) for r in spot.get("raise", [60]))
    lines = [f"set_pot {spot['pot']}", f"set_effective_stack {spot['stack']}", f"set_board {spot['board']}",
             f"set_range_ip {spot['ip']}", f"set_range_oop {spot['oop']}"]
    for who in ("oop", "ip"):
        for street in STREETS:
            lines += [f"set_bet_sizes {who},{street},bet,{bets}", f"set_bet_sizes {who},{street},raise,{raises}",
                      f"set_bet_sizes {who},{street},allin"]
    lines.append(f"set_bet_sizes oop,river,donk,{bets}")
    lines += [f"set_allin_threshold {spot.get('allin_threshold', 0.67)}", "build_tree",
              f"set_thread_num {spot.get('threads', 16)}", f"set_accuracy {spot.get('accuracy', 0.3)}",
              f"set_max_iteration {spot.get('iterations', 300)}", "set_print_interval 50", "set_use_isomorphism 1",
              "start_solve", "set_dump_rounds 1", f"dump_result {out_json.as_posix()}"]
    return "\n".join(lines) + "\n"


def solve(spot: dict, timeout: float = 900) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        inp, out = Path(tmp) / "in.txt", Path(tmp) / "out.json"
        inp.write_text(build_input(spot, out), encoding="utf-8")
        proc = subprocess.run([str(SOLVER / "console_solver.exe"), "-i", str(inp)], cwd=str(SOLVER),
                              capture_output=True, text=True, timeout=timeout)
        expl = [ln for ln in proc.stdout.splitlines() if "Total exploitability" in ln]
        tree = json.loads(out.read_text(encoding="utf-8"))
    st = tree.get("strategy", {})
    acts = st.get("actions", [])
    want = spot.get("hands")
    strat = {h: dict(zip(acts, [round(p, 3) for p in probs])) for h, probs in st.get("strategy", {}).items()
             if not want or h in want}
    return {"actions": acts, "strategy": strat, "exploitability": expl[-1].strip() if expl else None,
            "solver": "TexasSolver v0.2.0 (AGPL-3.0)"}


if __name__ == "__main__":
    print(json.dumps(solve(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))), ensure_ascii=False, indent=1))
