"""One command for the first PROVEN self-improvement run through the capability tree (zone -> coding task -> independent check).

    python tools/tree_self_improve.py                       # zone pit/discovery.py, free worker nemotron-ultra-free
    python tools/tree_self_improve.py --worker openrouter-free

It talks to the RUNNING Bossman backend (same product, same data dir, same keys as the dashboard: `POST /api/capability-tree/work`),
waits for the zone job and prints an honest verdict. It does not patch anything itself: the change must come from the free cloud
worker, in the isolated copy, and only Bossman's own independent verification (the zone's tests) can turn the leaf into «verified».
Paid or unknown workers are refused ($0 rule). No key in the vault -> the task fails and the verdict says FAILED, nothing is faked.

Run with the repo on PYTHONPATH (`command-center`, `bossman-core`, repo root) or with the installed runtime python.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

NODE_ID = "module-a7105ffd5da5"            # leaf: command-center/bcc/pit/discovery.py (zone `jeff`)
FREE_WORKERS = ("local", "nemotron-ultra-free", "openrouter-free", "openrouter-code-free")
WISH = ("В bcc/pit/discovery.py функция choose_discovery_question выбирает кандидата через max(candidates, key=score). Если у "
        "кандидата поле NaN (например relevance=float('nan')), его score — NaN; когда он стоит ПЕРВЫМ в списке, max() его не "
        "вытесняет и функция возвращает None, хотя в списке есть хороший вопрос. Нужно: NaN-оценка считается неприемлемой "
        "(как -inf), порядок кандидатов не влияет на результат. Сначала напиши тест, который падает на старом коде. "
        "ОБЪЁМ (жёстко): меняй ТОЛЬКО command-center/bcc/pit/discovery.py и добавь ОДНУ тест-функцию в уже существующий "
        "command-center/tests/test_pit_foundation.py рядом с тестами choose_discovery_question; новых файлов не создавай, "
        "остальные файлы и тесты не трогай.")


def verdict(job: dict[str, Any]) -> tuple[str, str]:
    """(label, why). Only a completed job with changed files AND a passed independent check is VERIFIED_CANDIDATE.

    Even then it is a candidate in an isolated copy: it reaches the project only through the owner's Apply."""
    status, earned = job.get("status"), job.get("earned")
    changed = list(job.get("changed_files") or [])
    if status != "completed":
        return "FAILED", f"задача завершилась как {status!r}; улучшения нет"
    if not changed:
        return "NO_DEFECT_FOUND", "исполнитель ничего не изменил; лист не получает «проверено»"
    if earned == "verified":
        return "VERIFIED_CANDIDATE", f"изменено: {', '.join(changed[:6])}; независимая проверка Bossman пройдена"
    if earned == "unverified":
        return "UNVERIFIED_CANDIDATE", "есть кандидат, но независимой проверки не было — это НЕ доказательство"
    return "CHECK_FAILED", "независимая проверка не пройдена; кандидат ничего не доказывает"


def refuse_worker(worker: str) -> str | None:
    return None if worker in FREE_WORKERS else (
        f"исполнитель {worker!r} не входит в $0-список {list(FREE_WORKERS)}: платные модели запрещены")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--worker", default="nemotron-ultra-free")
    ap.add_argument("--node", default=NODE_ID)
    ap.add_argument("--wish", default=WISH)
    ap.add_argument("--timeout", type=int, default=1900, help="сколько секунд ждать завершения задачи зоны")
    ap.add_argument("--url")
    ap.add_argument("--data-dir")
    args = ap.parse_args(argv)
    refusal = refuse_worker(args.worker)
    if refusal:
        print("TREE_SELF_IMPROVE=REFUSED\n" + refusal)
        return 2
    try:
        from bcc.terminal_cli.api_client import BossmanError, Client, discover
    except ImportError as exc:
        print(f"TREE_SELF_IMPROVE=NOT_RUN\nbcc не импортируется ({exc}); задайте PYTHONPATH=command-center;bossman-core;<repo>")
        return 3
    try:
        with Client(discover(args.url, args.data_dir), timeout=60.0) as client:
            started = client.post("/api/capability-tree/work",
                                  {"node_id": args.node, "instruction": args.wish, "worker": args.worker})
            job = started["job"]
            print(f"задача {job['task_id']} запущена, исполнитель: {job.get('worker_label')}; файлов: {len(job.get('files') or [])}")
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                time.sleep(10)
                live = client.get("/api/capability-tree", params={"lite": 1})
                mine = next((j for j in live.get("work", []) if j.get("task_id") == job["task_id"]), None)
                if mine and mine.get("status") in ("completed", "failed", "blocked"):
                    label, why = verdict(mine)
                    print(f"TREE_SELF_IMPROVE={label}\n{why}")
                    print(json.dumps({k: mine.get(k) for k in ("task_id", "status", "worker", "changed_files", "earned")},
                                     ensure_ascii=False))
                    if label == "VERIFIED_CANDIDATE":
                        print("Дальше: в панели Coding → «Применить» (или `bossman code apply "
                              f"{job['task_id']}`) — только после вашего подтверждения кандидат попадёт в проект.")
                    return 0 if label == "VERIFIED_CANDIDATE" else 1
            print("TREE_SELF_IMPROVE=TIMEOUT\nзадача ещё идёт; смотрите отчёты зоны в пульте/дашборде")
            return 4
    except BossmanError as exc:
        print(f"TREE_SELF_IMPROVE=NOT_RUN\n{exc}")
        return 3


if __name__ == "__main__":
    sys.exit(main())
