"""Child-process entry for the Jeff document library: parse ONE participant file and print one JSON line.

    python -X utf8 -m bcc.pit.doc_extract_child <tmpfile> <name>

stdout carries exactly one line, ``{"ok": true, "sections": [[ref, text], ...]}`` (plus ``"partial": {...}`` when the
parser kept only the first part of the file) or ``{"ok": false, "code": "...", "text": "..."}``. The parent
(``doc_library.extract_sections``) owns the wall-clock limit and kills this process, and anything it started, when it
overruns: a hostile office file can make the real parser burn minutes of CPU, and a thread cannot be killed while a
process can.

Before it touches the file the child puts ceilings on itself (``doc_library.apply_child_limits``: committed memory, CPU
time, die-with-parent) and arms a watchdog that ends the process after CHILD_WATCHDOG_SECONDS: if the parent died
(crash, hard stop) nobody is left to kill this process, and it must not keep parsing for ever.

The output is capped here: a document longer than ``MAX_DOC_CHARS`` characters is refused instead of being printed.
Nothing about the file is logged and nothing is written to disk by this module.
"""
from __future__ import annotations

import json
import os
import sys
import threading

from .doc_library import (CHILD_WATCHDOG_SECONDS, MAX_CHILD_OUTPUT_BYTES, LibraryError, _unreadable,
                          apply_child_limits, parse_in_process)


def _emit(payload: dict) -> int:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(raw) > MAX_CHILD_OUTPUT_BYTES:                      # cannot happen below MAX_DOC_CHARS; belt and braces
        raw = json.dumps({"ok": False, "code": "too_long",
                          "text": "Файл слишком сложный для разбора."}, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(raw + b"\n")
    sys.stdout.buffer.flush()
    return 0


def _arm_watchdog(seconds: float) -> threading.Timer:
    """End this process after ``seconds`` whatever it is doing (``os._exit``: no cleanup, no output)."""
    timer = threading.Timer(seconds, lambda: os._exit(124))
    timer.daemon = True
    timer.start()
    return timer


def main(argv: list[str]) -> int:
    _arm_watchdog(CHILD_WATCHDOG_SECONDS)
    apply_child_limits()                    # a ceiling that is not available is not a reason to refuse the file
    if len(argv) < 3:
        return _emit({"ok": False, "code": "unreadable", "text": _unreadable().text})
    path, name = argv[1], argv[2]
    try:
        sections = parse_in_process(path, name)
    except LibraryError as exc:
        return _emit({"ok": False, "code": exc.code, "text": exc.text})
    except (MemoryError, RecursionError):
        return _emit({"ok": False, "code": "too_long", "text": "Файл слишком сложный для разбора."})
    except Exception:  # noqa: BLE001 - whatever the parsers do, the parent gets a refusal, never a traceback
        return _emit({"ok": False, "code": "unreadable", "text": _unreadable().text})
    reply: dict = {"ok": True, "sections": [[ref, text] for ref, text in sections]}
    partial = getattr(sections, "partial", None)
    if isinstance(partial, dict):
        reply["partial"] = partial
    return _emit(reply)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
