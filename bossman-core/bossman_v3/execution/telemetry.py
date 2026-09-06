"""Автоматическая телеметрия реальных нагрузок, выведенная из durable TaskJournal.

Зачем отдельный слой: `scripts/real_workload_audit.py` умеет решать про железо
только по НАСТОЯЩИМ терминальным задачам. Пока такие записи создавались вручную,
аудит опирался на то, что кто-то не забыл их написать — то есть ни на что.
Здесь запись появляется сама, на терминальной границе исполнения.

Три правила, которые здесь важнее удобства:

1. ТЕЛЕМЕТРИЯ НАБЛЮДАТЕЛЬНА. Ни один сбой записи не меняет и не откатывает
   истину пользовательской задачи. `record_terminal_run` не бросает наружу
   вообще ничего; он возвращает `TelemetryOutcome`.
2. СБОЙ ВИДЕН. «Не записалось» не равно «нечего записывать»: причина уходит в
   `TelemetryOutcome.error` и в диагностический сайдкар рядом с корпусом.
3. ПИШЕТСЯ ТОЛЬКО НАБЛЮДЁННОЕ. Поля, которых никто не измерил (память, OOM,
   стоимость), ОТСУТСТВУЮТ в записи, а не равны нулю: ноль потребитель прочтёт
   как измеренный ноль. Стоимость требует ссылки на провайдерскую улику.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = 1
RECORD_TYPE = "bossman.real_workload_sample"
CORPUS_NAME = "real_workloads.jsonl"
DIAGNOSTICS_NAME = "telemetry_diagnostics.jsonl"
ENV_ROOT = "BOSSMAN_REAL_WORKLOAD_ROOT"
DEFAULT_ROOT = ".bossman-state/benchmarks"

# Одна запись — это факт о задаче, а не место для транскрипта. Всё, что не влезло
# в бюджет, обрезается по явным правилам, а не пишется «как получится».
MAX_RECORD_BYTES = 8192
MAX_TEXT_FIELD = 240
MAX_FAMILIES = 8
LOCK_TIMEOUT_S = 5.0
LOCK_STALE_S = 60.0

TERMINAL_STATUSES = ("passed", "failed", "blocked")


class TelemetryCorruption(RuntimeError):
    """Корпус нечитаем. Не поднимается наружу — попадает в диагностику."""


@dataclass(frozen=True)
class TelemetryOutcome:
    """Что случилось с наблюдением. Никогда не является частью истины задачи."""
    written: bool = False
    reason: str = ""
    path: Path | None = None
    record: dict[str, Any] | None = None
    error: str = ""

    def __bool__(self) -> bool:                     # `if outcome:` — «записалось»
        return self.written


# ------------------------------------------------------------------ helpers


def _ts(value: str) -> float | None:
    if not value:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _text(value: Any, limit: int = MAX_TEXT_FIELD) -> str:
    """Короткая однострочная строка. Обрезание явное, потому что бюджет записи конечен."""
    s = str(value if value is not None else "")
    s = " ".join(s.split())
    return s[:limit]


def _int(value: Any, default: int = 0, *, minimum: int | None = None) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError):
        out = default
    return out if minimum is None else max(minimum, out)


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and out not in (float("inf"), float("-inf")) else None


def corpus_root(context: Mapping[str, Any] | None = None) -> Path:
    ctx = dict(context or {})
    return Path(ctx.get("real_workload_telemetry_root")
                or os.getenv(ENV_ROOT)
                or DEFAULT_ROOT)


def corpus_path(root: Path) -> Path:
    """Путь корпуса внутри своего корня. Симлинк и выход за корень — отказ."""
    base = Path(root)
    base.mkdir(parents=True, exist_ok=True)
    base = base.resolve()
    path = base / CORPUS_NAME
    if path.is_symlink() or path.resolve().parent != base:
        raise TelemetryCorruption("real-workload corpus path escapes its root")
    return path


def workload_family(plan: Iterable[Any] | None, context: Mapping[str, Any] | None = None) -> str:
    """Семейство нагрузки. Явное значение из контекста, иначе — выведенное из
    типов действий плана (объективно доступный факт), иначе `unspecified`."""
    ctx = dict(context or {})
    explicit = _text(ctx.get("workload_family", ""), 64)
    if explicit:
        return explicit
    families: list[str] = []
    for step in plan or ():
        action = getattr(step, "action", None)
        action_type = _text(getattr(action, "action_type", ""), 64)
        head = action_type.split(".")[0] if action_type else ""
        if head and head not in families:
            families.append(head)
    if not families:
        return "unspecified"
    return "+".join(sorted(families)[:MAX_FAMILIES])


def _host_fingerprint() -> str:
    """Узел без секретов: стабильный хеш имени хоста, а не само имя."""
    import platform
    raw = f"{platform.node()}|{platform.machine()}|{platform.system()}"
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]


def sample_key(task_id: str, plan_digest: str, run_id: str) -> str:
    """Идентичность выборки. Статуса здесь НЕТ намеренно: одна терминальная
    задача — одна запись, даже если её терминальный статус уточнился после
    возобновления."""
    return hashlib.sha256("|".join((task_id, plan_digest, run_id)).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------- record


def journal_record(journal: Any, *, completed: bool, context: Mapping[str, Any] | None = None,
                   plan: Iterable[Any] | None = None) -> dict[str, Any]:
    """Наблюдение о ТЕРМИНАЛЬНОЙ задаче, выведенное из журнала, а не из текста модели."""
    ctx = dict(context or {})
    steps = list(getattr(journal, "steps", ()))
    task_id = _text(getattr(journal, "task_id", ""), 220)
    plan_digest = _text(getattr(journal, "plan_digest", ""), 128)

    updated = [t for t in (_ts(getattr(s, "updated_at", "")) for s in steps) if t is not None]
    started_at = _ts(getattr(journal, "created_at", ""))
    ended_at = max(updated) if updated else time.time()

    signed = [s for s in steps if s.finished and s.signature_valid(task_id)]
    verified = bool(completed and steps and len(signed) == len(steps))
    failed = any(getattr(s, "status", "") == "FAILED" for s in steps)
    status = "passed" if verified else ("failed" if failed else "blocked")
    duration = max(0.0, ended_at - started_at) if started_at is not None else 0.0

    run_id = _text(ctx.get("run_id", "") or f"{task_id}:{plan_digest}", 128)
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "record_type": RECORD_TYPE,
        "sample_key": sample_key(task_id, plan_digest, run_id),
        "task_id": task_id,
        "run_id": run_id,
        "plan_digest": plan_digest,
        "workload_family": workload_family(plan, ctx),
        "status": status,
        "verified": verified,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_s": round(duration, 6),
        "human_interventions": _int(ctx.get("human_interventions", 0), minimum=0),
        "retries": _int(ctx.get("retries", 0), minimum=0),
        "concurrency": _int(ctx.get("concurrency", 1), default=1, minimum=1),
        "steps_total": len(steps),
        "steps_verified": len(signed),
        "effects_verified": len(signed),
        "verification_summary": f"{len(signed)}/{len(steps)} steps carry a signed verified receipt",
        # Парная идентичность НАГРУЗКИ, а не прогона: baseline и candidate
        # сравниваются по одной и той же работе. plan_digest подходит ровно
        # потому, что покрывает действия, ожидания и политику.
        "pair_id": _text(ctx.get("pair_id", "") or plan_digest, 128),
        # Обязательные согласования считаются ОТДЕЛЬНО от лишних вмешательств:
        # согласование по проекту — не издержка, а требование.
        "mandatory_approvals": _int(ctx.get("mandatory_approvals", 0), minimum=0),
        "unsafe_events": _int(ctx.get("unsafe_events", 0), minimum=0),
        "evidence_ref": f"task_journal:{task_id}@{plan_digest}" if task_id else "",
        "provenance": {
            "source": "task_journal",
            "recorded_by": "bossman_v3.execution.telemetry",
            "recorded_at": time.time(),
            "host_fingerprint": _host_fingerprint(),
            "schema_version": SCHEMA_VERSION,
        },
    }

    # Ниже — только то, что кто-то действительно наблюдал. Отсутствие ключа и
    # ключ со значением 0 читаются потребителем по-разному, и это намеренно.
    mission_id = _text(ctx.get("mission_id", ""), 128)
    if mission_id:
        record["mission_id"] = mission_id
    node_id = _text(ctx.get("node_id", ""), 128)
    if node_id:
        record["node_id"] = node_id
    model = _text(ctx.get("model", "") or ctx.get("runtime_model", ""), 128)
    if model:
        record["model"] = model
    runtime = _text(ctx.get("runtime", ""), 128)
    if runtime:
        record["runtime"] = runtime
    executor = _text(ctx.get("executor_type", ""), 128)
    if executor:
        record["executor_type"] = executor

    peak = _float_or_none(ctx.get("peak_memory_gb"))
    if peak is not None and peak >= 0:
        record["peak_memory_gb"] = peak
    if isinstance(ctx.get("oom"), bool):
        record["oom"] = ctx["oom"]

    # Стоимость без провайдерской улики — выдумка. Без `cost_evidence` её нет.
    cost = _float_or_none(ctx.get("cost_usd"))
    evidence_ref = _text(ctx.get("cost_evidence", ""), 128)
    if cost is not None and cost >= 0 and evidence_ref:
        record["cost_usd"] = cost
        record["cost_evidence"] = evidence_ref

    blocked_at = _text(ctx.get("blocked_at", ""), 128)
    if blocked_at:
        record["blocked_at"] = blocked_at
    reason = _text(ctx.get("terminal_reason", ""), MAX_TEXT_FIELD)
    if reason:
        record["terminal_reason"] = reason
    return record


def _fit_budget(record: Mapping[str, Any]) -> dict[str, Any]:
    """Запись обязана быть ограниченной. Урезаются только необязательные поля;
    то, что валидирует потребитель (task_id/status/verified/duration_s), — никогда."""
    out = dict(record)
    for _ in range(4):
        if len(json.dumps(out, ensure_ascii=False, sort_keys=True).encode("utf-8")) <= MAX_RECORD_BYTES:
            return out
        for droppable in ("terminal_reason", "verification_summary", "blocked_at", "workload_family"):
            if droppable in out:
                out.pop(droppable)
                break
        else:
            break
    out = {k: v for k, v in out.items()
           if k in {"schema_version", "record_type", "sample_key", "task_id", "run_id",
                    "plan_digest", "status", "verified", "started_at", "ended_at",
                    "duration_s", "human_interventions", "retries", "concurrency",
                    "provenance", "truncated"}}
    out["truncated"] = True
    return out


# ------------------------------------------------------------------ writing


class _CorpusLock:
    """Межпроцессная блокировка корпуса с ограниченным ожиданием.

    Ждать бесконечно нельзя: телеметрия не имеет права задерживать завершение
    задачи. Не получили замок за LOCK_TIMEOUT_S — выборка пропускается, и это
    видно в диагностике, а не молчаливо.
    """

    def __init__(self, path: Path):
        self.path = path
        self.fd: int | None = None

    def __enter__(self) -> "_CorpusLock":
        deadline = time.monotonic() + LOCK_TIMEOUT_S
        while True:
            try:
                self.fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.write(self.fd, f"{os.getpid()} {time.time()}".encode())
                return self
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                except OSError:
                    age = 0.0
                if age > LOCK_STALE_S:
                    # Замок пережил своего владельца. Телеметрия наблюдательна:
                    # вечная блокировка корпуса хуже, чем взятый заново замок.
                    try:
                        self.path.unlink()
                    except OSError:
                        pass
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError("real-workload corpus is locked by another writer")
                time.sleep(0.02)

    def __exit__(self, *exc: Any) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        try:
            self.path.unlink()
        except OSError:
            pass


def _read_corpus(path: Path) -> tuple[list[dict[str, Any]], int]:
    """Существующие выборки и число нечитаемых строк. Битая строка не выбрасывается
    молча — она считается и попадает в диагностику."""
    if not path.exists():
        return [], 0
    rows: list[dict[str, Any]] = []
    corrupt = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            corrupt += 1
            continue
        if isinstance(item, dict):
            rows.append(item)
        else:
            corrupt += 1
    return rows, corrupt


def _atomic_write_all(path: Path, rows: list[dict[str, Any]]) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".real-workload-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            for item in rows:
                stream.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _diagnose(root: Path, event: Mapping[str, Any]) -> None:
    """Диагностика — тоже best-effort, но она существует: «телеметрия сломалась»
    обязано быть наблюдаемым состоянием, а не тишиной."""
    try:
        line = json.dumps({"at": time.time(), **dict(event)}, ensure_ascii=False, sort_keys=True)
        with (root / DIAGNOSTICS_NAME).open("a", encoding="utf-8") as stream:
            stream.write(line[:MAX_RECORD_BYTES] + "\n")
    except (OSError, TypeError, ValueError):
        pass


def record_terminal_run(journal: Any, *, completed: bool,
                        context: Mapping[str, Any] | None = None,
                        plan: Iterable[Any] | None = None) -> TelemetryOutcome:
    """Записать ровно одну выборку про терминальную задачу.

    Никогда не бросает. Возвращает наблюдаемый результат — вызывающий код может
    его залогировать, но НЕ имеет права менять из-за него исход задачи.
    """
    ctx = dict(context or {})
    if ctx.get("disable_real_workload_telemetry"):
        return TelemetryOutcome(False, "disabled")
    root = corpus_root(ctx)
    try:
        record = _fit_budget(journal_record(journal, completed=completed, context=ctx, plan=plan))
        if not record.get("task_id"):
            return TelemetryOutcome(False, "no task identity", record=record)
        path = corpus_path(root)
        with _CorpusLock(path.with_suffix(".jsonl.lock")):
            rows, corrupt = _read_corpus(path)
            if corrupt:
                _diagnose(root, {"event": "corpus_corrupt_lines", "lines": corrupt,
                                 "path": str(path)})
            key = record["sample_key"]
            for index, existing in enumerate(rows):
                if existing.get("sample_key") != key:
                    continue
                if existing.get("status") == record["status"]:
                    return TelemetryOutcome(False, "duplicate", path, record)
                # Терминальный статус того же прогона уточнился (например,
                # blocked -> passed после возобновления). Прошлый статус
                # СОХРАНЯЕТСЯ в записи: провал не должен исчезать бесследно.
                superseded = list(existing.get("supersedes") or [])
                superseded.append({"status": existing.get("status"),
                                   "recorded_at": (existing.get("provenance") or {}).get("recorded_at")})
                merged = dict(record)
                merged["supersedes"] = superseded[-8:]
                rows[index] = _fit_budget(merged)
                _atomic_write_all(path, rows)
                return TelemetryOutcome(True, "updated", path, rows[index])
            rows.append(record)
            _atomic_write_all(path, rows)
            return TelemetryOutcome(True, "appended", path, record)
    except (OSError, TimeoutError, ValueError, TypeError, TelemetryCorruption) as exc:
        # Единственная реакция на сбой наблюдения: сделать сбой видимым.
        _diagnose(root, {"event": "telemetry_write_failed", "error": f"{type(exc).__name__}: {exc}"})
        return TelemetryOutcome(False, "error", error=f"{type(exc).__name__}: {exc}")


def append_record(journal: Any, *, completed: bool,
                  context: Mapping[str, Any] | None = None) -> Path | None:
    """Совместимость с прежним вызовом: путь корпуса или None."""
    outcome = record_terminal_run(journal, completed=completed, context=context)
    return outcome.path
