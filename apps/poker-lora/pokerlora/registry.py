"""Versioned policies with a shared HELD-OUT gate, rollback and a record for the Bossman capability tree.

A candidate (imitation stand-in today, a LoRA adapter later) is never activated because it exists or because it scored well on its own
training data. ``propose`` evaluates the ACTIVE policy and the candidate on the SAME held-out examples (split by board) and promotes only if
 * the candidate's answers are at least as valid, and valid >= MIN_VALID,
 * the cluster-bootstrap CI of (baseline EV loss - candidate EV loss) over SPOTS is strictly positive (a few lucky hands are not evidence),
 * exploitability (profile level, where computed) did not get worse beyond a margin.
Otherwise it is rejected and the active policy stays. ``rollback`` re-activates the previous version."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from .evaluate import example_metrics, paired_diff_ci, profile_metrics

MIN_VALID = 0.99
EXPLOIT_MARGIN_PCT_POT = 1.0


@dataclass
class Verdict:
    promoted: bool
    reasons: list
    baseline: dict
    candidate: dict
    paired_ev_loss: dict


class PolicyRegistry:
    def __init__(self, root: Path):
        self.root = Path(root); (self.root / "versions").mkdir(parents=True, exist_ok=True)
        self.pointer = self.root / "active.json"

    def versions(self) -> list[str]:
        return sorted(p.name for p in (self.root / "versions").iterdir() if p.is_dir())

    def active(self) -> str | None:
        return json.loads(self.pointer.read_text())["version"] if self.pointer.exists() else None

    def meta(self, v: str) -> dict:
        return json.loads((self.root / "versions" / v / "meta.json").read_text(encoding="utf-8"))

    def register(self, version: str, meta: dict, activate: bool = False) -> None:
        d = self.root / "versions" / version; d.mkdir(parents=True, exist_ok=True)
        meta = {**meta, "version": version, "created": time.time(), "parent": self.active()}
        (d / "meta.json").write_text(json.dumps(meta, indent=1, default=str), encoding="utf-8")
        if activate:
            self._point(version)

    def _point(self, v: str) -> None:
        hist = json.loads((self.root / "history.json").read_text()) if (self.root / "history.json").exists() else []
        hist.append({"t": time.time(), "version": v})
        (self.root / "history.json").write_text(json.dumps(hist), encoding="utf-8")
        self.pointer.write_text(json.dumps({"version": v}), encoding="utf-8")

    def propose(self, version: str, meta: dict, candidate, baseline, examples: list[dict], specs: list[dict] | None = None) -> Verdict:
        cm, bm = example_metrics(candidate, examples), example_metrics(baseline, examples)
        reasons = []
        if cm["valid"]["mean"] < MIN_VALID or cm["valid"]["mean"] < bm["valid"]["mean"] - 0.005:
            reasons.append(f"validity {cm['valid']['mean']} below the gate (baseline {bm['valid']['mean']}, min {MIN_VALID})")
        pd = paired_diff_ci(bm["_by_spot"]["ev_loss_all"], cm["_by_spot"]["ev_loss_all"])
        if pd["lo"] is None or pd["lo"] <= 0:
            reasons.append(f"EV-loss improvement not supported: paired CI of (baseline - candidate) = [{pd['lo']}, {pd['hi']}] % of pot over {pd['n_spots']} spots")
        pm_c = pm_b = None
        if specs:
            pm_c, pm_b = profile_metrics(candidate, specs), profile_metrics(baseline, specs)
            if pm_c["exploitability_pct_pot"]["mean"] > pm_b["exploitability_pct_pot"]["mean"] + EXPLOIT_MARGIN_PCT_POT:
                reasons.append("exploitability got worse")
            if pm_c["invalid_share_in_play"] > pm_b["invalid_share_in_play"] + 0.005:
                reasons.append("more invalid answers in play")
        v = Verdict(not reasons, reasons, {"examples": _strip(bm), "profile": pm_b}, {"examples": _strip(cm), "profile": pm_c}, pd)
        meta = {**meta, "gate": {"promoted": v.promoted, "reasons": reasons, "paired_ev_loss": pd, "baseline_version": self.active()},
                "metrics": {"examples": _strip(cm), "profile": pm_c}}
        self.register(version, meta, activate=v.promoted)
        return v

    def rollback(self) -> str | None:
        hist = json.loads((self.root / "history.json").read_text()) if (self.root / "history.json").exists() else []
        if len(hist) < 2:
            return None
        prev = hist[-2]["version"]
        self._point(prev)
        return prev

    def tree_payload(self, sha: str, run: str) -> dict:
        v = self.active()
        m = self.meta(v) if v else {}
        return {"task": "Poker-LoRA: действие из проверенного состояния (HU NLHE, river)", "sha": sha, "run": run,
                "metrics": {"active": v, "kind": m.get("kind"), "dataset_sha256": m.get("dataset_sha256"), "gate": m.get("gate", {}).get("promoted"),
                            "paired_ev_loss": m.get("gate", {}).get("paired_ev_loss")},
                "blockers": [], "state": "working"}


def _strip(m: dict) -> dict:
    return {k: v for k, v in m.items() if not k.startswith("_")}
