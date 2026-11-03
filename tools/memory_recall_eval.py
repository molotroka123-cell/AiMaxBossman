"""Offline recall eval (harness proof 03.11): does the right lesson reach the top of the facts layer for a new task?

Reads the live facts table READ-ONLY (sqlite URI mode=ro), runs FactsSnapshot.search (the code under test) for each
case and prints the rank of the expected lesson fact. Deterministic: no model involved.
usage: python tools/memory_recall_eval.py [cases.json]   (default: the three A/B cases of 03.11)"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "command-center"))

from bcc.v2.memory.lifecycle_wiring import FactsSnapshot  # noqa: E402

CASES = [
    {"id": "T1", "expect": 238, "query": "Roblox Luau, сервер. Напиши обработчик ProximityPrompt для статуи на площади: при "
     "нажатии игрок получает +35 Лисьих Очков (атрибут FoxPoints). Нужен защищённый от злоупотреблений вариант."},
    {"id": "T2", "expect": 592, "query": "Roblox: в ServerScriptService есть серверный Script EconomyService.server.luau, внутри "
     "него локальная функция pushState(player). Из другого серверного скрипта (BoothService) после перерождения игрока "
     "нужно вызвать обновление состояния."},
    {"id": "T3", "expect": 600, "query": "Roblox Luau, сервер. Колесо удачи — одна станция на карте, игроки нажимают "
     "ProximityPrompt, колесо крутится 4 секунды и даёт приз. На сервере много игроков одновременно."},
]


def load_rows(limit: int = 500) -> list[dict]:
    db = Path(os.environ["LOCALAPPDATA"]) / "Bossman" / "CommandCenter" / "bcc.db"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT id, subject, predicate, object, statement FROM facts "
                       "WHERE superseded_by IS NULL AND (invalid_at IS NULL) ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in reversed(rows)]


def main() -> int:
    cases = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")) if len(sys.argv) > 1 else CASES
    snap = FactsSnapshot(load_rows())
    out = []
    for c in cases:
        hits = snap.search(c["query"], limit=10)
        ids = [int(h["id"]) for h in hits]
        rank = ids.index(c["expect"]) + 1 if c["expect"] in ids else None
        out.append({"id": c["id"], "expect": c["expect"], "rank": rank, "top3": ids[:3]})
    print(json.dumps(out, ensure_ascii=False))
    top3 = sum(1 for o in out if o["rank"] and o["rank"] <= 3)
    print(f"expected lesson in top-3: {top3}/{len(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
