#!/usr/bin/env python3
"""Инвентарь сходимости: что из требуемого владельцем ДОСТИЖИМО в рантайме.

Раздел 4 директивы: «не путать документацию с правдой рантайма». Слова
VERIFIED, DONE, PRODUCTION READY доказательством не являются. Поэтому здесь
ничего не утверждается — всё измеряется:

* код — по файлам, которые существуют;
* ДОСТИЖИМОСТЬ — по настоящей загрузке фич (`load_features()`) и по реестру
  страниц UI, а не по названию папки;
* тесты — по файлам, которые реально упоминают модули области;
* приёмка на установленном продукте — по `tools/acceptance_registry.json`.

Уровни улик (раздел 25) назначаются по измеренному, и выше измеренного не
поднимаются никогда:

* `L1_CONTRACT` — код есть и есть тесты;
* `L2_CI_INTEGRATION` — сверх того область присутствует в профиле приёмки на
  УСТАНОВЛЕННОМ продукте, то есть проверяется не на исходниках;
* `L3_OWNER_HARDWARE` — не назначается этим инструментом НИКОГДА. Его выдаёт
  только прогон на машине владельца.

Запуск: `python tools/convergence_inventory.py [--json ФАЙЛ]`
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CC = REPO / "command-center"

# Области — из разделов 6–22 директивы. Список явный: область, забытая здесь,
# это область, которую никто не инвентаризует.
AREAS = (
    {"id": "core_ai", "title": "Ядро ИИ: модели, маршрутизация, запасной путь, стоимость",
     "section": "6", "features": ("router", "openrouter", "local_first", "second_opinion"),
     "pattern": r"openai_compat|provider|routing|fallback|model_health"},
    {"id": "context_memory", "title": "Контекст и память владельца",
     "section": "7", "features": ("tools_memory", "provenance", "snapshot"),
     "pattern": r"decisions|context_store|memory|ContextStore"},
    {"id": "computer_control", "title": "Управление компьютером",
     "section": "8", "features": ("apps_control", "oss_integrations"),
     "pattern": r"computer_operator|uitars|pyautogui|desktop"},
    {"id": "browser_agent", "title": "Браузерный агент",
     "section": "9", "features": ("browser", "tools_browser", "browser_help", "web_research"),
     "pattern": r"playwright|browser"},
    {"id": "file_intelligence", "title": "Работа с файлами",
     "section": "10", "features": ("file_intelligence", "unified_search"),
     "pattern": r"file_intelligence"},
    {"id": "terminal_agent", "title": "Терминал и инженерный агент",
     "section": "11", "features": ("terminal", "tools_terminal", "tools_code", "coding_tasks"),
     "pattern": r"terminal|shell|command_policy"},
    {"id": "multi_agent", "title": "Мультиагентная система",
     "section": "12", "features": ("agentmap", "nl_orchestra", "forks", "organization"),
     "pattern": r"orchestra|planner|verifier|agentmap"},
    {"id": "approvals", "title": "Модель одобрений владельца",
     "section": "13", "features": ("action_gate", "nl_permissions", "review_gate", "action_contract"),
     "pattern": r"approval|gate_completion|ActionGate"},
    {"id": "telegram", "title": "Телеграм как удалённый интерфейс",
     "section": "14", "features": ("tools_openclaw", "plugins"),
     "pattern": r"telegram|openclaw"},
    {"id": "command_center", "title": "Командный центр — один понятный интерфейс",
     "section": "15", "features": ("command_bar", "control_plane", "reality"),
     "pattern": r"command_bar|control_plane"},
    {"id": "image_studio", "title": "Студия изображений",
     "section": "16", "features": ("images", "studio"),
     "pattern": r"studio|image"},
    {"id": "video_studio", "title": "Видеостудия",
     "section": "17", "features": ("video_studio",),
     "pattern": r"video_studio|ffmpeg"},
    {"id": "learning", "title": "Обучение на успешной работе",
     "section": "19", "features": ("skills", "benchlab", "failure_to_case", "healing"),
     "pattern": r"skill_version|promotion|learning|shadow"},
    {"id": "continuity", "title": "Непрерывность задач через перезапуск",
     "section": "20", "features": ("snapshot", "watchdog", "missions"),
     "pattern": r"recover|resume|checkpoint|crash"},
    {"id": "security", "title": "Безопасность и границы прав",
     "section": "21", "features": ("action_gate", "nl_permissions", "plugins"),
     "pattern": r"path_traversal|ssrf|secret|allowlist|injection"},
    {"id": "governor", "title": "Губернатор ресурсов и стоимости",
     "section": "22", "features": ("governor", "spend_meter"),
     "pattern": r"governor|budget|spend|cost_usd"},
    {"id": "local_models", "title": "Локальные модели на железе владельца",
     "section": "23", "features": ("local_first", "openrouter"),
     "pattern": r"openai_compat|llama|gguf|local_model"},
)


def loaded_features() -> dict[str, bool]:
    """ДОСТИЖИМОСТЬ измеряется настоящей загрузкой, а не списком файлов."""
    code = ("import sys, json; sys.path.insert(0, '.');"
            "from bcc.features import load_features;"
            "print(json.dumps({f.name: getattr(f, 'router', None) is not None"
            " for f in load_features()}))")
    try:
        out = subprocess.run([sys.executable, "-c", code], cwd=CC,
                             capture_output=True, text=True, timeout=300)
        return json.loads(out.stdout.strip().splitlines()[-1])
    except Exception as exc:  # noqa: BLE001 — отсутствие ответа не выдаётся за ответ
        print(f"ВНИМАНИЕ: фичи не загрузились ({type(exc).__name__}); "
              "достижимость будет NOT_MEASURED", file=sys.stderr)
        return {}


def ui_pages() -> set[str]:
    src = (CC / "ui" / "pages" / "index.js")
    if not src.is_file():
        return set()
    return set(re.findall(r"id:\s*'([a-z0-9_-]+)'", src.read_text(encoding="utf-8")))


def installed_profile_modules() -> set[str]:
    path = REPO / "tools" / "acceptance_registry.json"
    if not path.is_file():
        return set()
    return set(json.loads(path.read_text(encoding="utf-8"))["junit"]["modules"])


def count_tests(pattern: str) -> list[str]:
    roots = [REPO / "tests", CC / "tests", REPO / "bossman-core" / "tests"]
    found: list[str] = []
    rx = re.compile(pattern, re.I)
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("test_*.py")):
            try:
                if rx.search(path.read_text(encoding="utf-8", errors="replace")):
                    found.append(str(path.relative_to(REPO)))
            except OSError:
                continue
    return found


def assess(area: dict, features: dict[str, bool], pages: set[str],
           profile: set[str]) -> dict:
    present = [f for f in area["features"] if f in features]
    reachable = [f for f in present if features.get(f)]
    tests = count_tests(area["pattern"])
    in_profile = sorted(m for m in profile if re.search(area["pattern"], m, re.I))

    if not features:
        level, why = "NOT_MEASURED", "фичи не загрузились: достижимость не измерена"
    elif not present:
        level, why = "NOT_IMPLEMENTED", "ни одной объявленной фичи не существует"
    elif not reachable:
        level, why = "NOT_REACHABLE", (
            f"код есть ({len(present)} фич), но ни одна не отдаёт HTTP-роутер — "
            "из интерфейса владельца недостижимо")
    elif not tests:
        level, why = "UNTESTED", f"{len(reachable)} фич достижимы, но тестов не найдено"
    elif in_profile:
        level, why = "L2_CI_INTEGRATION", (
            f"{len(reachable)} фич достижимы, {len(tests)} тестовых файлов, "
            f"и {len(in_profile)} модуль(ей) в профиле приёмки на УСТАНОВЛЕННОМ продукте")
    else:
        level, why = "L1_CONTRACT", (
            f"{len(reachable)} фич достижимы, {len(tests)} тестовых файлов; "
            "на установленном продукте НЕ проверяется")

    return {"id": area["id"], "title": area["title"], "directive_section": area["section"],
            "features_declared": list(area["features"]), "features_present": present,
            "features_reachable": reachable, "ui_pages_related": sorted(
                p for p in pages if re.search(area["pattern"], p.replace("-", "_"), re.I)),
            "test_files": len(tests), "test_files_sample": tests[:4],
            "installed_profile_modules": in_profile,
            "evidence_level": level, "why": why}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)

    features, pages, profile = loaded_features(), ui_pages(), installed_profile_modules()
    rows = [assess(a, features, pages, profile) for a in AREAS]

    order = ("NOT_MEASURED", "NOT_IMPLEMENTED", "NOT_REACHABLE", "UNTESTED",
             "L1_CONTRACT", "L2_CI_INTEGRATION")
    counts = {lvl: sum(1 for r in rows if r["evidence_level"] == lvl) for lvl in order}

    for row in rows:
        print(f"{row['evidence_level']:<18} §{row['directive_section']:<3} {row['title']}")
        print(f"     {row['why']}")

    print()
    print("BOSSMAN_INVENTORY_AREAS=%d " % len(rows)
          + " ".join(f"{k}={v}" for k, v in counts.items() if v))
    print("BOSSMAN_INVENTORY_FEATURES_LOADED=%d reachable=%d"
          % (len(features), sum(1 for v in features.values() if v)))
    print("L3_OWNER_HARDWARE=0 — этот инструмент его не выдаёт по построению")

    report = {"type": "bossman.convergence_inventory", "schema": 1,
              "features_loaded": len(features),
              "features_reachable": sum(1 for v in features.values() if v),
              "ui_pages": len(pages), "counts": counts, "areas": rows}
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
