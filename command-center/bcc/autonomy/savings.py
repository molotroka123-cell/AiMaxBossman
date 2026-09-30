"""Savings ledger: how much Claude/Codex subscription work the free writer route avoided.

One JSONL row per finished writer run (`<root>/savings/ledger.jsonl`): goal, risk
tier, writer (claude | codex | nemotron), turns, tokens in/out, USD cost (0 on the
free route), model/provider. For a nemotron row the ledger also stores the
ESTIMATED Claude/Codex writer work it replaced.

Estimate method (versioned, recorded on every row):

* ``median_same_tier/v1`` - the median turns / tokens of the recorded Claude and
  Codex writer runs for the same risk tier, when there are at least
  ``MIN_SAMPLES`` of them;
* ``default/v1`` - otherwise the documented defaults below (one writer turn,
  DEFAULT_TOKENS_IN / DEFAULT_TOKENS_OUT), which are a conservative guess for a
  single-file edit, not a measurement.

The "subscription-limit share saved" in the report is
turns_avoided / (turns_avoided + actual Claude/Codex writer turns), i.e. the share
of writer turns that would otherwise have been spent on the subscriptions.

    python -m bcc.autonomy.savings report [--root DIR]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

ESTIMATE_VERSION = "v1"
MIN_SAMPLES = 3
DEFAULT_TURNS = 1
DEFAULT_TOKENS_IN = 30_000
DEFAULT_TOKENS_OUT = 3_000
PAID_WRITERS = ("claude", "codex")


@dataclass
class WriterRun:
    goal_id: str
    risk_tier: str
    writer: str
    turns: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    model: str = ""
    provider: str = ""
    ts: float = 0.0
    avoided_turns: float = 0.0
    avoided_tokens_in: float = 0.0
    avoided_tokens_out: float = 0.0
    estimate_method: str = ""


class SavingsLedger:
    def __init__(self, root: Path, *, clock=time.time):
        self.path = Path(root) / "savings" / "ledger.jsonl"
        self.clock = clock

    def rows(self) -> list[WriterRun]:
        if not self.path.is_file():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(WriterRun(**json.loads(line)))
            except (ValueError, TypeError):
                continue
        return out

    def estimate(self, risk_tier: str) -> dict:
        paid = [r for r in self.rows() if r.writer in PAID_WRITERS and r.risk_tier == risk_tier]
        if len(paid) >= MIN_SAMPLES:
            return {"turns": float(statistics.median(r.turns for r in paid)),
                    "tokens_in": float(statistics.median(r.tokens_in for r in paid)),
                    "tokens_out": float(statistics.median(r.tokens_out for r in paid)),
                    "method": f"median_same_tier/{ESTIMATE_VERSION}", "samples": len(paid)}
        return {"turns": float(DEFAULT_TURNS), "tokens_in": float(DEFAULT_TOKENS_IN),
                "tokens_out": float(DEFAULT_TOKENS_OUT), "method": f"default/{ESTIMATE_VERSION}",
                "samples": len(paid)}

    def record(self, *, goal_id: str, risk_tier: str, writer: str, turns: int, tokens_in: int, tokens_out: int,
               cost_usd: float = 0.0, model: str = "", provider: str = "") -> WriterRun:
        row = WriterRun(goal_id=goal_id, risk_tier=risk_tier, writer=writer, turns=int(turns),
                        tokens_in=int(tokens_in), tokens_out=int(tokens_out),
                        cost_usd=0.0 if writer == "nemotron" else float(cost_usd), model=model,
                        provider=provider, ts=self.clock())
        if writer not in PAID_WRITERS:
            est = self.estimate(risk_tier)
            row.avoided_turns, row.avoided_tokens_in = est["turns"], est["tokens_in"]
            row.avoided_tokens_out, row.estimate_method = est["tokens_out"], est["method"]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(asdict(row), ensure_ascii=False, sort_keys=True) + "\n")
        return row

    def report(self) -> dict:
        rows = self.rows()
        by_writer: dict[str, dict] = {}
        for r in rows:
            w = by_writer.setdefault(r.writer, {"tasks": 0, "turns": 0, "tokens_in": 0, "tokens_out": 0,
                                                "cost_usd": 0.0})
            w["tasks"] += 1
            w["turns"] += r.turns
            w["tokens_in"] += r.tokens_in
            w["tokens_out"] += r.tokens_out
            w["cost_usd"] = round(w["cost_usd"] + r.cost_usd, 6)
        saved_turns = sum(r.avoided_turns for r in rows)
        saved_in = sum(r.avoided_tokens_in for r in rows)
        saved_out = sum(r.avoided_tokens_out for r in rows)
        paid_turns = sum(r.turns for r in rows if r.writer in PAID_WRITERS)
        denom = saved_turns + paid_turns
        return {"schema": "bossman.autonomy.savings/1", "estimate_version": ESTIMATE_VERSION,
                "tasks": len(rows), "by_writer": by_writer, "turns_saved": round(saved_turns, 2),
                "tokens_saved_in": round(saved_in), "tokens_saved_out": round(saved_out),
                "subscription_share_saved": round(saved_turns / denom, 4) if denom else None,
                "methods": sorted({r.estimate_method for r in rows if r.estimate_method})}


def render_ru(rep: dict) -> str:
    lines = ["Экономия подписок Claude/Codex (оценка, метод " + rep["estimate_version"] + ")",
             f"{'исполнитель':<12} {'задач':>6} {'ходов':>6} {'токены вх/вых':>20} {'USD':>8}"]
    for writer, w in sorted(rep["by_writer"].items()):
        lines.append(f"{writer:<12} {w['tasks']:>6} {w['turns']:>6} "
                     f"{str(w['tokens_in']) + '/' + str(w['tokens_out']):>20} {w['cost_usd']:>8.2f}")
    share = rep["subscription_share_saved"]
    lines += [f"сэкономлено ходов writer: {rep['turns_saved']}",
              f"сэкономлено токенов: {rep['tokens_saved_in']} вх / {rep['tokens_saved_out']} вых",
              "доля лимита подписок, сэкономленная бесплатным маршрутом: "
              + ("нет данных" if share is None else f"{share * 100:.1f}%")]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bcc.autonomy.savings")
    p.add_argument("command", choices=["report"])
    p.add_argument("--root", default=None, help="autonomy data root (default: BOSSMAN_AUTONOMY_ROOT or "
                                                 "<BCC_DATA_DIR>/autonomy)")
    args = p.parse_args(argv)
    from .cycle import default_root
    rep = SavingsLedger(Path(args.root) if args.root else default_root()).report()
    sys.stdout.write(json.dumps(rep, ensure_ascii=False, indent=2, sort_keys=True) + "\n\n" + render_ru(rep) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
