"""Review queue: wrong or invalid answers of the live system are KEPT for a human to look at. They are never turned into reference answers
automatically; references come only from the solver (dataset.py)."""
from __future__ import annotations

import json
import time
from pathlib import Path


def record(root: Path, inp: dict, output, reasons: list[str], source: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with (root / "review_queue.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.time(), "source": source, "input": inp, "output": output, "reasons": reasons, "state": "needs_review"}, ensure_ascii=False, default=str) + "\n")


def read(root: Path) -> list[dict]:
    p = Path(root) / "review_queue.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []
