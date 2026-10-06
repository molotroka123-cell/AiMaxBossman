"""Locator/clicker comparison on IDENTICAL scenarios in the sandbox desk (own trainer, loopback, no money).

Same states, same policy decisions, same executor and verifier; only the locator (WHERE to click) differs. Scenarios perturb the world the way
a desktop does: window moves/resizes, something on top, DPI, animation, slow rendering, a stale decision, a STOP at the worst moment.
Every click is checked against DOM ground truth: which button was really under the pointer (wrong click = another element than intended).
Outcomes: ok (verified, right element) | refused_safe (nothing clicked) | obsolete (decision dropped) | wrong_click | unverified_click."""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
from pathlib import Path

from ..control.geometry import Rect
from ..control.intent import PolicyDecision
from ..control.locator import Located, Target, VisionLocator

PROFILES = Path(__file__).resolve().parents[1] / "adapters" / "profiles"


PRESET_LABELS = {"33%", "50%", "67%", "75%", "Pot", "ALL IN", "2x", "2.5x", "3x", "4x", "3-Bet", "3-Bet+", "4-Bet", "4-Bet+"}


def rss_mb() -> float:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS"):
            return int(line.split()[1]) / 1024
    return 0.0


class DomLocator:
    """Reference: reads the button box from the page DOM. NOT a vision candidate; shows what a perfect locator would achieve."""
    name = "dom_oracle"

    def __init__(self, get_desk):
        self.get_desk = get_desk

    def locate(self, frame, state, label):
        box = self.get_desk().dom_button_box(label)
        return Located(Target(label, box, 1.0, self.name, None)) if box else Located(None, f"dom: no unique {label!r}")


def make_locator(name: str, holder: dict):
    if name == "vision":
        return VisionLocator()
    if name == "dom_oracle":
        return DomLocator(lambda: holder["svc"].src.desk)
    raise SystemExit(f"candidate {name!r} cannot run here (see docs/UI_CONTROL_CANDIDATES.md)")


def run_scenario(url: str, scenario: str, candidate: str, trials: int, timeout_s: float = 300.0, seed: int = 7) -> dict:
    from ..service import VisionService
    import tempfile
    dpr = 2.0 if scenario == "dpr2" else 1.0
    holder: dict = {}
    clicks: list[dict] = []
    outcomes: list[dict] = []
    state = {"n": 0, "moved": False}
    base_rect = (40.0, 0.0, 520.0, 900.0)

    def click_wrap(src):
        def click(px, py):
            actual = src.desk.element_at(px, py)
            clicks.append({"actual": actual, "t": time.time()})
            src.click_phys(px, py)
        return click

    def restore(svc):
        d = svc.src.desk
        try:
            d.uncover("cover1")
        except Exception:
            pass
        r = d.windows["w1"]["rect"]
        if (r.x, r.y, r.w, r.h) != base_rect:
            d.move("w1", base_rect[0], base_rect[1]); d.resize("w1", base_rect[2], base_rect[3])
        svc.desk["executor"].resume_after_owner_review()

    def decide(svc, rc, fresh):
        cm = fresh.committed
        if scenario == "hand_change":                                   # a decision taken on a previous hand
            d = rc.decision
            return PolicyDecision(d.kind, d.raise_to, "XxXx|0", d.street, d.t_ms, d.to_call, reason="stale", size_min=d.size_min, size_max=d.size_max)
        if scenario == "enter_amount":
            names = [a[0] for a in (cm.actions or [])]
            if "RAISE" in names and cm.pot:
                pot = cm.pot.amount; pre = not [c for c in cm.board if c]
                lo, hi = (1.2 * pot, 4.0 * pot) if pre else (0.3 * pot, 1.01 * pot)
                ideal = (2.5 if pre else 0.6) * pot
                stack = cm.hero_stack.amount if cm.hero_stack else ideal
                return PolicyDecision("RAISE", min(ideal, stack), rc.decision.hand_key, rc.decision.street, rc.decision.t_ms, None, reason="bench raise", size_min=min(lo, stack), size_max=min(hi, stack))
        return None

    def pre_execute(svc):
        d = svc.src.desk
        if scenario == "move_before":
            d.move("w1", 160 if not state["moved"] else 40, 0); state["moved"] = not state["moved"]
        elif scenario == "overlap":
            d.cover("cover1", Rect(40, 560, 520, 340))                   # covers the action buttons
        elif scenario == "resize":
            d.resize("w1", 420, 760)
        elif scenario == "stop_race":
            pass

    def before_press(svc):
        d = svc.src.desk
        if scenario == "move_race":
            d.move("w1", 200, 0)                                         # window moves after the frame was captured and the point computed
        elif scenario == "stop_race":
            svc.stop_ev.set()

    def post_execute(svc, decision, res):
        if svc.stop_ev.is_set() and scenario != "stop_race":
            return                                                       # teardown STOP of the bench itself, not a trial
        n_clicks = len(clicks)
        steps = res.steps
        wrong = False
        # clicks recorded since the previous outcome
        mine = clicks[state.get("c0", 0):]
        state["c0"] = n_clicks
        labels = [s["label"] for s in steps]
        detail = []
        for s_, c in zip(labels, mine):
            act = (c["actual"] or "").split("\n")[0]
            if s_.startswith("PRESET"):
                ok_ = act in PRESET_LABELS                                 # the intended element is "some preset cell"; its VALUE is verified by the read-back
            else:
                ok_ = act == s_
            detail.append((s_, act, ok_))
            if not ok_:
                wrong = True
        if len(mine) > len(labels):                                      # a click that never produced a verified step
            if any((c["actual"] not in (None, "")) and True for c in mine[len(labels):]):
                pass
        if wrong:
            kind = "wrong_click"
        elif res.ok:
            kind = "ok"
        elif res.obsolete:
            kind = "obsolete"
        elif mine:
            kind = "unverified_click"
        else:
            kind = "refused_safe"
        lat = sum(s["latency_ms"] for s in steps) if res.ok else None
        outcomes.append({"detail": detail if wrong else None, "kind": kind, "reason": res.halted or res.obsolete or "", "latency_ms": lat, "clicks": len(mine), "rss_mb": rss_mb()})
        if res.halted and not svc.stop_ev.is_set() and scenario != "stop_race":
            restore(svc)
            if scenario == "enter_amount":
                svc.src.desk.dom_click("FOLD")                           # the owner closes a half-open raise panel by hand
        if scenario == "hand_change" and res.obsolete:
            pass

    bench = {"locator": make_locator(candidate, holder), "click_wrap": click_wrap, "decide": decide, "pre_execute": pre_execute,
             "before_press_hook": before_press, "post_execute": post_execute}
    if scenario == "animation":
        bench["settle_s"] = 0.03
    svc = VisionService(Path(tempfile.mkdtemp()), PROFILES)
    holder["svc"] = svc
    svc.start_desk({"kind": "sandbox", "url": url, "bootstrap": "cash_nl10", "dpr": dpr}, desk_mode="control", seed=seed, max_hands=trials * 3, bench=bench)
    t0 = time.time()
    try:
        if scenario == "latency":
            while svc.src is None or not hasattr(svc.src, "desk"):
                time.sleep(0.2)
            svc.src.submit("cpu_throttle", rate=6.0)
        while time.time() - t0 < timeout_s and len(outcomes) < trials and not svc.finished:
            time.sleep(0.5)
            ex = svc.desk.get("executor") if svc.desk else None
            if ex and ex.halted and scenario != "stop_race" and not svc.stop_ev.is_set() and not state.get("in_exec"):
                outcomes.append({"detail": None, "kind": "refused_safe", "reason": "outside execute: " + str(ex.halted), "latency_ms": None, "clicks": 0, "rss_mb": rss_mb()})
                ex.resume_after_owner_review()                         # the owner reviewed it; the next trial starts clean
    finally:
        mem = rss_mb()
        stop_t = time.time(); svc.stop(); stop_s = time.time() - stop_t
    kinds = [o["kind"] for o in outcomes]
    lat = sorted(o["latency_ms"] for o in outcomes if o["latency_ms"])
    pct = lambda p: round(lat[min(len(lat) - 1, int(p * len(lat)))]) if lat else None
    return {"scenario": scenario, "candidate": candidate, "trials": len(outcomes), "ok": kinds.count("ok"), "refused_safe": kinds.count("refused_safe"), "obsolete": kinds.count("obsolete"),
            "wrong_click": kinds.count("wrong_click"), "unverified_click": kinds.count("unverified_click"), "latency_p50_ms": pct(0.5), "latency_p95_ms": pct(0.95),
            "rss_mb_end": round(mem), "stop_returns_s": round(stop_s, 2), "clicks_total": len(clicks), "reasons": sorted({o["reason"][:90] for o in outcomes if o["reason"]})[:4], "wrong_details": [o["detail"] for o in outcomes if o["detail"]][:3],
            "elapsed_s": round(time.time() - t0), "service_error": svc.error, "executor_halted": (svc.desk or {}).get("executor") and svc.desk["executor"].halted}


EXPECT_REFUSAL = {"overlap", "resize", "move_race", "hand_change", "stop_race"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:3000/")
    ap.add_argument("--candidates", nargs="+", default=["vision", "dom_oracle"])
    ap.add_argument("--scenarios", nargs="+", default=["find_button", "enter_amount", "move_before", "move_race", "resize", "dpr2", "overlap", "animation", "latency", "hand_change", "stop_race"])
    ap.add_argument("--trials", type=int, default=8)
    ap.add_argument("--timeout-s", type=float, default=300.0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    rows = []
    for sc in a.scenarios:
        for cand in a.candidates:
            r = run_scenario(a.url, sc, cand, a.trials if sc != "stop_race" else 1, timeout_s=a.timeout_s)
            r["expected"] = "refusal (no click)" if sc in EXPECT_REFUSAL else "verified click or safe refusal"
            rows.append(r); print(json.dumps(r, ensure_ascii=False), flush=True)
            Path(a.out).write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
