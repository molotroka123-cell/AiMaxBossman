#!/usr/bin/env python3
"""Проверка, что метрики РАБОТАЮТ и ЧИТАЮТСЯ — на той машине, где запущена.

Зачем отдельный инструмент. Метрики проверялись по отдельности и в основном на
Linux. Прогон 132 показал цену такого подхода: определитель железа впервые
исполнился на настоящей Windows и упал на кириллице в собственном выводе
(BL-087). Дефект был не в вычислении, а в ОТОБРАЖЕНИИ — и его не видел ни один
тест, потому что никто не запускал раннер так, как его запускает приёмка.

Здесь каждая метрика запускается отдельным процессом с `-I` — ровно как её
зовёт приёмка, — и проверяется на четыре вещи:

1. процесс завершился одним из ОБЪЯВЛЕННЫХ для него кодов, а не любым;
2. в выводе есть его строка вердикта (иначе владельцу нечего прочесть);
3. JSON разбирается и несёт объявленные ключи с осмысленными числами;
4. в выводе нет следов испорченной кодировки — символа замены и
   характерных последовательностей мохибейка.

Четвёртая проверка — та, ради которой инструмент и написан: числа могут быть
верными, а на экране владельца стоять «ÐŸÑ€Ð¾Ñ†ÐµÑÑÐ¾Ñ€».

Запуск: `python tools/metrics_smoke.py [--home ПУТЬ] [--json ФАЙЛ]`.
`--home` указывает на распакованный архив: тогда раннеры берутся из
`app-support`, а не из репозитория. Коды выхода: 0 — PASS, 1 — FAIL.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Список явный, а не глоб: метрика, забытая в списке, — это метрика, которую
# никто не проверяет, и узнаётся это на машине владельца.
METRICS = (
    {
        "name": "железо",
        "repo_path": REPO / "scripts" / "target_hardware_acceptance.py",
        "archive_name": "target_hardware_acceptance.py",
        "args": ["--json", "{json}"],
        "exit_codes": (0, 2, 3),          # different / owner_required / undetermined
        "verdict_lines": ("TARGET_HARDWARE_VERDICT=", "TARGET_HARDWARE_SOURCES="),
        "json_keys": ("host", "verdict"),
        "numbers": (("host", "ram_gib"),),
    },
    {
        "name": "машина владельца",
        "repo_path": REPO / "tools" / "owner_machine_report.py",
        "archive_name": "owner_machine_report.py",
        "args": ["--json", "{json}", "--quiet"],
        "exit_codes": (0, 1, 2),
        "verdict_lines": ("BOSSMAN_OWNER_MACHINE=",),
        "json_keys": ("stages", "status", "platform"),
        "numbers": (),
    },
    {
        "name": "предполётная проверка",
        "repo_path": REPO / "scripts" / "bossman_doctor.py",
        "archive_name": "bossman_doctor.py",
        "args": ["--json-out", "{json}"],
        "exit_codes": (0, 1, 2),
        "verdict_lines": (),               # доктор печатает таблицу, не строку
        "json_keys": ("schema_version", "checks", "blocked"),
        "numbers": (("blocked",),),
    },
)

# Следы испорченной кодировки. U+FFFD — прямой признак; остальное — то, во что
# превращается кириллица в UTF-8, прочитанная как cp1252/latin-1.
MOJIBAKE = ("�", "Ð", "Ñ", "â€", "Ã©", "Ð¾Ð")


def _runner(metric: dict, home: Path | None) -> Path:
    if home is None:
        return metric["repo_path"]
    return home / "app-support" / metric["archive_name"]


def _dig(data, path: tuple[str, ...]):
    for key in path:
        if not isinstance(data, dict) or key not in data:
            return None
        data = data[key]
    return data


def check(metric: dict, python: str, home: Path | None, work: Path) -> dict:
    runner = _runner(metric, home)
    problems: list[str] = []
    if not runner.is_file():
        return {"metric": metric["name"], "verdict": "FAIL", "runner": str(runner),
                "problems": ["раннер не найден"], "exit_code": None}

    out_json = work / f"{metric['archive_name']}.json"
    argv = [python, "-I", str(runner)]
    argv += [a.replace("{json}", str(out_json)) for a in metric["args"]]
    try:
        done = subprocess.run(argv, capture_output=True, timeout=900)
    except subprocess.TimeoutExpired:
        return {"metric": metric["name"], "verdict": "FAIL", "runner": str(runner),
                "problems": ["не ответил за 900 с"], "exit_code": None}

    # Читаем БАЙТАМИ и декодируем сами: если раннер испортил кодировку, это
    # должно быть видно, а не исправлено за него чтением в режиме text.
    text = (done.stdout + done.stderr).decode("utf-8", "replace")

    if done.returncode not in metric["exit_codes"]:
        problems.append(f"код выхода {done.returncode}, объявлены {metric['exit_codes']}")
    if "Traceback (most recent call last)" in text:
        problems.append("процесс упал трейсбеком")
    for line in metric["verdict_lines"]:
        if line not in text:
            problems.append(f"нет строки вердикта {line!r}")
    for mark in MOJIBAKE:
        if mark in text:
            problems.append(f"в выводе следы испорченной кодировки: {mark!r}")
            break

    payload = None
    if out_json.is_file():
        try:
            payload = json.loads(out_json.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            problems.append(f"JSON не разобрался: {type(exc).__name__}")
    else:
        problems.append("JSON не записан")

    if isinstance(payload, dict):
        for key in metric["json_keys"]:
            if key not in payload:
                problems.append(f"в JSON нет ключа {key!r}")
        for path in metric["numbers"]:
            value = _dig(payload, path)
            if value is None:
                continue          # «не определено» — законный ответ, а не дефект
            if not isinstance(value, (int, float)) or value != value or value < 0:
                problems.append(f"{'.'.join(path)} = {value!r} — не годное число")

    return {"metric": metric["name"], "verdict": "PASS" if not problems else "FAIL",
            "runner": str(runner), "exit_code": done.returncode,
            "problems": problems, "chars": len(text)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, help="распакованный архив (иначе репозиторий)")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="metrics-smoke-") as tmp:
        rows = [check(m, args.python, args.home, Path(tmp)) for m in METRICS]

    for row in rows:
        print(f"{row['verdict']:<4} {row['metric']} (код {row['exit_code']})")
        for problem in row["problems"]:
            print(f"     {problem}")

    verdict = "PASS" if all(r["verdict"] == "PASS" for r in rows) else "FAIL"
    report = {"type": "bossman.metrics_smoke", "schema": 1, "verdict": verdict,
              "platform": sys.platform, "home": str(args.home) if args.home else None,
              "metrics": rows}
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    print(f"BOSSMAN_METRICS_SMOKE={verdict} проверено={len(rows)} платформа={sys.platform}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
