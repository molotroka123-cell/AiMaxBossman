"""Write evidence/mediaux-classification.md from the receipts (no judgement beyond the TOP/LOW sets below)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mediaux_probe as mp  # noqa: E402

EVID = mp.EVID
TOP = {"cap-12": "чат и история владельца: основной канал управления",
       "cap-13": "CMD: терминальный клиент Bossman",
       "slash-evolve": "управление циклом самоулучшения",
       "slash-stop": "глобальный STOP и остановка задач (надёжность)",
       "slash-approve": "одобрения: ни одно действие без решения владельца",
       "slash-deny": "одобрения: отказ без побочных эффектов",
       "slash-memory": "поиск по памяти и фактам",
       "slash-models": "выбор модели агента (маршрутизация бесплатных моделей)",
       "slash-keys": "ключи: секрет не принимается из командной строки",
       "reg-studio_governance": "бюджетные резервы Studio (жёсткий потолок)"}
LOW = {"slash-clear", "slash-expand", "slash-history", "slash-exit", "slash-help", "reg-motion_epic",
       "mod-game_bootstrap_v16", "reg-promo_video", "reg-motion_make_video"}
KEEP_REASON = {"reg-motion_make_video": ("нужна среда: tools/motion_studio/make_video.py импортирует score (scipy), "
                                         "scipy не установлен в Python312 линии; модуль живой (запускается "
                                         "features/motion_studio.py), удалять нельзя, тестов без scipy нет")}


def main():
    seed = json.loads(mp.SEED.read_text(encoding="utf-8"))
    label = {n["id"]: n["label"] for n in seed["nodes"]}
    rec = {r["node_id"]: r for r in json.loads((EVID / "mediaux.json").read_text(encoding="utf-8"))}
    ret = {r["node_id"]: r for r in json.loads((EVID / "mediaux-retire.json").read_text(encoding="utf-8"))}
    rows, counts = [], {"GREEN": 0, "RETIRE": 0, "KEEP": 0}
    ids = [n["id"] for n in mp.leaves()] + [i for i in ret if i not in {n["id"] for n in mp.leaves()}]
    for nid in ids:
        if nid in ret and ret[nid]["verdict"] == "RETIRE":
            verdict, reason = "RETIRE", ret[nid]["reason"]
        elif nid in rec and rec[nid]["verdict"] == "PASS":
            out = (EVID / "out" / f"{nid}.txt").read_text(encoding="utf-8")
            n = sum(int(x) for x in re.findall(r"(\d+) passed", out))
            verdict = "GREEN"
            reason = f"{rec[nid]['probe']}: import из этого worktree + {n} тестов прошло"
        else:
            verdict = "KEEP"
            reason = KEEP_REASON.get(nid) or f"FAIL {rec.get(nid, {}).get('reason', 'нет прогона')}"
        counts[verdict] += 1
        value = "TOP" if (nid in TOP and verdict == "GREEN") else ("LOW" if nid in LOW else "OK")
        if nid in TOP and verdict == "GREEN":
            reason += f"; TOP: {TOP[nid]}"
        rows.append(f"| {nid} | {label.get(nid, '').replace('|', '/')} | {verdict} | {reason.replace('|', '/')} | {value} |")
    head = ["# mediaux: классификация листьев (зоны media + ux)", "",
            f"GREEN {counts['GREEN']} · RETIRE {counts['RETIRE']} · KEEP {counts['KEEP']} · всего {len(rows)}", "",
            "PASS = импорт в чистом подпроцессе с этим worktree первым в PYTHONPATH (assert __file__ внутри) и "
            ">=1 тест, все прошли. Если у теста нет браузера (Chromium недоступен), такие упавшие по среде тесты "
            "исключены и отмечены `env_excluded=N` в probe; продуктовые падения не исключаются.", "",
            "| id | label | verdict | reason | value |", "|---|---|---|---|---|"]
    (EVID / "mediaux-classification.md").write_text("\n".join(head + rows) + "\n", encoding="utf-8")
    print(counts)


if __name__ == "__main__":
    main()
