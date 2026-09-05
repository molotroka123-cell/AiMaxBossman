#!/usr/bin/env python3
"""TRUTH-003 §16 — реестр пропусков тестов: каждый skip/skipif/importorskip в
command-center/tests, bossman-core/tests, tests — с причиной, владельцем (каталог),
зависимостью от окружения и условием пересмотра. Невидимого числа «46 skips» больше нет.

    python tools/skips_registry.py            # перегенерировать docs/testing/SKIPS_REGISTRY.md
    python tools/skips_registry.py --check    # реестр актуален и у каждого skip есть причина
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUITES = {"command-center/tests": "Command Center", "bossman-core/tests": "Bossman Core", "tests": "root (shared/tools)"}
OUT = ROOT / "docs" / "testing" / "SKIPS_REGISTRY.md"
ENV_HINTS = (
    ("chromium|browser|playwright", "Chromium/Playwright на хосте"),
    ("docker|sandbox|gvisor|kvm|runtime", "контейнерный рантайм (docker/gVisor/KVM)"),
    ("OPENROUTER_API_KEY|LIVE|live", "живой внешний сервис / owner-authorized live"),
    ("PG|postgres|DSN", "реальный PostgreSQL"),
    ("mcp|websockets|opencode|bossman_v3|LEDGER", "необязательный пакет / соседний компонент"),
    ("symlink|Windows|win", "права ФС / платформа"),
)
REVIEW = "пересмотреть, когда зависимость появится в CI-окружении (runner/секрет/пакет)"


def _reason(call: ast.Call, src: str) -> str:
    for kw in call.keywords:
        if kw.arg == "reason":
            return _text(kw.value, src)
    fn = call.func
    name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
    if name == "skip" and call.args:
        return _text(call.args[0], src)
    if name == "importorskip" and call.args:
        return f"нет пакета {_text(call.args[0], src)}"
    return ""


def _text(node: ast.AST, src: str) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.strip()
    return ast.get_source_segment(src, node) or "<expr>"


def _env(reason: str, cond: str) -> str:
    blob = f"{reason} {cond}"
    for rx, label in ENV_HINTS:
        if re.search(rx, blob, re.I):
            return label
    return "условие в коде теста"


def collect() -> list[dict]:
    rows: list[dict] = []
    for suite, owner in SUITES.items():
        for path in sorted((ROOT / suite).rglob("test_*.py")) + sorted((ROOT / suite).glob("conftest.py")):
            src = path.read_text(encoding="utf-8-sig")            # BOM-файлы тоже разбираются
            try:
                tree = ast.parse(src, filename=str(path))
            except SyntaxError as exc:
                rows.append({"suite": suite, "owner": owner, "file": path.relative_to(ROOT).as_posix(), "line": exc.lineno or 0,
                             "kind": "unparsable", "condition": "", "reason": f"SyntaxError: {exc.msg}", "env": "—", "review": REVIEW})
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                chain = ast.get_source_segment(src, fn) or ""
                if name not in ("skip", "skipif", "importorskip") or "pytest" not in chain and "mark" not in chain:
                    continue
                cond = _text(node.args[0], src) if name == "skipif" and node.args else ""
                reason = _reason(node, src)
                rows.append({"suite": suite, "owner": owner, "file": path.relative_to(ROOT).as_posix(), "line": node.lineno,
                             "kind": name, "condition": cond[:120], "reason": reason[:160], "env": _env(reason, cond),
                             "review": REVIEW})
    return rows


def render(rows: list[dict]) -> str:
    lines = ["# Реестр пропусков тестов (генерируется `python tools/skips_registry.py`)", "",
             f"Всего записей: {len(rows)}. Каждая — с причиной, владельцем, зависимостью от окружения и условием пересмотра.",
             "Пропуск без причины — провал `--check`. Skip не равен PASS: пропущенный тест не является уликой.", "",
             "| Тест | Вид | Условие | Причина | Владелец | Зависимость | Пересмотр |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| `{r['file']}:{r['line']}` | {r['kind']} | `{r['condition'] or '—'}` | {r['reason'] or '**НЕТ ПРИЧИНЫ**'} | "
                     f"{r['owner']} | {r['env']} | {r['review']} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    rows = collect()
    text = render(rows)
    missing = [r for r in rows if not r["reason"]]
    if "--check" in argv:
        ok = OUT.exists() and OUT.read_text(encoding="utf-8") == text
        for r in missing:
            print(f"skip without reason: {r['file']}:{r['line']}", file=sys.stderr)
        print(f"SKIPS_REGISTRY_CURRENT={'PASS' if ok else 'FAIL'} entries={len(rows)} without_reason={len(missing)}")
        return 0 if ok and not missing else 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"registry written: {OUT.relative_to(ROOT)} entries={len(rows)} without_reason={len(missing)}")
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
