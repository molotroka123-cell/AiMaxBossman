"""The ONE place for the terminal's colours and glyphs (Bossman 1.2).

Everything the human view draws takes its colour and symbol from here, so the
owner's next terminal references change this file only. Values are taken
(approximately) from the "CLI Operator" reference, docs/evo/BOSSMAN_1_2_TERMINAL.md
§3.1a: cyan headings, green "You ›" and enabled states, blue-cyan plan items,
muted grey-blue secondary text.

No colour is ever taken from model or file output: untrusted text is rendered
as plain `rich.text.Text` with one of the styles below (see console.sanitize).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

#: Style name -> rich style string. Names are used by the renderer; values are
#: the swappable part.
STYLES: dict[str, str] = {
    "title": "bold #27BBDE",           # «Bossman 1.x — CLI Operator»
    "tagline": "#8B95A5",
    "label": "#27BBDE",                # «Mode:», «Model:», section headings
    "value": "#E6EDF3",
    "value.on": "#3FEC87",             # enabled / on-demand / online
    "value.warn": "#F5B83D",           # pending / waiting / MOCK_MODEL
    "value.off": "#FF6B6B",            # stopped / disabled / error
    "border": "#3A6B8C",
    "you": "bold #15C44C",             # «You ›»
    "bossman": "bold #27BBDE",         # «Bossman ›»
    "text": "#E6EDF3",
    "muted": "#8B95A5",
    "plan.num": "#27BBDE",
    "plan.title": "bold #208CAE",
    "thinking": "italic #6B7785",
    "tool": "bold #27BBDE",
    "tool.args": "#8B95A5",
    "tool.ok": "#3FEC87",
    "tool.err": "#FF6B6B",
    "tool.pending": "#F5B83D",
    "note": "#208CAE",                 # skills / memory lines
    "approval": "bold #F5B83D",
    "error": "bold #FF6B6B",
    "ok": "bold #3FEC87",
    "diff.add": "#3FEC87",
    "diff.del": "#FF6B6B",
    "diff.hunk": "#27BBDE",
    "footer": "#8B95A5",
    "badge.mock": "bold black on #F5B83D",
}


@dataclass(frozen=True)
class Glyphs:
    you: str = "You ›"
    bossman: str = "Bossman ›"
    section: str = "›"
    tool: str = "●"
    result: str = "⎿"
    ok: str = "✓"
    fail: str = "✗"
    done: str = "●"
    running: str = "◐"
    pending: str = "○"
    check_on: str = "☑"
    check_off: str = "☐"
    thinking: str = "✻"
    note: str = "◆"
    approval: str = "⚠"
    stop: str = "■"
    rule: str = "─"
    sep: str = "│"
    ellipsis: str = "…"
    dot: str = "•"
    arrow: str = "→"
    spinner: tuple[str, ...] = field(default=("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"))
    box: str = "square"                # rich box name for the status bar


ASCII = Glyphs(you="You >", bossman="Bossman >", section=">", tool="*", result="|_", ok="+",
               fail="x", done="*", running="~", pending="o", check_on="[x]", check_off="[ ]",
               thinking="~", note="#", approval="!", stop="#", rule="-", sep="|", ellipsis="...",
               dot="-", arrow="->", spinner=("|", "/", "-", "\\"), box="ascii")

UNICODE = Glyphs()


def glyphs(*, ascii_only: bool | None = None) -> Glyphs:
    """Unicode glyphs unless the console cannot draw them (or the owner said so)."""
    if ascii_only is None:
        ascii_only = os.environ.get("BOSSMAN_TERMINAL_ASCII", "").strip() in ("1", "true", "yes")
    return ASCII if ascii_only else UNICODE


def rich_theme():
    from rich.theme import Theme
    return Theme(STYLES, inherit=True)


#: Header texts. The version in the title is the BACKEND's build identity, not
#: a constant: see human.header().
TITLE_SUFFIX = "CLI Operator"
TAGLINE = "Your AI pair programmer, on your terms."
