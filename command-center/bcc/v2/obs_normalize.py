"""Нормализация наблюдений (spec Part G) — детерминированный парсинг выходов
инструментов ДО попадания в контекст LLM.

Правило (spec §38): сырой вывод (хоть 50k строк) НИКОГДА не идёт в контекст —
он целиком кладётся в `raw_artifact` (вызывающий код сохраняет его как
артефакт), а в контекст уходит компактное ограниченное представление.
Модуль чистый и толерантный: на странном входе не падает, а ставит ok=False
и приписку об ошибке.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

MAX_OBSERVATION_CHARS = 4000  # ограничение активного наблюдения (spec §38)
_MAX_FAILURE_NAMES = 20       # имен упавших тестов в наблюдении — не все
_MAX_TRACEBACK_CHARS = 1200   # первый блок traceback, ограниченный

_MARK = "...[truncated {} chars]"


@dataclass(slots=True)
class NormalizedObservation:
    kind: str
    ok: bool
    summary: str
    fields: dict = field(default_factory=dict)
    failure_names: list[str] = field(default_factory=list)
    truncated: bool = False
    raw_artifact: str = ""   # raw хранится ЗДЕСЬ, в контекст не идёт


def bound_text(text: str, max_chars: int) -> tuple[str, bool]:
    """Детерминированная обрезка: голова + маркер «...[truncated N chars]».
    Возвращает (текст, был_ли_обрезан)."""
    text = "" if text is None else str(text)
    if max_chars is None or max_chars <= 0:
        return "", True
    if len(text) <= max_chars:
        return text, False
    kept = max(0, max_chars - 30)
    while kept > 0 and kept + len(_MARK.format(len(text) - kept)) > max_chars:
        kept -= 1
    if kept == 0:
        return _MARK.format(len(text))[:max_chars], True
    return text[:kept] + _MARK.format(len(text) - kept), True


def _tail(text: str, max_chars: int) -> tuple[str, bool]:
    """Хвост-keep: маркер в начале + хвост строки (для stderr/логов важен конец)."""
    text = "" if text is None else str(text)
    if max_chars is None or max_chars <= 0:
        return "", True
    if len(text) <= max_chars:
        return text, False
    kept = max(0, max_chars - 30)
    while kept > 0 and kept + len(_MARK.format(len(text) - kept)) > max_chars:
        kept -= 1
    if kept == 0:
        return _MARK.format(len(text))[:max_chars], True
    return _MARK.format(len(text) - kept) + text[-kept:], True


def _fallback(kind: str, raw: str, exc: Exception) -> NormalizedObservation:
    return NormalizedObservation(
        kind=kind, ok=False, summary=f"{kind}: normalize failed ({type(exc).__name__})",
        fields={"error": f"{type(exc).__name__}: {exc}"}, failure_names=[],
        truncated=False, raw_artifact=raw or "")


def _count(text: str, pattern: str) -> int | None:
    m = re.search(pattern, text)
    return int(m.group(1)) if m else None


def normalize_pytest_output(text: str, *,
                            max_chars: int = MAX_OBSERVATION_CHARS) -> NormalizedObservation:
    """Счётчики passed/failed/skipped/errors, имена упавших тестов (строки
    short summary с "::"), первый traceback-блок (ограниченный)."""
    text = text if isinstance(text, str) else ("" if text is None else str(text))
    try:
        passed = _count(text, r"(\d+)\s+passed")
        failed = _count(text, r"(\d+)\s+failed")
        skipped = _count(text, r"(\d+)\s+skipped")
        errors = _count(text, r"(\d+)\s+errors?\b")
        if passed is None and failed is None and errors is None and skipped is None:
            note = "no pytest summary found"
            view, truncated = bound_text(text, max_chars)
            return NormalizedObservation(
                "pytest", False, f"pytest: unparsed output ({len(text)} chars)",
                fields={"note": note, "output_head": view}, failure_names=[],
                truncated=truncated, raw_artifact=text)
        passed = passed or 0
        failed = failed or 0
        skipped = skipped or 0
        errors_ct = errors or 0
        failure_names: list[str] = []
        for line in text.splitlines():
            s = line.strip()
            if not (s.startswith("FAILED ") or s.startswith("ERROR ")):
                continue
            name = s.split(None, 1)[1].split(" - ")[0].strip()
            if "::" not in name or name in failure_names:
                continue
            if len(failure_names) < _MAX_FAILURE_NAMES:
                failure_names.append(name)
        m = re.search(r"=+\s+(?:FAILURES|ERRORS)\s+=+(.*?)(?:\n=+\s+\w+\s+=+|\Z)",
                      text, re.S)
        if m:
            tb, tb_trunc = bound_text(m.group(1).strip(), _MAX_TRACEBACK_CHARS)
        else:
            tb_lines = [ln for ln in text.splitlines() if ln.startswith("E ")][:10]
            tb = "\n".join(tb_lines)[:_MAX_TRACEBACK_CHARS]
            tb_trunc = bool(tb_lines)
        fields = {"passed": passed, "failed": failed, "skipped": skipped,
                  "errors": errors_ct, "error_block": tb}
        truncated = len(text) > max_chars or tb_trunc
        if len(failure_names) >= _MAX_FAILURE_NAMES:
            fields["failure_names_truncated"] = True
        return NormalizedObservation(
            "pytest", ok=(failed == 0 and errors_ct == 0),
            summary=f"pytest: {passed or 0} passed, {failed or 0} failed, "
                    f"{skipped or 0} skipped, {errors_ct} errors",
            fields=fields, failure_names=failure_names, truncated=truncated,
            raw_artifact=text)
    except Exception as exc:  # толерантность: странный вход не должен ронять пайплайн
        return _fallback("pytest", text, exc)


def normalize_git_status(text: str, *,
                         max_chars: int = MAX_OBSERVATION_CHARS) -> NormalizedObservation:
    """Porcelain-статус и опциональные numstat-строки «ins\tdel\tpath»."""
    text = text if isinstance(text, str) else ("" if text is None else str(text))
    try:
        changed: list[str] = []
        insertions = deletions = 0
        for line in text.splitlines():
            if not line.strip():
                continue
            m = re.match(r"^(\d+|-)\t(\d+|-)\t(.+)$", line)
            if m:
                ins, dels, path = m.groups()
                if ins != "-":
                    insertions += int(ins)
                if dels != "-":
                    deletions += int(dels)
                changed.append(path.strip())
                continue
            code, _, path = line.partition(" ")
            if len(code) == 2 and re.fullmatch(r"[MADRCU?!A ]{2}", code) and path.strip():
                changed.append(path.strip())
        seen: set[str] = set()
        unique = [f for f in changed if not (f in seen or seen.add(f))]
        truncated = len(text) > max_chars
        files = unique[:50]
        if len(unique) > 50:
            fields_extra = {"files_truncated": True}
        else:
            fields_extra = {}
        fields = {"changed_files": files, "changed_count": len(unique),
                  "insertions": insertions, "deletions": deletions,
                  "dirty": bool(unique), **fields_extra}
        return NormalizedObservation(
            "git", ok=True,  # сам парсинг статуса — не успех/неуспех команды
            summary=f"git: {len(unique)} changed, +{insertions}/-{deletions}",
            fields=fields, failure_names=[], truncated=truncated,
            raw_artifact=text)
    except Exception as exc:
        return _fallback("git", text, exc)


def normalize_process(text: str, *, exit_code: int, stderr: str = "",
                      max_chars: int = MAX_OBSERVATION_CHARS) -> NormalizedObservation:
    """Код выхода + ограниченные хвосты stdout/stderr (важен конец вывода)."""
    text = text if isinstance(text, str) else ("" if text is None else str(text))
    stderr = stderr if isinstance(stderr, str) else ("" if stderr is None else str(stderr))
    try:
        half = max(0, max_chars // 2)
        out_tail, out_cut = _tail(text, half)
        err_tail, err_cut = _tail(stderr, half)
        truncated = out_cut or err_cut
        return NormalizedObservation(
            "process", ok=(exit_code == 0),
            summary=f"process exit {exit_code}" + (" (stderr, bounded)" if err_tail and exit_code else ""),
            fields={"exit_code": int(exit_code), "stdout_tail": out_tail,
                    "stderr_tail": err_tail},
            failure_names=[], truncated=truncated, raw_artifact=text)
    except Exception as exc:
        return _fallback("process", text, exc)


def normalize_stage13(payload: dict, *,
                      max_chars: int = MAX_OBSERVATION_CHARS) -> NormalizedObservation:
    """Stage-1/3 наблюдение: action/target/effect/fresh observation/verification
    evidence — со спокойными фолбэками, никогда не бросает."""
    try:
        if not isinstance(payload, dict):
            raw = json.dumps(payload, default=str, ensure_ascii=False) if payload is not None else ""
            view, _ = bound_text(raw, max_chars)
            return NormalizedObservation(
                "stage13", ok=False, summary="stage13: payload is not a dict",
                fields={"error": "payload is not a dict", "payload_head": view},
                failure_names=[], truncated=len(view) < len(raw), raw_artifact=raw)
        raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
        action = str(payload.get("action") or "")
        target = str(payload.get("target") or "")
        effect = str(payload.get("effect") or "")
        obs = payload.get("observation") or payload.get("fresh_observation") or {}
        verification = payload.get("verification") or payload.get("evidence") or {}
        obs_str, _ = bound_text(json.dumps(obs, sort_keys=True, default=str), max_chars // 4)
        ver_str, _ = bound_text(json.dumps(verification, sort_keys=True, default=str),
                                max_chars // 4)
        truncated = len(raw) > max_chars
        summary = bound_text(
            f"stage13 {action} -> {target}: {effect} "
            f"(verification: {'present' if verification else 'absent'})",
            300)[0]
        return NormalizedObservation(
            "stage13", ok=bool(verification or payload.get("ok") or effect),
            summary=summary,
            fields={"action": action, "target": target, "effect": effect,
                    "observation": obs_str,
                    "verification_present": bool(verification)},
            failure_names=[], truncated=truncated, raw_artifact=raw)
    except Exception as exc:
        raw = json.dumps(payload, default=str, ensure_ascii=False) if payload is not None else ""
        return _fallback("stage13", raw, exc)
