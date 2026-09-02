"""Local Task Exchange V1 — Bossman-side worker (file transport, не второй task engine).

Контракт SHARED_APP_CONTRACT: приложение пишет `data/bossman/inbox/<task_id>.json`
внутри своего каталога (apps/<app_id>/data/bossman/), Bossman атомарно забирает
задачу, прогоняет через существующие policy/approvals, пишет результат в
`completed/` или `failed/`. Bossman НЕ запускает shell из JSON и НЕ импортирует
код приложений.

Гарантии: schema validation; atomic claim (os.replace); idempotency + anti-replay
(персистентный ledger); bounded retries (MAX_ATTEMPTS); crash recovery (claimed
возвращается в inbox на старте цикла); redaction результатов.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request

from ..plugin_security import redact
from . import Feature
from .apps import APPS_DIR, _load, _manifest_files

MAX_TASK_BYTES = 262144          # 256 KiB на задачу
MAX_ATTEMPTS = 3
LEDGER_NAME = "_ledger.json"
BUCKETS = ("inbox", "claimed", "completed", "failed", "artifacts")
EXTERNAL_CAPABILITIES = {"browser.read", "browser.write", "llm.reasoning",
                         "computer.ui", "llm.chat"}


# F-017: app_id и task_id становятся именами каталогов/файлов. Один сегмент
# без разделителей, без ведущей точки, без процентов (закодированный traversal).
_SAFE_SEGMENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,199}")


def is_safe_segment(value: Any) -> bool:
    """Ровно один безопасный сегмент пути: не пусто, не `.`/`..`, без `/` `\\` `%`."""
    return isinstance(value, str) and _SAFE_SEGMENT.fullmatch(value) is not None


# ------------------------------------------------------------------ layout

def exchange_root(app_id: str) -> Path:
    return APPS_DIR / app_id / "data" / "bossman"


def _buckets(app_id: str) -> dict[str, Path]:
    root = exchange_root(app_id)
    out: dict[str, Path] = {}
    for name in BUCKETS:
        d = root / name
        d.mkdir(parents=True, exist_ok=True)
        out[name] = d
    return out


def _ledger_path(dirs: dict[str, Path]) -> Path:
    return dirs["inbox"].parent / LEDGER_NAME


def _load_ledger(dirs: dict[str, Path]) -> dict[str, Any]:
    p = _ledger_path(dirs)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {"completed_ids": list(data.get("completed_ids") or []),
                        "completed_keys": list(data.get("completed_keys") or [])}
        except (OSError, ValueError):
            pass
    return {"completed_ids": [], "completed_keys": []}


def _save_ledger(dirs: dict[str, Path], ledger: dict[str, Any]) -> None:
    p = _ledger_path(dirs)
    tmp = p.with_name(LEDGER_NAME + ".tmp")
    tmp.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


# ------------------------------------------------------------------ manifest side

def known_apps() -> dict[str, dict[str, Any]]:
    """id → {app_id, dir, permissions}. id каталога обязан совпадать с manifest id."""
    out: dict[str, dict[str, Any]] = {}
    for p in _manifest_files():
        raw = _load(p)
        if not raw or not raw.get("id") or str(raw["id"]) != p.parent.name:
            continue
        perms = raw.get("permissions")
        out[str(raw["id"])] = {
            "app_id": str(raw["id"]),
            "dir": p.parent,
            "permissions": perms if isinstance(perms, dict) else {},
        }
    return out


def _effect(perms: dict[str, Any], capability: str) -> str:
    raw = perms.get(capability, "deny")
    if isinstance(raw, dict):
        raw = raw.get("default", "deny")
    low = str(raw).lower()
    return low if low in ("auto", "ask") else "deny"


class LocalTaskExchange:
    """Один цикл: inbox → validate → claim → policy → execute → result."""

    def __init__(self) -> None:
        self.executors: dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]] = {}

    async def _execute(self, app_id: str, task: dict[str, Any]) -> dict[str, Any]:
        """V1-исполнитель: детерминированные типы без внешних зависимостей.
        Задачи с browser/llm/computer capability в offline-прогоне честно
        отклоняются (no fake-green); live-исполнение — существующие Bossman
        инструменты (policy/approval/gateway), а не второй движок."""
        exec_fn = self.executors.get(str(task.get("type") or ""))
        if exec_fn is not None:
            return dict(await exec_fn(task) or {})
        if any(c in EXTERNAL_CAPABILITIES
               for c in (task.get("requested_capabilities") or [])):
            raise PermissionError(
                "task requires live Bossman tool execution (browser/llm/computer); "
                "not available in this run")
        return {"accepted": True, "type": task.get("type"),
                "note": "deterministic local completion (no external capability)"}

    def _fail(self, dirs: dict[str, Path], src: Path, task: dict | None,
              reason: str) -> None:
        task_id = str((task or {}).get("task_id") or src.stem or "unknown")
        dst = dirs["failed"] / src.name
        try:
            os.replace(src, dst)      # src → failed, затем честный FAILED-body
        except OSError:
            return
        body = {"task_id": task_id, "status": "FAILED", "error": reason[:500],
                "task": redact(task or {})}
        dst.write_text(json.dumps(body, ensure_ascii=False, indent=2),
                       encoding="utf-8")

    async def _handle_one(self, svc: Any, info: dict[str, Any], path: Path,
                          ledger: dict[str, Any]) -> str:
        app_id = info["app_id"]
        dirs = _buckets(app_id)
        raw = path.read_bytes()
        if len(raw) > MAX_TASK_BYTES:
            self._fail(dirs, path, None, "task too large")
            return "failed"
        try:
            task = json.loads(raw.decode("utf-8"))
            if not isinstance(task, dict):
                raise ValueError("task is not a JSON object")
        except (ValueError, UnicodeDecodeError) as exc:
            self._fail(dirs, path, None, f"malformed JSON: {type(exc).__name__}")
            return "failed"

        task_id = str(task.get("task_id") or "")
        idem = str(task.get("idempotency_key") or "")
        if task_id and not is_safe_segment(task_id):
            # F-017: task_id даёт имя файла в completed/ — побег за каталог запрещён
            self._fail(dirs, path, {**task, "task_id": src_or_unknown(path)},
                       "task_id must be a single safe path segment")
            return "failed"

        # anti-replay / duplicate
        if (task_id and task_id in ledger.get("completed_ids", [])) or \
           (idem and idem in ledger.get("completed_keys", [])):
            self._fail(dirs, path, task, "replayed task (already completed)")
            return "replayed"

        if str(task.get("app_id") or "") != app_id:
            self._fail(dirs, path, task, "app_id does not match exchange directory")
            return "failed"
        if task.get("reply_to") != f"bossman/completed/{task_id}.json":
            self._fail(dirs, path, task,
                       "reply_to must point into bossman/completed/<task_id>")
            return "failed"

        caps = [str(c) for c in (task.get("requested_capabilities") or [])]
        unknown = [c for c in caps if c not in info["permissions"]]
        if unknown:
            self._fail(dirs, path, task,
                       f"unsupported capability: {', '.join(unknown)}")
            return "failed"
        denied = [c for c in caps if _effect(info["permissions"], c) == "deny"]
        if denied:
            self._fail(dirs, path, task,
                       f"capability denied by policy: {', '.join(denied)}")
            return "failed"
        ask_caps = [c for c in caps if _effect(info["permissions"], c) == "ask"]

        # atomic claim: rename атомарен на том же томе
        claimed = dirs["claimed"] / path.name
        try:
            os.replace(path, claimed)
        except OSError:
            return "failed"

        if ask_caps:
            try:
                approvals = getattr(svc, "approvals", None)
                if approvals is None:
                    raise PermissionError("approvals service unavailable")
                await approvals.create(
                    kind="task_exchange",
                    preview=f"app {app_id} task {task_id} type={task.get('type')} "
                            f"caps={ask_caps}")
            except Exception as exc:
                self._fail(dirs, claimed, task,
                           f"approval required: {type(exc).__name__}: {exc}")
                return "failed"
            return "needs_approval"

        try:
            result = await self._execute(app_id, task)
        except Exception as exc:
            # bounded retries: сбой возвращает задачу в inbox до MAX_ATTEMPTS,
            # затем честный FAILED (без тихой потери)
            attempts = int(task.get("attempts") or 0) + 1
            task["attempts"] = attempts
            reason = f"{type(exc).__name__}: {exc}"
            if attempts < MAX_ATTEMPTS:
                requeued = dirs["inbox"] / path.name
                requeued.write_text(json.dumps(task, ensure_ascii=False),
                                    encoding="utf-8")
                try:
                    claimed.unlink()
                except OSError:
                    pass
                return "failed"       # задача остаётся в inbox для retry
            self._fail(dirs, claimed, task, reason)
            return "failed"
            self._fail(dirs, claimed, task, reason)
            return "failed"

        completed = {
            "task_id": task_id,
            "app_id": app_id,
            "type": task.get("type"),
            "status": "COMPLETED",
            "result": redact(result),
            "requested_capabilities": caps,
            "finished_at": time.time(),
        }
        (dirs["completed"] / f"{task_id}.json").write_text(
            json.dumps(redact(completed), ensure_ascii=False, indent=2),
            encoding="utf-8")
        ledger.setdefault("completed_ids", []).append(task_id)
        if idem:
            ledger.setdefault("completed_keys", []).append(idem)
        _save_ledger(dirs, ledger)
        try:
            claimed.unlink()
        except OSError:
            pass
        return "processed"

    def _recover_claimed(self) -> int:
        """Crash recovery: claimed/ возвращается в inbox; повторное исполнение
        блокируется ledger'ом (anti-replay) — не теряется и не дублируется."""
        recovered = 0
        for app_id in known_apps():
            dirs = _buckets(app_id)
            for p in sorted(dirs["claimed"].glob("*.json")):
                try:
                    os.replace(p, dirs["inbox"] / p.name)
                    recovered += 1
                except OSError:
                    continue
        return recovered

    async def process(self, svc: Any = None) -> dict[str, int]:
        counts = {"processed": 0, "failed": 0, "replayed": 0, "needs_approval": 0}
        self._recover_claimed()
        for app_id, info in known_apps().items():
            dirs = _buckets(app_id)
            ledger = _load_ledger(dirs)
            for path in sorted(dirs["inbox"].glob("*.json")):
                outcome = await self._handle_one(svc, info, path, ledger)
                if outcome in counts:
                    counts[outcome] += 1
        return counts


# ------------------------------------------------------------------ API

router = APIRouter()
exchange = LocalTaskExchange()


@router.get("/taskxchange/queue")
async def queue_view(request: Request, app_id: str | None = None):
    apps = known_apps()
    if app_id:
        if app_id not in apps:
            raise HTTPException(status_code=404, detail="unknown app")
        apps = {app_id: apps[app_id]}
    out = []
    for app_id in sorted(apps):
        dirs = _buckets(app_id)
        out.append({
            "app_id": app_id,
            "inbox": len(list(dirs["inbox"].glob("*.json"))),
            "claimed": len(list(dirs["claimed"].glob("*.json"))),
            "completed": len(list(dirs["completed"].glob("*.json"))),
            "failed": len(list(dirs["failed"].glob("*.json"))),
        })
    return {"apps": out}


@router.post("/taskxchange/tick")
async def tick_now():
    counts = await exchange.process(None)
    return {"ok": True, **counts}


def src_or_unknown(path: Path) -> str:
    """Имя для файла отказа, когда task_id из JSON нельзя использовать как имя."""
    return path.stem if is_safe_segment(path.stem) else "unknown"


@router.get("/taskxchange/result/{app_id}/{task_id}")
async def result(app_id: str, task_id: str):
    # F-017: валидация ДО любого обращения к ФС (раньше _buckets делал mkdir по
    # app_id с `..`, создавая каталоги вне APPS_DIR). Только зарегистрированное
    # приложение, только один безопасный сегмент — и никаких mkdir на чтении.
    if not is_safe_segment(app_id):
        raise HTTPException(status_code=400, detail="invalid app_id")
    if not is_safe_segment(task_id):
        raise HTTPException(status_code=400, detail="invalid task_id")
    if app_id not in known_apps():
        raise HTTPException(status_code=404, detail="unknown app")
    root = exchange_root(app_id).resolve()
    for bucket in ("completed", "failed"):
        p = (root / bucket / f"{task_id}.json")
        if root not in p.resolve().parents:
            raise HTTPException(status_code=400, detail="invalid task_id")
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                raise HTTPException(status_code=502, detail="corrupted result file")
    raise HTTPException(status_code=404, detail="no result yet")


async def _tick(svc: Any) -> None:
    await exchange.process(svc)


FEATURE = Feature(name="task_exchange", router=router, tick=_tick, tick_seconds=2.0)
