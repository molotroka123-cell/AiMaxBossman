"""Generic-OCR comparator (RapidOCR = PP-OCR ONNX models bundled in the wheel; Apache-2.0; CPU) on the SAME frames/zones.

Measured question: for the numeric fields (pot, to call, hero stack) is a generic OCR better than the template reader,
and what does it cost? The comparator gets the same anchor + zones (it is not asked to find the table by itself)."""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import numpy as np

from ..adapters.poker_train import PokerTrainAdapter, Profile
from ..parse import parse_money
from .dataset import load_session
from .metrics import _inview, _vis_state


def run(data: Path, sessions: list[str], limit: int, profile: Path) -> dict:
    from rapidocr_onnxruntime import RapidOCR
    ocr = RapidOCR()
    prof = Profile.load(profile); ad = PokerTrainAdapter(prof); P = prof.data
    res = {"pot": [0, 0, 0], "to_call": [0, 0, 0], "hero_stack": [0, 0, 0]}      # ok, wrong, unknown(no number found)
    mine = {"pot": [0, 0, 0], "to_call": [0, 0, 0], "hero_stack": [0, 0, 0]}
    t_ocr, t_mine, n = [], [], 0
    for s in sessions:
        ad.reset()
        for lb in load_session(data, s):
            t = lb.truth
            if not lb.row["stable"] or t["animating"] or n >= limit:
                continue
            a = ad._anchor(lb.frame)
            if a is None:
                continue
            n += 1
            sc, top = a["s"], a["top"]
            t0 = time.perf_counter(); st = ad.read(lb.frame); t_mine.append((time.perf_counter() - t0) * 1000)
            zones = {"pot": (P["info_dy"][0], P["cy_dy"]["pot"] + 20), "to_call": (P["cy_dy"]["to_call"] - 12, P["info_dy"][1]), "hero_stack": tuple(P["stack_dy"])}
            for name, (d0, d1) in zones.items():
                key, vkey, rkey = {"pot": ("pot_text", "pot_vis", "pot_rect"), "to_call": ("to_call_text", "to_call_vis", "to_call_rect"), "hero_stack": ("hero_stack_text", "hero_stack_vis", "hero_stack_line_rect")}[name]
                if not t.get(key) or _vis_state(t.get(vkey), _inview(t.get(rkey), t)) != "visible":
                    continue
                truth = parse_money(t[key])
                y0, y1 = max(int(top + d0 * sc), 0), int(top + d1 * sc)
                crop = lb.frame.bgr[y0:y1, int(lb.frame.w * 0.2):int(lb.frame.w * 0.8)]
                t0 = time.perf_counter(); out, _ = ocr(crop); t_ocr.append((time.perf_counter() - t0) * 1000)
                txt = " ".join(x[1] for x in (out or []))
                m = re.findall(r"\d[\d,\.]*[KkMm]?", txt.replace("O", "0"))
                pred = parse_money(m[-1]) if m else None
                res[name][0 if pred and pred.agrees(truth) else 2 if pred is None else 1] += 1
                f = getattr(st, name)
                mine[name][0 if f.known and f.value.agrees(truth) else 2 if not f.known else 1] += 1
    return {"frames": n, "generic_ocr_rapidocr": res, "template_reader_after": mine,
            "latency_ms": {"rapidocr_per_crop_p50": round(float(np.percentile(t_ocr, 50)), 1), "rapidocr_per_crop_p95": round(float(np.percentile(t_ocr, 95)), 1),
                           "whole_pipeline_per_frame_p50": round(float(np.percentile(t_mine, 50)), 1)},
            "cells": "ok / wrong / unknown(no number)"}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True); ap.add_argument("--sessions", nargs="+", required=True)
    ap.add_argument("--limit", type=int, default=120); ap.add_argument("--profile", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    r = run(Path(a.data), a.sessions, a.limit, Path(a.profile))
    Path(a.out).write_text(json.dumps(r, indent=1), encoding="utf-8"); print(json.dumps(r, indent=1))
