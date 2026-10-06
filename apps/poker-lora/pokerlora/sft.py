"""Supervised fine-tuning data. TRAIN split only (the validation split is for early stopping, the test split is never written here).
The assistant answer carries the action, the size, the equilibrium PROBABILITIES (mixed strategies are not collapsed to one 'correct' move) and
the templated short explanation. EV tables, equity and anything solver-internal stay out of the prompt and out of the answer."""
from __future__ import annotations

import json
from pathlib import Path

from .dataset import load
from .openai_policy import SYSTEM
from .validate import validate_input


def to_chat(ex: dict) -> dict:
    assert not validate_input(ex["input"])
    ref = ex["reference"]
    answer = {"action": ref["action"], "size": ref["size"], "probs": {a: p for a, p in ref["probs"].items() if p >= 0.005}, "explanation": ref["explanation"]}
    return {"id": ex["id"], "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps(ex["input"], ensure_ascii=False)},
                                         {"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)}]}


def write(dataset_dir: Path, out_dir: Path, splits=("train", "val")) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    n = {}
    for s in splits:
        if s == "test":
            raise ValueError("the test split is never exported for training")
        rows = [to_chat(e) for e in load(dataset_dir, s)]
        with (out_dir / f"sft_{s}.jsonl").open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        n[s] = len(rows)
    return n
