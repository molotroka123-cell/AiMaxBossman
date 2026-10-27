"""Text colours of the chat additions (19.10: typing label, copy/regenerate, «вниз», photo-prompt card) have a WCAG
contrast of at least 4.5:1 in BOTH themes, read from the real tokens in app.css (light :root and the dark block)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

CSS = (Path(__file__).resolve().parents[1] / "bossmobile" / "ui" / "app.css").read_text(encoding="utf-8")


def tokens(block: str) -> dict[str, str]:
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})", block))


LIGHT = tokens(CSS.split("@media (prefers-color-scheme: dark)")[0])
DARK = {**LIGHT, **tokens(CSS.split("@media (prefers-color-scheme: dark)")[1].split("}\n}")[0])}


def lum(hex_: str) -> float:
    c = [int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def ratio(a: str, b: str) -> float:
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


PAIRS = [  # (what, text token, background token, minimum)
    ("typing label", "muted", "surface2", 4.5), ("copy / regenerate", "muted", "bg", 4.5),
    ("«вниз»", "text", "surface", 4.5), ("prompt text", "text", "bg2", 4.5), ("card note/status", "muted", "surface", 4.5),
    ("card error", "err", "surface", 4.5), ("card hint", "faint", "surface", 4.5), ("«на ПК» badge", "local", "local-bg", 4.5),
    ("«Запустить» (.btn.primary)", "accent-ink", "accent-strong", 4.5), ("«Продолжить» (.stop-resume)", "ok-ink", "ok", 4.5),
    ("photo-mode icon on", "accent", "accent-soft", 3.0),
]


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_new_chat_colours_meet_contrast(theme):
    t = LIGHT if theme == "light" else DARK
    low = [(what, round(ratio(t[fg], t[bg]), 2)) for what, fg, bg, need in PAIRS if ratio(t[fg], t[bg]) < need]
    assert not low, f"{theme}: {low}"


def test_dark_tokens_really_differ():
    assert DARK["bg"] != LIGHT["bg"] and DARK["text"] != LIGHT["text"]
