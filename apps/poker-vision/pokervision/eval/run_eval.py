"""Evaluation: perception accuracy on labelled frames with split-by-session, baseline vs after on IDENTICAL frames.

  python -m pokervision.eval.run_eval --data DIR --train A_tr1 ... --test name=sess1,sess2 ... --out DIR

Reported per split: per-field ok / wrong / unknown, exact-match and "safe" state rates, Wilson 95% intervals,
latency p50/p95 and peak RSS. Strategy quality is NOT measured here (see strategy_eval.py)."""
from __future__ import annotations

import argparse
import json
import re
import resource
import time
from collections import Counter
from pathlib import Path

from ..adapters.poker_train import PokerTrainAdapter, Profile, calibrate_profile
from ..reconcile import Config, Reconciler
from .dataset import Labelled, assert_no_deal_overlap, load_session
from .metrics import CORE, FIELDS, aggregate, score_frame, wilson


def stable(lb: Labelled) -> bool:
    return bool(lb.row["stable"]) and not lb.truth["animating"]


def run_split(adapter, items, prof: Profile, only_stable=True) -> tuple[list[dict], list[float]]:
    res, lat = [], []
    last = None
    for lb in items:
        if lb.session != last:
            adapter.reset(); last = lb.session
        if only_stable and not stable(lb):
            continue
        t0 = time.perf_counter()
        st = adapter.read(lb.frame)
        lat.append((time.perf_counter() - t0) * 1000)
        res.append(score_frame(st, lb.truth, prof.data["slot_angles"], prof.data["table_center_dy"], prof.data["dealer_offsets"]))
    return res, lat


def pct(xs, p):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(p * len(xs)))], 1) if xs else None


def summarize(res: list[dict]) -> dict:
    agg = aggregate(res)
    for f in FIELDS:
        a = agg[f]
        n = a["ok"] + a["wrong"] + a["unknown"]
        a["wrong_ci95"] = wilson(a["wrong"], n) if n else None
        a["n"] = n
    n = len(res)
    ex = agg["_state"]
    ex["exact_ci95"] = wilson(round(ex["exact_match"] * n), n) if n else None
    return agg


# ------------------------------------------------------------------ sequence level (temporal layer, hands, events)
def parse_log_actions(lines: list[str]) -> list[tuple[str, float]]:
    out = []
    for ln in lines:
        m = re.search(r"\]\s+\S+\s+(raises to|bets)\s+(\d+)", ln)
        if m:
            out.append(("raise_or_bet", float(m.group(2))))
    return out


def sequence_eval(adapter, items: list[Labelled], prof: Profile) -> dict:
    """Run the reconciler over a recorded session, compare committed values at settled frames and hand segmentation."""
    rec = Reconciler(Config())
    per_frame = []
    for lb in items:
        st = adapter.read(lb.frame)
        cm = rec.push(st)
        per_frame.append((lb, cm))
    rec.finish()
    # field accuracy of COMMITTED values on settled frames
    c = Counter()
    for k, (lb, cm) in enumerate(per_frame):
        if not stable(lb) or lb.row["tag"] not in ("settled", "decision", "deal_settled") or k < 2:
            continue
        t = lb.truth
        key = lambda x: (x.get("pot_text"), tuple(y["card"] for y in x["hero_cards"]))
        # a value cannot be committed before it has been visible on min_votes frames (+1 for the animation frame that
        # precedes readable pixels): score only frames whose truth has been unchanged for the last 3 frames
        if not (key(per_frame[k - 1][0].truth) == key(t) == key(per_frame[k - 2][0].truth)):
            c["skipped_value_changed_within_last_3_frames"] += 1
            continue
        tp = t.get("pot_text")
        if tp:
            tm = __import__("pokervision.parse", fromlist=["parse_money"]).parse_money(tp)
            if cm.pot is None: c["pot_unknown"] += 1
            elif cm.pot.agrees(tm): c["pot_ok"] += 1
            else: c["pot_wrong"] += 1
        th = [x["card"] for x in t["hero_cards"]]
        if len(th) == 2:
            if all(cm.hero_cards):
                c["hero_ok" if sorted(cm.hero_cards) == sorted(th) else "hero_wrong"] += 1
            else:
                c["hero_unknown"] += 1
    # hand segmentation: map every frame to the detected hand id; a detected hand must not contain >1 true hand
    by_det: dict[int, set] = {}
    ambiguous = 0
    for lb, cm in per_frame:
        hid = rec.frame_hand.get(lb.frame.frame_id)
        if hid is None:
            ambiguous += 1
            continue
        if lb.truth["hero_cards"]:
            by_det.setdefault(hid, set()).add(lb.row["hand_idx"])
    mixed = sum(1 for v in by_det.values() if len(v) > 1)
    true_hands = len({lb.row["hand_idx"] for lb, _ in per_frame if lb.row["hand_idx"] >= 0})
    # split detection: a true hand spread over several detected hands
    by_true: dict[int, set] = {}
    for lb, cm in per_frame:
        hid = rec.frame_hand.get(lb.frame.frame_id)
        if hid is not None and lb.truth["hero_cards"]:
            by_true.setdefault(lb.row["hand_idx"], set()).add(hid)
    split = sum(1 for v in by_true.values() if len(v) > 1)
    return {"committed": dict(c), "true_hands": true_hands, "detected_hands": len(rec.hands), "mixed_hands": mixed, "split_hands": split, "ambiguous_frames": ambiguous, "frames": len(per_frame)}


def truth_state(lb: Labelled, prof: Profile):
    """TableState built from DOM truth with the same slot geometry: the reference the pixel pipeline is compared to."""
    import math
    from ..parse import parse_money
    from ..schema import Field, Seat, TableState
    from .metrics import _inview, _vis_state
    t, P = lb.truth, prof.data
    ts = lb.row["t_ms"]
    K = lambda v: Field.ok(v, 1.0, ts, "truth")
    st = TableState(lb.frame.frame_id, ts, "truth", "truth")
    st.hero_cards = [K(c["card"]) for c in t["hero_cards"]]
    st.board = [K(c["card"]) for c in t["board"]]
    st.board_count = K(len(t["board"]))
    st.street = K({0: "preflop", 3: "flop", 4: "turn", 5: "river"}.get(len(t["board"]), "preflop"))
    pm = parse_money(t["pot_text"]) if t.get("pot_text") and _vis_state(t.get("pot_vis"), _inview(t.get("pot_rect"), t)) == "visible" else None
    st.pot = K(pm) if pm else Field.unknown(ts, "truth", "hidden")
    for n in ("to_call", "hero_stack", "dealer_slot", "num_seats", "hero_turn", "actions", "hero_position"):
        setattr(st, n, Field.unknown(ts, "truth", "n/a"))
    if not t["hero_cards"]:
        return st
    cx = t["vw"] / 2
    tcy = t["hero_cards"][0]["y"] + P["table_center_dy"]
    slots = P["slot_angles"]
    def slot(x, y, tol):
        ang = math.degrees(math.atan2(y - tcy, x - cx)) % 360
        j = min(range(len(slots)), key=lambda i: min(abs(slots[i] - ang) % 360, 360 - abs(slots[i] - ang) % 360))
        d = min(abs(slots[j] - ang) % 360, 360 - abs(slots[j] - ang) % 360)
        return j if d <= tol else None
    seats: dict[int, Seat] = {}
    for sd in t["seats"]:
        if _vis_state(sd.get("vis"), _inview(sd, t)) == "visible":
            j = slot(sd["cx"], sd["cy"], P["slot_tol_deg"])
            if j is not None:
                seats.setdefault(j, Seat(j)).stack = K(parse_money(sd["text"]))
    for b in t["bets"]:
        if _vis_state(b.get("vis"), _inview(b, t)) == "visible":
            j = slot(b["cx"], b["cy"], P["bet_tol_deg"])
            if j is not None:
                seats.setdefault(j, Seat(j)).bet = K(parse_money(b["text"]))
    st.seats = [seats[k] for k in sorted(seats)]
    return st


def _events_of(states, ids=None):
    from ..history import derive_events
    rec = Reconciler(Config())
    for st in states:
        rec.push(st)
    rec.finish()
    out = []
    for h in rec.hands:
        derive_events(h)
        out.append(Counter((e["type"], e.get("slot"), int(e["to"])) for e in h["events"] if e["type"] in ("bet", "raise", "call")))
    return out, rec


def event_eval(adapter, items: list[Labelled], prof: Profile) -> dict:
    """Missed / duplicate bet-raise-call events: pixel pipeline vs the SAME deriver fed with DOM truth (so only perception and
    temporal reconciliation are being judged, not the event semantics). Matching is by (type, slot, amount) over the whole session."""
    pred, _ = _events_of([adapter.read(lb.frame) for lb in items])
    ref, _ = _events_of([truth_state(lb, prof) for lb in items])
    P = sum(pred, Counter()); R = sum(ref, Counter())
    matched = sum((P & R).values())
    return {"reference_events": sum(R.values()), "observed_events": sum(P.values()), "matched": matched,
            "missed": sum(R.values()) - matched, "spurious_or_duplicate": sum(P.values()) - matched,
            "reference": "DOM-truth state through the same event deriver"}


# ------------------------------------------------------------------ main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="root of the TEST frames")
    ap.add_argument("--train-data", default=None, help="root of the calibration frames (default: --data)")
    ap.add_argument("--train", nargs="+", required=True)
    ap.add_argument("--test", nargs="+", required=True, help="name=sess1,sess2")
    ap.add_argument("--adapt", nargs="*", default=[], help="names of test splits to ALSO evaluate after calibrating on the first half of their hands")
    ap.add_argument("--out", required=True)
    ap.add_argument("--profile-out", default=None)
    a = ap.parse_args(argv)
    root, out = Path(a.data), Path(a.out); out.mkdir(parents=True, exist_ok=True)
    troot = Path(a.train_data) if a.train_data else root
    train = [it for s in a.train for it in load_session(troot, s)]
    prof = calibrate_profile(train, f"train={a.train}")
    if a.profile_out:
        prof.save(Path(a.profile_out))
    report = {"profile": prof.data["id"], "train_sessions": a.train, "train_frames_used": prof.data["n_calibration_frames"], "splits": {}}
    base, after = PokerTrainAdapter(prof, naive=True), PokerTrainAdapter(prof)
    for spec in a.test:
        name, sess = spec.split("=")
        sess_list = sess.split(",")
        entry = {"sessions": sess_list, "frames": 0, "scored_frames": 0, "deal_overlap_with_train": 0}
        acc = {"baseline_naive": ([], []), "after": ([], [])}
        un = Counter()
        for sname in sess_list:                                   # one session in memory at a time
            its = load_session(root, sname)
            entry["frames"] += len(its); entry["scored_frames"] += sum(stable(i) for i in its)
            entry["deal_overlap_with_train"] += len(assert_no_deal_overlap(train, its))
            for label, ad in (("baseline_naive", base), ("after", after)):
                r_, l_ = run_split(ad, its, prof)
                acc[label][0].extend(r_); acc[label][1].extend(l_)
            after.reset()
            for lb in (i for i in its if not stable(i)):          # unstable/animating frames: do we answer or abstain?
                st = after.read(lb.frame)
                un["frames"] += 1; un["hero_unknown"] += int(not all(f.known for f in st.hero_cards)); un["pot_unknown"] += int(not st.pot.known)
            del its
        for label in acc:
            entry[label] = summarize(acc[label][0])
            entry[label]["latency_ms"] = {"p50": pct(acc[label][1], .5), "p95": pct(acc[label][1], .95)}
        entry["unstable_frames_after"] = dict(un)
        if name in a.adapt:
            cal, test_r0, test_r1 = [], [], []
            for sname in sess_list:
                its = load_session(root, sname)
                hands = sorted({i.row["hand_idx"] for i in its if i.row["hand_idx"] >= 0})
                half = set(hands[: max(1, len(hands) // 2)])
                cal += [i for i in its if i.row["hand_idx"] in half]
                tst = [i for i in its if i.row["hand_idx"] not in half]
                assert not assert_no_deal_overlap([i for i in its if i.row["hand_idx"] in half], tst)
                test_r0.append(tst); del its
            prof2 = calibrate_profile(train + cal, f"train+{name} first-half hands")
            ad2 = PokerTrainAdapter(prof2)
            r_ad, r_z = [], []
            for tst in test_r0:
                r_ad += run_split(ad2, tst, prof2)[0]; r_z += run_split(after, tst, prof)[0]
            entry["adapted_after"] = summarize(r_ad); entry["adapted_after"]["calibration_frames"] = len(cal)
            entry["same_frames_zero_shot_after"] = summarize(r_z)
            del cal, test_r0
        report["splits"][name] = entry
        print(name, {k: round(v["error_rate"] or 0, 4) for k, v in entry["after"].items() if k in CORE}, flush=True)
    # sequence-level on the first test split's sessions
    first = a.test[0].split("=")
    seqs = {}
    for s in first[1].split(","):
        its = load_session(root, s)
        seqs[s] = {"after": sequence_eval(after, its, prof), "events": event_eval(after, its, prof)}
    report["sequence"] = seqs
    report["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    report["gpu"] = "NOT_RUN (CPU only)"
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
