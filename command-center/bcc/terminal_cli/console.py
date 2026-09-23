"""Console plumbing: UTF-8 streams, Windows VT mode, colour decision and the
neutralisation of untrusted text (model output, tool output, file content).

The renderer is the only thing allowed to produce terminal control sequences.
Text that came from a model, a tool or a file may contain its own — colour
codes, cursor moves, an OSC 52 clipboard write, a window-title change, a
carriage return that overwrites the line it is on, bidi overrides that make a
line read differently than it is. `sanitize()` turns all of that into inert,
visible text before anything is printed, in the human view AND in JSON.
"""
from __future__ import annotations

import os
import re
import sys

# ESC-sequences: CSI (ESC [ … final), OSC (ESC ] … BEL | ESC \), DCS/SOS/PM/APC
# (ESC P|X|^|_ … ESC \), and any other two-byte ESC sequence.
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?")
_STRING_SEQ = re.compile(r"\x1b[PX^_][^\x1b]*(?:\x1b\\)?")
_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]?")
_ESC_OTHER = re.compile(r"\x1b[@-Z\\-_]?")
# 8-bit C1 controls (0x80-0x9F): 0x9B is a one-byte CSI, 0x9D a one-byte OSC.
_C1 = re.compile(r"[\x80-\x9f]")
# C0 controls except TAB and LF (CR is handled first, see below).
_C0 = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Bidi embeddings/overrides/isolates: a line must read as it is stored.
_BIDI = re.compile("[\u202a-\u202e\u2066-\u2069\u200e\u200f\u061c]")


def sanitize(text: object, *, keep_newlines: bool = True) -> str:
    """Untrusted text -> inert text. Idempotent. Never raises."""
    if text is None:
        return ""
    s = text if isinstance(text, str) else str(text)
    if not s:
        return s
    s = _OSC.sub("", s)
    s = _STRING_SEQ.sub("", s)
    s = _CSI.sub("", s)
    s = _ESC_OTHER.sub("", s)
    s = _C1.sub("", s)
    # CR: "\r\n" is a line end; a lone "\r" would overwrite what is printed
    # before it, so it becomes a visible line break instead.
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = _C0.sub("", s)
    s = _BIDI.sub("", s)
    if not keep_newlines:
        s = s.replace("\n", " ")
    return s


def utf8_console() -> None:
    """`-I` (the bundle's launchers) ignores PYTHONUTF8/PYTHONIOENCODING: on a
    cp1251/cp1252 Windows console the first Cyrillic line would crash. Same
    helper as the shipped runners (tests/test_shipped_runners_console.py)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def enable_windows_vt() -> bool:
    """Windows: switch the console to UTF-8 and turn on VT processing, so
    colours are escape sequences the console understands (cmd/ConHost,
    Windows Terminal, PowerShell). Returns True when VT is on. No-op elsewhere."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleOutputCP(65001)
        kernel32.SetConsoleCP(65001)
        handle = kernel32.GetStdHandle(-11)            # STD_OUTPUT_HANDLE
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False                               # not a console (redirected)
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        if mode.value & ENABLE_VIRTUAL_TERMINAL_PROCESSING:
            return True
        return bool(kernel32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING))
    except Exception:  # noqa: BLE001 — старый ConHost: без цвета, но без падения
        return False


def stdout_is_tty(stream=None) -> bool:
    stream = stream or sys.stdout
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def color_allowed(stream=None) -> bool:
    """Colour only on a real terminal and only when NO_COLOR is not set
    (https://no-color.org: any non-empty value disables colour)."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("BOSSMAN_TERMINAL_PLAIN", "").strip() in ("1", "true", "yes"):
        return False
    return stdout_is_tty(stream)


def make_console(*, stream=None, plain: bool | None = None, width: int | None = None,
                 record: bool = False):
    """The one rich Console the human view prints through.

    `plain=True` (not a TTY, NO_COLOR, --plain): no colour, no box drawing, no
    cursor movement — the output of a redirected `bossman` is ordinary text.
    Markup and emoji parsing are OFF for everything: our own text is built
    from rich `Text` objects, and untrusted text can never become markup."""
    from rich.console import Console
    from .theme import rich_theme
    stream = stream or sys.stdout
    if plain is None:
        plain = not color_allowed(stream)
    if not plain:
        enable_windows_vt()
    return Console(file=stream, theme=rich_theme(), markup=False, emoji=False, highlight=False,
                   force_terminal=not plain, no_color=plain, color_system=None if plain else "truecolor"
                   if _truecolor() else "256", width=width, soft_wrap=False, record=record,
                   legacy_windows=False)


def _truecolor() -> bool:
    forced = os.environ.get("BOSSMAN_TERMINAL_COLORS", "").strip().lower()
    if forced in ("256", "16"):
        return False
    if forced in ("truecolor", "24bit"):
        return True
    # Windows Terminal and modern ConHost render 24-bit colour; so do most
    # Linux terminals that announce it.
    return (os.name == "nt" or bool(os.environ.get("WT_SESSION"))
            or os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit"))
