"""python -m pokerlora.cli build|study|sft ..."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from . import dataset, sft
from .evaluate import example_metrics, profile_metrics
from .policies import EquityRule, Imitation, PassiveCheckCall, RandomLegal
from .registry import PolicyRegistry


def git_sha() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=Path(__file__).parent).stdout.strip() or "unknown"


def cmd_build(a):
    m = dataset.build(a.spots, a.seed, Path(a.out), per_node_cap=a.cap, workers=a.workers)
    print(json.dumps(m, indent=1)); return 0 if not m["board_leak"] else 1


def cmd_sft(a):
    print(json.dumps(sft.write(Path(a.data), Path(a.out)))); return 0


def cmd_study(a):
    d = Path(a.data); out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((d / "manifest.json").read_text())
    tr, va, te = dataset.load(d, "train"), dataset.load(d, "val"), dataset.load(d, "test")
    assert not (set(e["board_key"] for e in tr) & set(e["board_key"] for e in te)), "train/test share a board"
    specs = list({e["spot_id"]: e["spec"] for e in te}.values())[: a.profile_spots]
    t0 = time.time()
    imi = Imitation(seed=1).fit(tr, va)
    train_s = time.time() - t0
    pols = [RandomLegal(0), PassiveCheckCall(), EquityRule(), imi]
    rep = {"sha": git_sha(), "dataset": {k: manifest[k] for k in ("sha256", "examples", "boards", "spots_kept", "spots_rejected", "board_leak")},
           "test_profile_spots": len(specs), "policies": {}, "train_seconds_imitation": round(train_s, 1)}
    for p in pols:
        t = time.time()
        em = example_metrics(p, te); pm = profile_metrics(p, specs)
        em.pop("_by_spot", None)
        rep["policies"][p.name] = {"examples": em, "profile": pm, "eval_seconds": round(time.time() - t, 1)}
        print(p.name, "valid", em["valid"]["mean"], "ev_loss_all", em["ev_loss_all"], "expl", pm["exploitability_pct_pot"]["mean"], flush=True)
    reg = PolicyRegistry(out / "registry")
    reg.register("v0_equity_rule", {"kind": "baseline", "dataset_sha256": manifest["sha256"]}, activate=True)
    v = reg.propose("v1_imitation_standin", {"kind": "imitation_mlp (stand-in, NOT a LoRA)", "dataset_sha256": manifest["sha256"], "sha": git_sha(),
                                              "base": "none", "train_examples": len(tr)}, imi, EquityRule(), te, specs)
    rep["registry"] = {"active": reg.active(), "promoted": v.promoted, "reasons": v.reasons, "paired_ev_loss_pct_pot": v.paired_ev_loss, "versions": reg.versions()}
    rb = reg.rollback() if v.promoted else None
    rep["registry"]["rollback_to"] = rb; rep["registry"]["active_after_rollback"] = reg.active()
    (out / "study_report.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--spots", type=int, default=300); b.add_argument("--seed", type=int, default=20261006)
    b.add_argument("--out", required=True); b.add_argument("--cap", type=int, default=6); b.add_argument("--workers", type=int, default=4); b.set_defaults(fn=cmd_build)
    s = sub.add_parser("sft"); s.add_argument("--data", required=True); s.add_argument("--out", required=True); s.set_defaults(fn=cmd_sft)
    st = sub.add_parser("study"); st.add_argument("--data", required=True); st.add_argument("--out", required=True); st.add_argument("--profile-spots", type=int, default=40); st.set_defaults(fn=cmd_study)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
