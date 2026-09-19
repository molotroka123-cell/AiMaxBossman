#!/usr/bin/env python3
"""Матрица возможностей в читаемом виде — проекция, а не второй источник.

Источник истины один: `docs/v8/CAPABILITY_MATRIX.json`. Здесь он только
рисуется. Второй список, который пишут руками, расходится с первым на первой
же правке, и расходится молча.

    python tools/render_capability_matrix.py           # перерисовать
    python tools/render_capability_matrix.py --check   # CI: совпадает ли
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "v8" / "CAPABILITY_MATRIX.json"
TARGET = ROOT / "docs" / "v8" / "CAPABILITY_MATRIX.md"

ORDER = ("IMPLEMENTED_AND_TESTED", "IMPLEMENTED_LIVE_PENDING",
         "OWNER_REQUIRED", "NOT_IMPLEMENTED")
HEADING = {
    "IMPLEMENTED_AND_TESTED": "Написано и проверено прогоном",
    "IMPLEMENTED_LIVE_PENDING": "Написано, но на живом маршруте не прогонялось",
    "OWNER_REQUIRED": "Требует того, чего у агента нет",
    "NOT_IMPLEMENTED": "Не написано — сказано прямо",
}


def render(data: dict) -> str:
    rows = data["capabilities"]
    lines = ["# Матрица возможностей V8",
             "",
             "Этот файл НЕ редактируется руками: он рисуется из",
             "`CAPABILITY_MATRIX.json` командой",
             "`python tools/render_capability_matrix.py`. Правка здесь потеряется.",
             "",
             data["purpose"],
             "",
             "## Правила, по которым строки получают состояние",
             ""]
    lines += [f"* {rule}" for rule in data["rules"]]
    lines += ["", "## Счёт", "",
              "| Состояние | Строк |", "|---|---:|"]
    for state in ORDER:
        lines.append(f"| `{state}` | {sum(1 for r in rows if r['state'] == state)} |")
    lines.append(f"| **всего** | **{len(rows)}** |")

    for state in ORDER:
        block = [r for r in rows if r["state"] == state]
        if not block:
            continue
        lines += ["", f"## {HEADING[state]} — `{state}`", "",
                  f"*{data['states'][state]}*", ""]
        for row in block:
            lines.append(f"### {row['title']}")
            lines.append("")
            lines.append("* **Улики:** " + ", ".join(f"`{e}`" for e in row["evidence"]))
            if row.get("owner_supplies"):
                lines.append(f"* **Владелец предоставляет:** {row['owner_supplies']}")
            if row.get("measurement"):
                lines.append(f"* **Замер:** `{row['measurement']}`")
            if row.get("note"):
                lines.append(f"* {row['note']}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    body = render(json.loads(SOURCE.read_text(encoding="utf-8")))
    if args.check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != body:
            print("CAPABILITY_MATRIX_CURRENT=FAIL "
                  "(перерисуйте: python tools/render_capability_matrix.py)")
            return 1
        print("CAPABILITY_MATRIX_CURRENT=PASS")
        return 0
    TARGET.write_text(body, encoding="utf-8")
    print(f"CAPABILITY_MATRIX_WRITTEN={TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
