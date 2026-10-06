"""Learn the raise-panel digit exemplars (preset values + "Raise to") from labelled frames (DOM truth), using only the listed sessions.
Usage: calibrate_panel.py --data DIR --sessions P_tr1 P_tr2 ... --out pokervision/adapters/profiles/poker_train_panel.json"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pokervision.vision.glyphs import GlyphBook, is_sep   # noqa: E402
from pokervision.vision.textspot import spot_lines        # noqa: E402

VOCAB = ("FOLD", "CHECK", "CALL", "RAISE", "ALL IN", "CONFIRM", "Dashboard", "Exit", "Range Chart", "Coach", "Live", "DEAL")


def learn(book: GlyphBook, img, rect, text, dpr, hr, tag, pick="lower", **kw) -> bool:
    x0, y0, x1, y1 = int(rect["x"] * dpr) + 3, int(rect["y"] * dpr) + 3, int((rect["x"] + rect["w"]) * dpr) - 3, int((rect["y"] + rect["h"]) * dpr) - 3
    lines = spot_lines(img, (x0, y0, x1, y1), (hr[0] * dpr, hr[1] * dpr), **(kw or {"otsu": True}))
    if not lines:
        return False
    ln = max(lines, key=(lambda q: q.cy) if pick == "lower" else (lambda q: q.height))
    chars = [c for c in text if c not in " ,."]
    gl = [g for g in ln.glyphs if not is_sep(g, ln.height)]
    if len(gl) != len(chars):
        return False
    for g, ch in zip(gl, chars):
        book.add(ch, g, tag)
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True); ap.add_argument("--sessions", nargs="+", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    book, book_rt = GlyphBook(max_per_class=60), GlyphBook(max_per_class=60)       # presets and the big 'Raise to' digits are different sizes: separate books
    n_cells = n_rt = n_frames = 0
    for sess in a.sessions:
        for line in (Path(a.data) / sess / "labels.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(line); t = r["truth"]
            if not r["stable"] or t["animating"] or not t.get("raise_panel"):
                continue
            img = cv2.imread(str(Path(a.data) / sess / r["frame"])); dpr = t["dpr"]; n_frames += 1
            for b in t["buttons"]:
                if b["label"] not in VOCAB and b.get("amount_text") and 36 <= b["h"] <= 62:
                    n_cells += learn(book, img, b, b["amount_text"], dpr, (5.0, 18.0), "preset")
            if t.get("raise_to_text") and t.get("raise_to_vis", 1) >= 0.99:
                n_rt += learn(book_rt, img, t["raise_to_rect"], t["raise_to_text"], dpr, (14.0, 44.0), "raise_to", pick="tall", v_min=150)
    Path(a.out).write_text(json.dumps({"glyphs": book.to_json(), "glyphs_raise_to": book_rt.to_json(), "sessions": a.sessions, "frames": n_frames, "cells": n_cells, "raise_to": n_rt}, separators=(",", ":")), encoding="utf-8")
    print(f"panel profile: {n_frames} frames, {n_cells} preset cells, {n_rt} raise-to numbers -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
