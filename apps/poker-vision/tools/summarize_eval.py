"""Turn eval report.json into a compact markdown table (baseline -> after, same frames)."""
import json, sys
from pathlib import Path

CORE = ["hero_cards", "board", "pot", "to_call", "hero_stack", "street"]
EXTRA = ["seat_stacks", "bets", "dealer", "actions", "hero_turn"]


def cell(a):
    n = a["ok"] + a["wrong"] + a["unknown"]
    return f'{a["wrong"]}/{a["unknown"]}/{n}'


def main(path, out=None):
    r = json.loads(Path(path).read_text())
    L = [f"profile `{r['profile']}` calibrated on {r['train_sessions']} ({r['train_frames_used']} frames). Cells: wrong / unknown / scored.", ""]
    for name, e in r["splits"].items():
        L += [f"### {name}  (sessions {', '.join(e['sessions'])}; {e['scored_frames']} scored frames; deal overlap with train: {e['deal_overlap_with_train']})", "",
              "| field | baseline (naive, no UNKNOWN discipline) | after |", "|---|---|---|"]
        for f in CORE + EXTRA:
            b, a = e["baseline_naive"][f], e["after"][f]
            L.append(f"| {f} | {cell(b)} | {cell(a)} |")
        bs, as_ = e["baseline_naive"]["_state"], e["after"]["_state"]
        L += ["", f"exact-match state (all core fields right): baseline {bs['exact_match']:.3f}, after {as_['exact_match']:.3f} · "
                  f"state with no wrong core field: baseline {bs['safe_no_wrong_core']:.3f}, after {as_['safe_no_wrong_core']:.3f}",
              f"latency per frame (CPU): p50 {e['after']['latency_ms']['p50']} ms, p95 {e['after']['latency_ms']['p95']} ms", ""]
        if "adapted_after" in e:
            z, ad = e["same_frames_zero_shot_after"], e["adapted_after"]
            L += [f"adaptation (calibrate on first-half hands of this split, test on the other half, {ad['calibration_frames']} calibration frames): "
                  f"core wrong/unknown zero-shot {sum(z[f]['wrong'] for f in CORE)}/{sum(z[f]['unknown'] for f in CORE)} -> adapted {sum(ad[f]['wrong'] for f in CORE)}/{sum(ad[f]['unknown'] for f in CORE)}; "
                  f"exact-match {z['_state']['exact_match']:.3f} -> {ad['_state']['exact_match']:.3f}", ""]
    L += ["### sequence level (temporal reconciliation, hands, events) on " + ", ".join(r["sequence"]), ""]
    for s, v in r["sequence"].items():
        L.append(f"- {s}: committed {v['after']['committed']}; hands true/detected {v['after']['true_hands']}/{v['after']['detected_hands']}, mixed {v['after']['mixed_hands']}, split {v['after']['split_hands']}, ambiguous frames {v['after']['ambiguous_frames']}/{v['after']['frames']}; events {v['events']['matched']}/{v['events']['reference_events']} matched, missed {v['events']['missed']}, spurious/duplicate {v['events']['spurious_or_duplicate']}")
    L += ["", f"peak RSS {r['peak_rss_mb']} MB · GPU: {r['gpu']}"]
    text = "\n".join(L)
    if out: Path(out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main(*sys.argv[1:])
