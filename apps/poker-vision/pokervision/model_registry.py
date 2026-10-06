"""Versioned perception profiles with a separate holdout gate and rollback.

A candidate profile (new calibration / re-labelled data) is never activated because it exists or because it scored well on
its own calibration data. ``propose`` evaluates BOTH the active profile and the candidate on the SAME held-out frames and
promotes only if no critical error rate got worse (beyond a margin) and the answered share did not collapse. Examples
stored for labelling are a memory of cases, not weight training."""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

CRITICAL = ("hero_cards", "board", "pot", "hero_stack", "street")


@dataclass
class Decision:
    promoted: bool
    reason: str
    baseline: dict
    candidate: dict


def _wrong_rate(m: dict, f: str) -> float:
    return m[f]["wrong"] / max(1, m[f]["ok"] + m[f]["wrong"] + m[f]["unknown"])


def _answered(m: dict, f: str) -> float:
    return (m[f]["ok"] + m[f]["wrong"]) / max(1, m[f]["ok"] + m[f]["wrong"] + m[f]["unknown"])


class ProfileRegistry:
    def __init__(self, root: Path):
        self.root = Path(root); (self.root / "versions").mkdir(parents=True, exist_ok=True)
        self.pointer = self.root / "active.json"

    def versions(self) -> list[str]:
        return sorted(p.name for p in (self.root / "versions").iterdir())

    def active(self) -> str | None:
        return json.loads(self.pointer.read_text())["active"] if self.pointer.exists() else None

    def history(self) -> list[dict]:
        return json.loads(self.pointer.read_text()).get("history", []) if self.pointer.exists() else []

    def _write(self, active: str, entry: dict) -> None:
        hist = self.history() + [entry]
        tmp = self.pointer.with_suffix(".tmp"); tmp.write_text(json.dumps({"active": active, "history": hist}, indent=1)); tmp.replace(self.pointer)

    def add(self, name: str, profile_file: Path) -> Path:
        dst = self.root / "versions" / name
        if dst.exists():
            raise FileExistsError(name)
        shutil.copyfile(profile_file, dst); return dst

    def bootstrap(self, name: str, profile_file: Path) -> None:
        self.add(name, profile_file); self._write(name, {"t": time.time(), "event": "bootstrap", "active": name})

    def propose(self, name: str, profile_file: Path, evaluate: Callable[[Path], dict], margin: float = 0.002, min_answered_ratio: float = 0.9) -> Decision:
        """``evaluate(profile_path) -> metrics`` must score the same held-out frames for both profiles."""
        cur = self.active()
        base_m = evaluate(self.root / "versions" / cur)
        cand_path = self.add(name, profile_file)
        cand_m = evaluate(cand_path)
        for f in CRITICAL:
            if _wrong_rate(cand_m, f) > _wrong_rate(base_m, f) + margin:
                return self._reject(name, f"critical field {f}: wrong rate {_wrong_rate(cand_m, f):.4f} > baseline {_wrong_rate(base_m, f):.4f}", base_m, cand_m)
            if _answered(cand_m, f) < min_answered_ratio * _answered(base_m, f):
                return self._reject(name, f"field {f}: answered share collapsed {_answered(cand_m, f):.3f} vs {_answered(base_m, f):.3f}", base_m, cand_m)
        gain = sum(_wrong_rate(base_m, f) - _wrong_rate(cand_m, f) for f in CRITICAL) + sum(_answered(cand_m, f) - _answered(base_m, f) for f in CRITICAL)
        if gain <= 0:
            return self._reject(name, "no measurable improvement on the shared holdout", base_m, cand_m)
        self._write(name, {"t": time.time(), "event": "promote", "from": cur, "to": name, "active": name})
        return Decision(True, "no critical regression and a measurable gain on the shared holdout", base_m, cand_m)

    def _reject(self, name, why, b, c) -> Decision:
        (self.root / "versions" / name).unlink(missing_ok=True)
        self._write(self.active(), {"t": time.time(), "event": "reject", "candidate": name, "why": why, "active": self.active()})
        return Decision(False, why, b, c)

    def rollback(self) -> str:
        promos = [h for h in self.history() if h.get("event") == "promote"]
        undone = {h.get("undone") for h in self.history() if h.get("event") == "rollback"}
        live = [h for h in promos if h["to"] not in undone]
        if not live:
            raise RuntimeError("nothing to roll back")
        last = live[-1]
        self._write(last["from"], {"t": time.time(), "event": "rollback", "undone": last["to"], "active": last["from"]})
        return last["from"]
