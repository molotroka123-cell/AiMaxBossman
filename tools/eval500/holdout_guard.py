"""Holdout guard: a sealed EVAL500 scenario must never reach a coder / swarm / improver model context (TZ 2.5 M5).

`leaks_holdout(prompt)` is True when the prompt names the sealed location or contains the
input text of any holdout scenario (normalised, 8-word shingles). The holdout file is read in-process only to compare;
nothing from it is returned or printed.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from . import schema

SHINGLE = 8
PATH_MARKERS = ("eval500_holdout", "eval\\heldout", "eval/heldout")


def _shingles(text: str) -> set[str]:
    words = schema.norm_text(text).split()
    if len(words) < SHINGLE:
        return {" ".join(words)} if len(words) >= 4 else set()
    return {" ".join(words[i:i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)}


@lru_cache(maxsize=4)
def _holdout_shingles(path: str, mtime: float) -> frozenset[str]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    out: set[str] = set()
    for s in doc.get("scenarios", []):
        for t in s.get("turns", []):
            if isinstance(t, dict) and t.get("text"):
                out |= _shingles(t["text"])
    return frozenset(out)


def leaks_holdout(prompt: str, holdout_file: Path | None = None) -> bool:
    low = prompt.casefold()
    if any(m in low for m in PATH_MARKERS):
        return True
    path = Path(holdout_file or schema.HOLDOUT_FILE)
    if not path.is_file():
        return False
    sealed = _holdout_shingles(str(path), path.stat().st_mtime)
    if not sealed:
        return False
    return bool(_shingles(prompt) & sealed)


def assert_no_holdout(prompt: str, holdout_file: Path | None = None) -> None:
    if leaks_holdout(prompt, holdout_file):
        raise SystemExit("EVAL500 holdout text or location in the prompt: refused (sealed holdout, TZ 2.5 M5)")
