"""Обязательные задания должны СУЩЕСТВОВАТЬ для точного SHA кандидата.

Урок BL-089 в исполняемом виде. Каноническая ветка полгода выглядела
благополучно, потому что два задания Windows на ней не запускались ВООБЩЕ:
не красный прогон, а отсутствие прогона. Красный виден в любом отчёте.
Отсутствующий не окрашен никак, и страница ветки выглядит одинаково и когда
задание не требовалось, и когда оно не запускалось.

Отсюда правило владельца, которое здесь и кодируется:

    отсутствующее задание  — ОТКАЗ (а не «замечаний нет»)
    задание в очереди      — НЕИЗВЕСТНО (а не «пройдено»)
    отменённое задание     — НЕИЗВЕСТНО (а не «пройдено»)
    только завершённый успешный прогон — ПРОЙДЕНО

Решение отделено от сети намеренно: `classify()` и `gate()` — чистые функции
над списком прогонов, и именно они покрыты тестами. Обращение к API живёт в
`main()` и ничего не решает.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DECLARATION = ROOT / "tools" / "mandatory_release_workflows.json"

PASS = "PASS"
FAIL = "FAIL"
MISSING = "MISSING"
UNKNOWN_QUEUED = "UNKNOWN_QUEUED"
UNKNOWN_CANCELLED = "UNKNOWN_CANCELLED"
UNKNOWN_OTHER = "UNKNOWN_OTHER"

#: Вердикты, при которых кандидат НЕ выпускается. `MISSING` здесь не случайно
#: и не для строгости: именно он был дефектом BL-089.
BLOCKING = (FAIL, MISSING, UNKNOWN_QUEUED, UNKNOWN_CANCELLED, UNKNOWN_OTHER)


def load_declaration(path: Path | None = None) -> list[str]:
    data = json.loads((path or DECLARATION).read_text(encoding="utf-8"))
    declared = data["mandatory"]
    if not declared:
        raise ValueError("список обязательных заданий пуст — гейт ничего не проверял бы")
    return list(declared)


def classify(run: dict[str, Any]) -> str:
    """Один прогон — один вердикт. Незавершённое НЕ становится успехом."""
    status = (run.get("status") or "").lower()
    if status != "completed":
        # queued / in_progress / pending / waiting / requested
        return UNKNOWN_QUEUED
    conclusion = (run.get("conclusion") or "").lower()
    if conclusion == "success":
        return PASS
    if conclusion == "cancelled":
        return UNKNOWN_CANCELLED
    if conclusion in ("failure", "timed_out", "startup_failure"):
        return FAIL
    # neutral / skipped / action_required / stale — успехом не считается ничто.
    return UNKNOWN_OTHER


def _best(verdicts: list[str]) -> str:
    """Лучший вердикт среди повторов одного задания.

    Повторный запуск — законный способ исправить срыв инфраструктуры, поэтому
    один успешный прогон на этом SHA достаточен. Но ТОЛЬКО успешный: из двух
    неуспешных лучший не выбирается, порядок ниже это задаёт явно.
    """
    for candidate in (PASS, FAIL, UNKNOWN_CANCELLED, UNKNOWN_QUEUED, UNKNOWN_OTHER):
        if candidate in verdicts:
            return candidate
    return MISSING


def gate(declared: Iterable[str], runs: Iterable[dict[str, Any]], sha: str) -> dict[str, Any]:
    """Свести объявленные задания с прогонами ИМЕННО этого SHA."""
    declared = list(declared)
    seen: dict[str, list[str]] = {name: [] for name in declared}
    for run in runs:
        if (run.get("head_sha") or "") != sha:
            continue  # чужой коммит доказательством для этого не является
        name = run.get("path") or run.get("name") or ""
        name = name.rsplit("/", 1)[-1]
        if name in seen:
            seen[name].append(classify(run))
    results = {name: _best(v) for name, v in seen.items()}
    blocking = {n: v for n, v in results.items() if v in BLOCKING}
    return {
        "sha": sha,
        "verdict": PASS if not blocking else FAIL,
        "workflows": results,
        "blocking": blocking,
    }


def _fetch(repo: str, sha: str, token: str) -> list[dict[str, Any]]:
    import httpx
    runs: list[dict[str, Any]] = []
    url = f"https://api.github.com/repos/{repo}/actions/runs"
    headers = {"Authorization": f"Bearer {token}",
               "Accept": "application/vnd.github+json"}
    with httpx.Client(timeout=30.0) as client:
        for page in range(1, 6):
            reply = client.get(url, headers=headers,
                               params={"head_sha": sha, "per_page": 100, "page": page})
            reply.raise_for_status()
            batch = reply.json().get("workflow_runs") or []
            runs.extend(batch)
            if len(batch) < 100:
                break
    return runs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sha", required=True)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--json", type=Path)
    parser.add_argument("--runs-from", type=Path,
                        help="читать прогоны из файла вместо API (для проверки самой логики)")
    args = parser.parse_args(argv)

    declared = load_declaration()
    if args.runs_from is not None:
        runs = json.loads(args.runs_from.read_text(encoding="utf-8"))
    else:
        # Ключ ТОЛЬКО из окружения, и никогда не печатается.
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
        if not token or not args.repo:
            print("RELEASE_WORKFLOW_GATE=OWNER_REQUIRED нет GITHUB_TOKEN или репозитория")
            return 3
        runs = _fetch(args.repo, args.sha, token)

    report = gate(declared, runs, args.sha)
    for name, verdict in sorted(report["workflows"].items()):
        print(f"  {verdict:<18} {name}")
    print(f"RELEASE_WORKFLOW_GATE={report['verdict']} sha={args.sha[:12]} "
          f"blocking={len(report['blocking'])}")
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    return 0 if report["verdict"] == PASS else 1


if __name__ == "__main__":
    sys.exit(main())
