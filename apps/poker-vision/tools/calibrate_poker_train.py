"""Calibrate the Poker Train layout profile from the TRAIN split only and write adapters/profiles/poker_train.json."""
import argparse, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pokervision.adapters.poker_train import calibrate_profile, Profile, PROFILE_PATH
from pokervision.eval.dataset import SPLITS, load_session


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--sessions", nargs="*", default=SPLITS["train"])
    ap.add_argument("--out", default=str(PROFILE_PATH))
    a = ap.parse_args()
    t0 = time.time()
    items = [it for s in a.sessions for it in load_session(Path(a.data), s)]
    prof = calibrate_profile(items, f"train sessions {a.sessions}")
    sha = prof.save(Path(a.out))
    gl = {k: len(v) for k, v in prof.glyphs.exemplars.items()}
    print(f"profile {prof.id} sha={sha} frames_used={prof.data['n_calibration_frames']} slots={prof.data['slot_angles']}")
    print("glyph classes", len(gl), "missing digits:", [d for d in "0123456789$KM" if d not in gl])
    print("rank exemplars", {k: len(v) for k, v in prof.cards.rank.items()})
    print("suit exemplars", {k: len(v) for k, v in prof.cards.suit.items()})
    print(f"{time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
