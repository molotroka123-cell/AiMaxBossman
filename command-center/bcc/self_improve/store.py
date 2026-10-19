"""Journal-backed store: ONE hash-chained append-only journal is the single source of truth.

Reuses ``bcc.autonomy.journal.Journal`` (sha256 chain + head file, so editing, reordering,
deleting or truncating entries is detected). The view served to the Command Center is a pure
replay of that journal - there is no second, mutable state file that could drift from the evidence.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..autonomy.journal import Journal
from .limits import BLOCKED, FAIL, NA, NOT_RUN, PASS
from .observation import validate_observation
from .redact import assert_clean, sanitize

LEARNING_KINDS = ("observation", "hypothesis", "experiment", "result", "draft")


def default_root(data_dir: str | os.PathLike) -> Path:
    return Path(data_dir) / "self_improve"


class Store:
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)
        self.journal = Journal(self.root / "journal")

    # ------------------------------------------------------------ write
    def record(self, kind: str, payload: dict[str, Any]) -> str:
        if kind in LEARNING_KINDS:
            payload = sanitize(payload) | {k: payload[k] for k in ("id", "state", "hypothesis_id") if k in payload}
            assert_clean({k: v for k, v in payload.items() if k not in ("id", "state", "hypothesis_id")})
        return self.journal.append(f"self_improve.{kind}", payload)

    def record_observation(self, rec: dict) -> str:
        problems = validate_observation(rec)
        if problems:
            raise ValueError("invalid observation: " + "; ".join(problems))
        return self.record("observation", rec)

    # ------------------------------------------------------------ read
    def events(self, kind: str | None = None) -> list[dict]:
        full = f"self_improve.{kind}" if kind else "self_improve"
        return self.journal.entries(kind=full)

    def verify(self):
        return self.journal.verify()

    def state(self) -> dict:
        """Everything the Command Center shows. Missing data is the literal 'N/A', never a guess."""
        verdict = self.verify()
        obs: list[dict] = []
        hyps: dict[str, dict] = {}
        exps: dict[str, dict] = {}
        results: dict[str, dict] = {}
        refusals: list[dict] = []
        applies: dict[str, dict] = {}
        limits: dict = {}
        for e in self.journal.entries():
            kind = str(e["kind"]).removeprefix("self_improve.")
            p = e.get("payload") if isinstance(e.get("payload"), dict) else {}
            if kind == "observation":
                obs.append(p)
            elif kind == "hypothesis":
                hyps.setdefault(str(p.get("id")), {}).update(p)
            elif kind == "experiment":
                exps.setdefault(str(p.get("id")), {}).update(p)
            elif kind == "result":
                results[str(p.get("hypothesis_id"))] = p
            elif kind == "refusal":
                refusals.append({"ts": e["ts"], **p})
            elif kind == "limits.frozen":
                limits = p
            elif kind in ("apply", "rollback"):
                applies.setdefault(str(p.get("hypothesis_id")), {}).update({"last": kind, **p})
        cost = 0.0
        for r in results.values():
            c = r.get("cost_usd")
            cost += c if isinstance(c, (int, float)) else 0.0
        fails = [o for o in obs if o.get("outcome") in (FAIL, BLOCKED)]
        pending = [h for h in hyps.values() if h.get("state") == "AWAITING_OWNER"]
        return {
            "schema_version": 1,
            "journal": {"ok": verdict.ok, "entries": verdict.entries, "head": verdict.head,
                        "reason": verdict.reason or NA},
            "observations": {"total": len(obs), "failures": len(fails), "recent": obs[-20:]},
            "hypotheses": list(hyps.values()),
            "experiments": list(exps.values()),
            "results": list(results.values()),
            "limits": limits or NA,
            "cost": {"total_usd": round(cost, 6), "cap_usd": limits.get("cost_cap_usd", NA)},
            "rollback": [a for a in applies.values()],
            "refusals": refusals[-20:],
            "owner_decision_required": pending,
            "gain_claim": gain_claim(list(results.values())),
        }


def gain_claim(results: list[dict]) -> str:
    """The only sentence the UI may show about improvement. NOT_RUN unless a held-out PASS exists."""
    if any(r.get("status") == PASS for r in results):
        n = sum(1 for r in results if r.get("status") == PASS)
        return f"PASS: воспроизводимый выигрыш на отложенных задачах подтверждён независимым проверяющим ({n})"
    if not results:
        return f"{NOT_RUN}: эксперименты не запускались, улучшение не заявляется"
    return f"{NOT_RUN}: выигрыш на отложенных задачах не подтверждён, улучшение не заявляется"
