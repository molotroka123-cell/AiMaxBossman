"""Ranges are GIVEN to the model as explicit hand-class lists (a model cannot be expected to derive them): top X % of hands by heads-up
preflop equity (BotLab's table, read-only), the boundary band at weight 0.5."""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

_BOTLAB = Path(__file__).resolve().parents[2] / "poker-botlab"
if str(_BOTLAB) not in sys.path:
    sys.path.insert(0, str(_BOTLAB))
from botlab import cards as cd            # noqa: E402
from botlab import equity as eq           # noqa: E402
from botlab import evaluator as ev        # noqa: E402


@lru_cache(maxsize=None)
def range_classes(top_pct: int) -> tuple:
    """((class, weight), ...) for the top ``top_pct`` % of the 1326 combos."""
    pct = eq._percentiles()
    x = top_pct / 100.0
    out = []
    for label in cd.all_hand_classes():
        p = pct[label]
        if p <= x:
            out.append((label, 1.0 if p <= x - 0.04 else 0.5))
    return tuple(out)


def range_text(top_pct: int) -> str:
    return ",".join(l if w == 1.0 else f"{l}:{w}" for l, w in range_classes(top_pct))


def expand(classes, dead: set) -> list:
    """[(hole(c1,c2), weight)] for all combos of the classes that do not use a dead card."""
    out = []
    for label, w in classes:
        hi, lo = cd.RANKS.index(label[0]), cd.RANKS.index(label[1])
        if len(label) == 2:
            pairs = [(cd.make_card(hi, a), cd.make_card(hi, b)) for a in range(4) for b in range(a + 1, 4)]
        elif label[2] == "s":
            pairs = [(cd.make_card(hi, s), cd.make_card(lo, s)) for s in range(4)]
        else:
            pairs = [(cd.make_card(hi, a), cd.make_card(lo, b)) for a in range(4) for b in range(4) if a != b]
        for c1, c2 in pairs:
            if c1 not in dead and c2 not in dead:
                out.append(((c1, c2), w))
    return out


def strengths(combos, board) -> list:
    return [ev.evaluate(list(h) + list(board)) for h, _ in combos]
