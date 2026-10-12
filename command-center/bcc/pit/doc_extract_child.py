"""Child-process entry for the Jeff document library: parse ONE participant file and print one JSON line.

    python -X utf8 -m bcc.pit.doc_extract_child <tmpfile> <name>

stdout carries exactly one line, ``{"ok": true, "sections": [[ref, text], ...]}`` or
``{"ok": false, "code": "...", "text": "..."}``. The parent (``doc_library.extract_sections``) owns the wall-clock
limit and kills this process, and anything it started, when it overruns: a hostile office file can make the real
parser burn minutes of CPU, and a thread cannot be killed while a process can.

The output is capped here: a document longer than ``MAX_DOC_CHARS`` characters is refused instead of being printed.
Nothing about the file is logged and nothing is written to disk by this module.
"""
from __future__ import annotations

import json
import sys

from .doc_library import MAX_CHILD_OUTPUT_BYTES, LibraryError, _unreadable, parse_in_process


def _emit(payload: dict) -> int:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(raw) > MAX_CHILD_OUTPUT_BYTES:                      # cannot happen below MAX_DOC_CHARS; belt and braces
        raw = json.dumps({"ok": False, "code": "too_long",
                          "text": "Файл слишком сложный для разбора."}, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(raw + b"\n")
    sys.stdout.buffer.flush()
    return 0


def main(argv: list[str]) -> int:
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
    return _emit({"ok": True, "sections": [[ref, text] for ref, text in sections]})


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
