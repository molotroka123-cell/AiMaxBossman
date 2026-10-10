"""Preflop decisions from the OWNER'S OWN 6-max cash charts (copied from his Poker Train repo, see data/pokertrain_cash6max_preflop.json).

These are reference ranges (the trainer's comment says GTO Wizard, 6-max cash 100bb); Bossman did not solve them. The chart answers only the
spots it covers and says why when it does not:

* unopened pot (to call <= 1 bb, nobody raised) -> RFI chart for the hero's position;
* BB facing a single open, SB vs BTN, others facing a single open -> 3-bet / call / fold table for the opener's position. The opener's
  position is NOT read from the screen, so the TIGHTEST table (vs UTG) is used and that is said in the reason;
* a bigger price (3-bet and above), unknown position, or a non-preflop street -> not covered (None): the caller uses its own fallback.

Input is visible information only: hero cards, position badge, to-call, pot, big blind of the table the bot itself opened."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

RANKS = "23456789TJQKA"  # ci-secret-scan: allow (file name, not a secret)
DATA = Path(__file__).with_name("data") / "pokertrain_cash6max_preflop.json"
POSITIONS = ("UTG", "HJ", "CO", "BTN", "SB", "BB")
POSITION_ALIASES = {"UTG+1": "HJ", "MP": "HJ", "LJ": "UTG"}      # 7-9 handed labels mapped onto the 6-max chart (said in the reason)


def _r(c: str) -> int:
    return RANKS.index(c)


def _cls(hi: int, lo: int, kind: str) -> str:
    return RANKS[hi] + RANKS[lo] + ("" if kind == "p" else kind)


def parse_range(text: str) -> frozenset:
    """'22+, A2s+, K9s+, ATo+, 22-JJ, A5s-A2s, AKo' -> set of 169-class labels ('AA', 'AKs', 'AKo'). Raises ValueError on bad tokens."""
    out: set[str] = set()
    for tok in (t.strip() for t in text.split(",")):
        if not tok:
            continue
        plus = tok.endswith("+")
        core = tok[:-1] if plus else tok
        if "-" in core:
            a, b = core.split("-")
            if len(a) == 2 and len(b) == 2 and a[0] == a[1] and b[0] == b[1]:
                lo_, hi_ = sorted((_r(a[0]), _r(b[0])))
                out.update(_cls(r, r, "p") for r in range(lo_, hi_ + 1))
                continue
            if len(a) == 3 and len(b) == 3 and a[0] == b[0] and a[2] == b[2]:
                lo_, hi_ = sorted((_r(a[1]), _r(b[1])))
                out.update(_cls(_r(a[0]), k, a[2]) for k in range(lo_, hi_ + 1))
                continue
            raise ValueError(f"bad range token {tok!r}")
        if len(core) == 2 and core[0] == core[1] and core[0] in RANKS:
            r = _r(core[0])
            out.update(_cls(k, k, "p") for k in (range(r, 13) if plus else (r,)))
            continue
        if len(core) == 3 and core[0] in RANKS and core[1] in RANKS and core[2] in "so" and _r(core[0]) > _r(core[1]):
            hi, lo = _r(core[0]), _r(core[1])
            out.update(_cls(hi, k, core[2]) for k in (range(lo, hi) if plus else (lo,)))
            continue
        raise ValueError(f"bad range token {tok!r}")
    return frozenset(out)


def hand_class(c1: str, c2: str) -> str:
    """'As','Kd' -> 'AKo'; 'Th','Ts' -> 'TT'."""
    r1, r2 = _r(c1[0]), _r(c2[0])
    if r1 == r2:
        return RANKS[r1] * 2
    hi, lo = max(r1, r2), min(r1, r2)
    return RANKS[hi] + RANKS[lo] + ("s" if c1[1] == c2[1] else "o")


def combos(classes) -> int:
    return sum(6 if len(c) == 2 else 4 if c[2] == "s" else 12 for c in classes)


@lru_cache(maxsize=1)
def chart() -> dict:
    d = json.loads(DATA.read_text(encoding="utf-8"))
    parsed = {"source": d["source"], "rfi": {}, "vs_open": {}, "bb_defense": {}}
    for pos, v in d["rfi"].items():
        parsed["rfi"][pos] = {"raise": parse_range(v["raise"]), "open_bb": float(v["open_bb"])}
    for sect in ("vs_open", "bb_defense"):
        for k, v in d[sect].items():
            parsed[sect][k] = {"threebet": parse_range(v["threebet"]), "call": parse_range(v["call"])}
    return parsed


@dataclass(frozen=True)
class ChartAction:
    action: str               # FOLD | CHECK | CALL | RAISE
    raise_to_bb: float | None
    reason: str
    table: str                # which chart row answered


def decide(hero: tuple, position: str | None, to_call: float, pot: float, bb: float, posted: float | None = None) -> ChartAction | None:
    """Chart decision or None (not covered). Amounts in chips; ``bb`` is the big blind of the table the bot opened (a known setting).
    ``posted``: what the hero already has in front this street (blinds) when known; default from the position."""
    if not hero or len(hero) != 2 or not all(hero) or position is None or bb <= 0:
        return None
    pos = POSITION_ALIASES.get(position, position)
    note = f" (метка {position} → {pos})" if pos != position else ""
    if pos not in POSITIONS:
        return None
    hc = hand_class(*hero)
    c = chart()
    if posted is None:
        posted = {"SB": 0.5 * bb, "BB": bb}.get(pos, 0.0)
    eps = 0.01 * bb
    facing_bet = to_call + posted                       # the size of the current bet the hero faces
    if pos == "BB" and to_call <= eps:
        return ChartAction("CHECK", None, f"BB, никто не повысил: чек бесплатно{note}", "bb_option")
    if pos != "BB" and facing_bet <= bb + eps:        # nobody raised (limpers, if any, only add to the pot)
        if pot > 1.5 * bb + eps:
            note += "; в банке есть лимперы — таблица открытия применена как изоляция"
        row = c["rfi"][pos]
        if hc in row["raise"]:
            return ChartAction("RAISE", row["open_bb"], f"{hc} входит в диапазон открытия {pos} ({row['open_bb']:g}bb){note}", f"rfi.{pos}")
        return ChartAction("FOLD", None, f"{hc} не входит в диапазон открытия {pos}{note}", f"rfi.{pos}")
    # facing a single open: price between 1 bb and ~5 bb (a 3-bet or more is not covered by these charts)
    if bb + eps < facing_bet <= 5.0 * bb:
        if pos == "BB":
            key, sect = "vs_UTG", "bb_defense"
        elif pos == "SB":
            key, sect = "sb_vs_btn", "vs_open"           # the most frequent single-raise spot for SB; still flagged below
        else:
            key, sect = "vs_UTG", "vs_open"
        row = c[sect][key]
        assumed = "позиция открывшего НЕ прочитана — взята самая тугая таблица" if key == "vs_UTG" else "позиция открывшего НЕ прочитана — взята таблица SB против BTN"
        if hc in row["threebet"]:
            mult = 3.0 if pos in ("CO", "BTN") else 3.5
            return ChartAction("RAISE", round(mult * facing_bet / bb, 2), f"{hc}: 3-бет по таблице {sect}.{key}; {assumed}{note}", f"{sect}.{key}")
        if hc in row["call"]:
            return ChartAction("CALL", None, f"{hc}: колл по таблице {sect}.{key}; {assumed}{note}", f"{sect}.{key}")
        return ChartAction("FOLD", None, f"{hc}: нет в таблице {sect}.{key} (ни 3-бет, ни колл); {assumed}{note}", f"{sect}.{key}")
    return None
