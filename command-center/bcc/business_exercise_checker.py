"""Независимый чекер учебных бизнес-упражнений.

Не импортирует `bcc.business_exercises`: читает только файл ключа (лежит вне
корней агента) и присланный ответ. Деньги сравниваются через Decimal,
округление ROUND_HALF_UP до 0.01, списки — как множества.

Квитанция содержит sha256 ответа, ключа и самой квитанции, но НЕ содержит
ожидаемых значений: в отказе видны только имена полей, которые не сошлись.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

MAX_ANSWER_BYTES = 16_384
CHECKER_VERSION = "bizcheck-2026.10.31-v1"


class CheckerError(ValueError):
    pass


def _money(value: Any) -> Decimal | None:
    try:
        text = str(value).replace(" ", "").replace(" ", "").replace(",", ".")
        return Decimal(text).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None


def _ids(value: Any) -> set[str] | None:
    if not isinstance(value, list):
        return None
    return {str(x).strip().upper() for x in value}


def _compare(expected: Any, got: Any, path: str, out: list[str]) -> int:
    """Сравнить узел; вернуть число проверенных полей, несошедшиеся пути — в out."""
    if isinstance(expected, dict):
        if not isinstance(got, dict):
            out.append(path or "<root>")
            return max(1, len(expected))
        exp_keys = {str(k).upper(): k for k in expected}
        got_norm = {str(k).strip().upper(): v for k, v in got.items()}
        n = 0
        for up, key in exp_keys.items():
            n += _compare(expected[key], got_norm.get(up), f"{path}.{key}" if path else str(key), out)
        extra = set(got_norm) - set(exp_keys)
        if extra:
            out.append(f"{path or '<root>'}:extra_keys")
            n += 1
        return n
    if isinstance(expected, list):
        if _ids(got) != _ids(expected):
            out.append(path)
        return 1
    exp_money = _money(expected) if any(ch.isdigit() for ch in str(expected)) and "." in str(expected) else None
    if exp_money is not None:
        if _money(got) != exp_money:
            out.append(path)
        return 1
    if str(got or "").strip().upper() != str(expected).strip().upper():
        out.append(path)
    return 1


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def check_answer(keys_dir: Path, exercise_id: str, answer: Any) -> dict:
    """Проверить ответ по файлу ключа. Возвращает квитанцию."""
    if not isinstance(exercise_id, str) or not exercise_id.replace("_", "").isalnum():
        raise CheckerError("неверный exercise_id")
    raw_answer = json.dumps(answer, sort_keys=True, ensure_ascii=False)
    if len(raw_answer.encode("utf-8")) > MAX_ANSWER_BYTES:
        raise CheckerError(f"ответ больше {MAX_ANSWER_BYTES} байт")
    key_path = Path(keys_dir) / f"{exercise_id}.json"
    if not key_path.is_file():
        raise CheckerError("ключ упражнения не материализован")
    key_text = key_path.read_text(encoding="utf-8")
    key = json.loads(key_text)
    if key.get("exercise_id") != exercise_id:
        raise CheckerError("ключ не от этого упражнения")
    mismatches: list[str] = []
    total = _compare(key["truth"], answer if isinstance(answer, dict) else None, "", mismatches)
    passed = not mismatches
    receipt = {
        "checker": CHECKER_VERSION, "exercise_id": exercise_id, "passed": passed,
        "fields_checked": total, "fields_failed": len(mismatches),
        "failed_fields": sorted(mismatches),
        "score": round((total - len(mismatches)) / total, 4) if total else 0.0,
        "answer_sha256": _sha_text(raw_answer), "key_sha256": _sha_text(key_text),
        "checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "simulated": True,
    }
    receipt["receipt_sha256"] = _sha_text(json.dumps(receipt, sort_keys=True, ensure_ascii=False))
    return receipt


__all__ = ["check_answer", "CheckerError", "MAX_ANSWER_BYTES", "CHECKER_VERSION"]
