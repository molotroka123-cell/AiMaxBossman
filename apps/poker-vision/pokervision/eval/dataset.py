"""Labelled-frame dataset: PNG frames + DOM-derived labels written by tools/collect_poker_train.py.

Splits are by *session*, so no hand appears in two splits; ``assert_no_deal_overlap`` double-checks by (hero, board)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2

from ..adapters.base import Frame
from ..parse import parse_money


@dataclass
class Labelled:
    session: str
    row: dict
    frame: Frame

    @property
    def truth(self) -> dict: return self.row["truth"]
    @property
    def hand_key(self) -> str: return f"{self.session}#{self.row['hand_idx']}"


def load_session(root: Path, session: str, load_images: bool = True) -> list[Labelled]:
    d = Path(root) / session
    out = []
    for line in (d / "labels.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        img = cv2.imread(str(d / r["frame"])) if load_images else None
        out.append(Labelled(session, r, Frame(img, r["t_ms"], f"replay:{session}", f"{session}/{r['frame']}")))
    return out


def sessions(root: Path) -> list[str]:
    return sorted(p.name for p in Path(root).iterdir() if (p / "labels.jsonl").exists())


SPLITS = {
    # tuned/calibrated on:
    "train": ["A_tr1", "A_tr2", "A_tr3", "A_tr4"],
    # used only for threshold selection during development:
    "dev": ["A_tr5", "A_tr6"],
    # never touched during development:
    "test_in": ["A_te1", "A_te2", "A_te3"],
    "test_theme": ["B_ept", "B_wpt", "B_daily", "B_main", "B_turbo", "B_k100"],
    "test_layout": ["C_phone3x", "C_dpr2", "C_desk", "C_tab"],
    "test_theme_layout": ["C_ept_phone", "C_ept_desk"],
}


def deal_keys(items: list[Labelled]) -> set[tuple]:
    keys = set()
    for it in items:
        t = it.truth
        if t["hero_cards"]:
            keys.add((tuple(c["card"] for c in t["hero_cards"]), tuple(c["card"] for c in t["board"])))
    return keys


def assert_no_deal_overlap(a: list[Labelled], b: list[Labelled]) -> list[tuple]:
    """Return deals (hero cards + final board) that occur in both splits. Same-session hands never cross by construction."""
    ha = {k for k in deal_keys(a) if len(k[1]) >= 5}
    hb = {k for k in deal_keys(b) if len(k[1]) >= 5}
    return sorted(ha & hb)


def truth_money(text: str | None):
    return parse_money(text) if text else None
