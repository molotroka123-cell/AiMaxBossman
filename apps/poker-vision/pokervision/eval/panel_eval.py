"""Raise-panel reading accuracy on held-out frames: preset values (cells found / values right / wrong / unknown) and the "Raise to" number."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

from ..adapters.base import Frame
from ..adapters.poker_train import PokerTrainAdapter, Profile
from ..adapters.poker_train_panel import TrainerPanelReader
from ..control.executor import Fresh

VOCAB = ("FOLD", "CHECK", "CALL", "RAISE", "ALL IN", "CONFIRM", "Dashboard", "Exit", "Range Chart", "Coach", "Live", "DEAL")


def run(data: Path, sessions: list[str], profile: Path) -> dict:
    ad = PokerTrainAdapter(Profile.load(profile))
    rd = TrainerPanelReader()
    tot = {"frames": 0, "panel_open_found": 0, "cells_truth": 0, "cells_found": 0, "values_right": 0, "values_wrong": 0, "raise_to_truth": 0, "raise_to_right": 0, "raise_to_wrong": 0, "raise_to_unknown": 0,
           "confirm_found": 0}
    wrong = []
    for sess in sessions:
        ad.reset()
        for line in (data / sess / "labels.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(line); t = r["truth"]
            if not r["stable"] or t["animating"] or not t.get("raise_panel"):
                continue
            img = cv2.imread(str(data / sess / r["frame"]))
            fr = Frame(img, r["t_ms"], "eval", r["frame"])
            st = ad.read(fr)
            tot["frames"] += 1
            pn = rd.read(Fresh(fr, st, None, 0.0))
            if not pn.open:
                continue
            tot["panel_open_found"] += 1
            tot["confirm_found"] += 1 if pn.confirm else 0
            dpr = t["dpr"]
            truth_cells = [(b["x"] * dpr, b["y"] * dpr, b["amount"]) for b in t["buttons"] if b["label"] not in VOCAB and b.get("amount") is not None and 36 <= b["h"] <= 62]
            tot["cells_truth"] += len(truth_cells)
            for (tx, ty, tv) in truth_cells:
                hit = [p for p in pn.presets if abs(p.box.x - tx) < 8 and abs(p.box.y - ty) < 8]
                if hit:
                    tot["cells_found"] += 1
                    if abs(hit[0].value - tv) <= max(0.5, 0.0):
                        tot["values_right"] += 1
                    else:
                        tot["values_wrong"] += 1; wrong.append((sess, r["frame"], tv, hit[0].value))
            if t.get("raise_to_text") and t.get("raise_to_vis", 1) >= 0.99:
                tot["raise_to_truth"] += 1
                want = float(t["raise_to_text"].replace(",", "").replace("K", "e3").replace("M", "e6"))
                if pn.raise_to is None:
                    tot["raise_to_unknown"] += 1
                elif abs(pn.raise_to - want) <= 0.5 or (t["raise_to_text"][-1] in "KM" and abs(pn.raise_to - want) <= 0.06 * want):
                    tot["raise_to_right"] += 1
                else:
                    tot["raise_to_wrong"] += 1; wrong.append((sess, r["frame"], "raise_to", want, pn.raise_to))
    tot["wrong_examples"] = wrong[:10]
    return tot


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True); ap.add_argument("--sessions", nargs="+", required=True); ap.add_argument("--profile", required=True); ap.add_argument("--out")
    a = ap.parse_args(argv)
    res = run(Path(a.data), a.sessions, Path(a.profile))
    print(json.dumps(res, indent=1, ensure_ascii=False))
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
