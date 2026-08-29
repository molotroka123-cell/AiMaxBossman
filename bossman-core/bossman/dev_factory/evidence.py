"""Stage 10 — сбор доказательств.

Инвариант: заявленный успех обязан иметь артефакт. Разбор вывода тестов
консервативен — непонятный вывод даёт UNKNOWN, а UNKNOWN НЕ считается успехом.
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import obs
from .models import Evidence, Verdict

_PYTEST_OK = re.compile(r"(\d+)\s+passed", re.I)
_PYTEST_FAIL = re.compile(r"(\d+)\s+(?:failed|error(?:s)?)", re.I)


def from_test_output(text: str, stdout_path: str | None = None) -> Evidence:
    """Вердикт по выводу тестов. Секреты вычищаются до сохранения."""
    clean = obs.redact(text or "")
    passed = int(m.group(1)) if (m := _PYTEST_OK.search(clean)) else 0
    failed = sum(int(x) for x in _PYTEST_FAIL.findall(clean))
    if failed > 0:
        verdict = Verdict.FAIL
    elif passed > 0:
        verdict = Verdict.PASS
    else:
        # Ни одного распознанного результата — доказательств нет.
        verdict = Verdict.UNKNOWN
    summary = f"passed={passed} failed={failed}"
    return Evidence(verdict=verdict, summary=summary, stdout_path=stdout_path,
                    passed=passed, failed=failed)


def write_evidence(dir_path: str | Path, name: str, text: str) -> str:
    """Сохранить доказательство (с вычищенными секретами) и вернуть путь."""
    d = Path(dir_path)
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(obs.redact(text or ""), encoding="utf-8")
    return str(p)
