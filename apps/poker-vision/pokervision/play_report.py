"""Grading and annotated screenshots for a play session in the owner's own trainer.

``grade`` compares what the bot READ (the committed state it decided on) with the trainer's own DOM state captured at the same moment.
The DOM state is used ONLY here, for scoring; the policy and the executor never see it. ``annotate`` draws what the bot saw: boxes on
recognised cards / numbers / buttons, the read state, the decision with its source and reason, and the click point(s)."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .parse import parse_money

FIELDS = ("hero_cards", "board", "pot", "to_call", "hero_stack", "position", "actions")
FONT_CANDIDATES = ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


def _money(text) -> float | None:
    m = parse_money(str(text)) if text not in (None, "") else None
    return m.amount if m else None


def truth_view(t: dict) -> dict:
    """The trainer's DOM state (tools/truth.js output) in the same shape as a committed state."""
    btn = {b["label"]: b.get("amount") for b in t.get("buttons", [])}
    acts = [lab for lab in ("FOLD", "CHECK", "CALL", "RAISE", "ALL IN", "CONFIRM") if lab in btn]
    to_call = 0.0 if "CHECK" in btn else (btn.get("CALL") if btn.get("CALL") is not None else _money(t.get("to_call_text")))
    return {"hero_cards": [c["card"] for c in t.get("hero_cards", [])], "board": [c["card"] for c in t.get("board", [])],
            "pot": _money(t.get("pot_text")), "to_call": to_call, "hero_stack": _money(t.get("hero_stack_text")),
            "position": t.get("position"), "actions": acts, "hand": t.get("hand_header")}


def committed_view(cm) -> dict:
    acts = [a[0] for a in (cm.actions or [])] if cm.actions is not None else None
    amounts = {a[0]: a[1] for a in (cm.actions or [])}
    to_call = 0.0 if "CHECK" in amounts else amounts.get("CALL")
    return {"hero_cards": list(cm.hero_cards), "board": [c for c in cm.board], "pot": cm.pot.amount if cm.pot else None,
            "to_call": to_call, "hero_stack": cm.hero_stack.amount if cm.hero_stack else None, "position": cm.hero_position, "actions": acts}


def grade(read: dict, truth: dict) -> dict:
    """field -> 'ok' | 'wrong' | 'unknown' (the bot said it does not know) | 'na' (no truth to compare)."""
    out = {}
    for f in FIELDS:
        r, t = read.get(f), truth.get(f)
        if t is None or (f in ("hero_cards",) and len(t) != 2):
            out[f] = "na"
        elif r is None or (isinstance(r, list) and f in ("hero_cards", "board") and any(x is None for x in r)):
            out[f] = "unknown"
        elif f in ("pot", "to_call", "hero_stack"):
            out[f] = "ok" if abs(float(r) - float(t)) <= 0.5 else "wrong"
        elif f == "actions":
            out[f] = "ok" if sorted(r) == sorted(t) else "wrong"
        else:
            out[f] = "ok" if r == t else "wrong"
    return out


def _font(size: int):
    from PIL import ImageFont
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def annotate(bgr: np.ndarray, boxes: list, lines: list[tuple[str, str]], clicks: list[tuple[float, float, str]], panel_w: int = 460) -> np.ndarray:
    """Frame + side panel. ``boxes``: adapter quality boxes (x,y,w,h,field,label,ok); ``lines``: (text, colour) for the panel;
    ``clicks``: (x, y, label) click points in frame pixels."""
    from PIL import Image, ImageDraw
    img = bgr.copy()
    for b in boxes:
        col = (60, 200, 60) if b.get("ok") else (40, 40, 230)
        x, y, w, h = int(b["x"]), int(b["y"]), int(b["w"]), int(b["h"])
        cv2.rectangle(img, (x, y), (x + w, y + h), col, 2)
    for (cx, cy, lab) in clicks:
        cx, cy = int(cx), int(cy)
        cv2.circle(img, (cx, cy), 14, (0, 215, 255), 3)
        cv2.line(img, (cx - 22, cy), (cx + 22, cy), (0, 215, 255), 2)
        cv2.line(img, (cx, cy - 22), (cx, cy + 22), (0, 215, 255), 2)
    canvas = np.full((max(img.shape[0], 200), img.shape[1] + panel_w, 3), 24, np.uint8)
    canvas[:img.shape[0], :img.shape[1]] = img
    pil = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB))
    dr = ImageDraw.Draw(pil)
    small, big = _font(14), _font(17)
    for b in boxes:                                                       # labels next to the boxes (Unicode-safe)
        lab = str(b.get("label", ""))[:18]
        dr.text((int(b["x"]), max(int(b["y"]) - 16, 0)), lab, fill=(120, 230, 120) if b.get("ok") else (255, 110, 110), font=small)
    for i, (cx, cy, lab) in enumerate(clicks):                               # numbered: RAISE -> PRESET -> CONFIRM can share a spot
        dr.text((max(int(cx) - 150, 4), int(cy) - 44 - 20 * i), f"{i + 1}. CLICK {lab}", fill=(255, 215, 0), font=big)
    x0, y = img.shape[1] + 14, 12
    colours = {"h": (255, 215, 0), "ok": (140, 235, 140), "bad": (255, 120, 120), "": (225, 225, 225), "dim": (160, 160, 160)}
    for text, c in lines:
        for chunk in _wrap(text, 52):
            dr.text((x0, y), chunk, fill=colours.get(c, colours[""]), font=big if c == "h" else small)
            y += 22 if c == "h" else 18
        if y > canvas.shape[0] - 20:
            break
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def _wrap(text: str, n: int) -> list[str]:
    words, out, cur = str(text).split(" "), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > n and cur:
            out.append(cur); cur = w
        else:
            cur = (cur + " " + w).strip()
    return out + ([cur] if cur else [])


def summarize(rows: list[dict]) -> dict:
    """Per-field reading accuracy over all decisions: ok / wrong / unknown / na and the ok-rate among answered."""
    tot = {f: {"ok": 0, "wrong": 0, "unknown": 0, "na": 0} for f in FIELDS}
    for r in rows:
        for f, v in (r.get("grade") or {}).items():
            tot[f][v] += 1
    for f, d in tot.items():
        ans = d["ok"] + d["wrong"]
        d["acc_answered"] = round(d["ok"] / ans, 4) if ans else None
    return tot
