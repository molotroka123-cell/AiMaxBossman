"""V2.1 фаза O — минимальный снапшот/откат состояния BOSSMAN (пробел G13).

Что попадает в снапшот: **ссылки и контрольные суммы**, а не «весь компьютер».

  * БД (единственный носитель состояния) — консистентная копия через
    `VACUUM INTO`, с sha256;
  * конфиг — несекретные поля Settings + СПИСОК ключей `settings_kv`
    (значения зашифрованы и живут внутри копии БД, ключ шифрования — нет);
  * git: HEAD/ветка/грязь/worktrees основного репозитория (для справки — код
    снапшот НЕ откатывает);
  * активные миссии/задачи/раны, версии скиллов;
  * провайдеры и модели — БЕЗ ключей: только `key_fingerprint`
    (sha256 расшифрованного ключа, необратимо) и `has_key`;
  * производные хранилища (индекс памяти, индекс кода) — по строгому
    allowlist из `bcc.v2.derived_stores`, с sha256 и в рамках общего предела.
    Они ПЕРЕСТРАИВАЕМЫ: если файл не влез в предел, снапшот честно пишет
    `copied=false, rebuildable=true`, а откат — `restored=false,
    reason="not copied; rebuild required"`, и БД всё равно откатывается.

Чего в снапшоте нет и не будет:

  * `secret.key` (Fernet) и `token` — без них копия БД бесполезна для вора;
  * весов моделей, кэшей браузера, логов — ничего тяжёлого не копируется
    (жёсткий предел `MAX_ARTIFACT_BYTES` на файл и на весь артефакт).

Откат НИКОГДА не происходит молча: `POST /snapshots/{id}/restore` без
подтверждения заводит approval (`kind="snapshot_restore"`) и отвечает 202.
Реальная замена БД возможна только по approval со статусом `approved`.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.engine import make_url

from ..config import ROOT
from ..db import (approvals as approvals_t, events as events_t, fetch_one, missions as missions_t,
                  models as models_t, providers as providers_t, rows_dicts, settings_kv,
                  skill_versions as skill_versions_t, skills as skills_t,
                  snapshots as snapshots_t, task_runs as runs_t, tasks as tasks_t, utcnow)
from ..v2.derived_stores import copy_into_snapshot, restore_from_snapshot, safety_copy_current
from . import Feature

router = APIRouter()

MANIFEST_VERSION = 1
DB_FILE = "db.sqlite"
MANIFEST_FILE = "manifest.json"
SNAPSHOT_DIRNAME = "snapshots"
RESTORE_KIND = "snapshot_restore"

# Снапшот — это состояние, а не дистрибутив: артефакт больше этого предела
# означает, что внутрь попало что-то лишнее (веса, кэш, логи). Лучше честно
# отказать, чем тихо скопировать гигабайты.
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024

# Файлы data dir, которые не попадают в снапшот НИКОГДА (и почему).
EXCLUDED = [
    {"path": "secret.key", "reason": "ключ Fernet — с ним копия БД стала бы связкой секретов"},
    {"path": "token", "reason": "токен доступа к Control API"},
    {"path": "browser/", "reason": "профиль браузера: cookie и сессии сайтов"},
    {"path": "*.gguf, *.safetensors, models/", "reason": "веса моделей — не состояние"},
    {"path": "logs/", "reason": "логи не нужны для отката состояния"},
]

ACTIVE_TASK_STATUSES = ("queued", "running", "paused", "waiting_approval")
ACTIVE_MISSION_STATUSES = ("queued", "planning", "running", "paused")
COUNTED_TABLES = ("providers", "models", "agents", "tasks", "task_runs", "missions",
                  "skills", "skill_versions", "approvals", "tool_calls", "settings")


# ---------------------------------------------------------------- утилиты

def _jsonable(value: Any) -> Any:
    """datetime/Path/dataclass → JSON-совместимое (manifest уходит и в БД, и в файл)."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(secret: str | None) -> str | None:
    """Необратимый отпечаток секрета: позволяет сравнить «тот же ключ или нет»,
    но не восстановить его. Полного ключа и даже хвоста в снапшоте нет."""
    if not secret:
        return None
    return "sha256:" + hashlib.sha256(secret.encode()).hexdigest()[:16]


def _db_path(url: str) -> Path | None:
    """Файл SQLite из URL. Для не-SQLite — None (файловый откат невозможен)."""
    try:
        parsed = make_url(url)
    except Exception:
        return None
    if not parsed.drivername.startswith("sqlite") or not parsed.database:
        return None
    return Path(parsed.database)


def _root_dir(svc) -> Path:
    return Path(svc.settings.data_dir) / SNAPSHOT_DIRNAME


async def _git(repo: Path) -> dict:
    """HEAD/ветка/грязь/worktrees. Git может отсутствовать — тогда честный note."""
    info: dict[str, Any] = {"repo": str(repo)}
    if not (repo / ".git").exists():
        return {**info, "available": False, "note": "каталог не является git-репозиторием"}

    async def run(*args: str) -> str | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", str(repo), *args,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        except (FileNotFoundError, asyncio.TimeoutError, OSError):
            return None
        return out.decode("utf-8", "replace").strip() if proc.returncode == 0 else None

    head = await run("rev-parse", "HEAD")
    if head is None:
        return {**info, "available": False, "note": "git недоступен в этой среде"}
    status = await run("status", "--porcelain") or ""
    worktrees_raw = await run("worktree", "list", "--porcelain") or ""
    worktrees: list[dict] = []
    current: dict[str, Any] = {}
    for line in worktrees_raw.splitlines():
        if line.startswith("worktree "):
            if current:
                worktrees.append(current)
            current = {"path": line[9:]}
        elif line.startswith("HEAD "):
            current["head"] = line[5:]
        elif line.startswith("branch "):
            current["branch"] = line[7:].replace("refs/heads/", "")
        elif line.startswith("detached"):
            current["branch"] = "(detached)"
    if current:
        worktrees.append(current)
    return {**info, "available": True, "head": head,
            "branch": await run("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty_files": len([ln for ln in status.splitlines() if ln.strip()]),
            "worktrees": worktrees}


async def _table_counts(svc) -> dict[str, int]:
    counts: dict[str, int] = {}
    async with svc.db.session() as s:
        for name in COUNTED_TABLES:
            try:
                res = await s.execute(sa.text(f"SELECT COUNT(*) FROM {name}"))
                counts[name] = int(res.scalar() or 0)
            except sa.exc.SQLAlchemyError:
                continue      # таблицы может не быть в старой БД — это не ошибка
    return counts


async def _state(svc) -> dict:
    """Активное состояние: миссии, задачи, раны, скиллы, провайдеры, модели."""
    async with svc.db.session() as s:
        missions = rows_dicts((await s.execute(
            sa.select(missions_t.c.id, missions_t.c.title, missions_t.c.status,
                      missions_t.c.progress, missions_t.c.spent_usd)
            .where(missions_t.c.status.in_(ACTIVE_MISSION_STATUSES)))).fetchall())
        tasks = rows_dicts((await s.execute(
            sa.select(tasks_t.c.id, tasks_t.c.title, tasks_t.c.status, tasks_t.c.agent_id,
                      tasks_t.c.mission_id)
            .where(tasks_t.c.status.in_(ACTIVE_TASK_STATUSES))
            .order_by(tasks_t.c.id).limit(500))).fetchall())
        runs = rows_dicts((await s.execute(
            sa.select(runs_t.c.id, runs_t.c.task_id, runs_t.c.status, runs_t.c.attempt,
                      runs_t.c.model_alias)
            .where(runs_t.c.status.in_(("queued", "leased", "running")))
            .order_by(runs_t.c.id).limit(500))).fetchall())
        skill_rows = (await s.execute(
            sa.select(skills_t.c.id, skills_t.c.slug, skills_t.c.name,
                      skills_t.c.current_version_id, skill_versions_t.c.version)
            .select_from(skills_t.outerjoin(
                skill_versions_t, skill_versions_t.c.id == skills_t.c.current_version_id))
        )).fetchall()
        provider_rows = rows_dicts((await s.execute(sa.select(providers_t))).fetchall())
        model_rows = rows_dicts((await s.execute(
            sa.select(models_t.c.id, models_t.c.provider_id, models_t.c.alias, models_t.c.name,
                      models_t.c.kind, models_t.c.status, models_t.c.context_window,
                      models_t.c.caps))).fetchall())
        setting_keys = [r[0] for r in (await s.execute(sa.select(settings_kv.c.key))).fetchall()]

    providers = [{
        "id": p["id"], "name": p["name"], "kind": p["kind"], "base_url": p["base_url"],
        "has_key": bool(p.get("api_key_enc")),
        # только необратимый отпечаток: ни ключа, ни его хвоста
        "key_fingerprint": _fingerprint(svc.vault.decrypt(p.get("api_key_enc"))),
    } for p in provider_rows]

    return {
        "missions": _jsonable(missions),
        "tasks": _jsonable(tasks),
        "runs": _jsonable(runs),
        "skills": [{"id": r[0], "slug": r[1], "name": r[2],
                    "current_version_id": r[3], "version": r[4]} for r in skill_rows],
        "providers": providers,
        "models": _jsonable(model_rows),
        "settings_keys": sorted(setting_keys),
    }


async def _copy_db(svc, dest: Path) -> Path:
    """Консистентная копия SQLite (VACUUM INTO). Без остановки воркеров."""
    src = _db_path(svc.settings.database_url)
    if src is None:
        raise HTTPException(501, {
            "message": "снапшот файла БД поддержан только для SQLite",
            "hint": "для Postgres используйте pg_dump — это вне ответственности снапшота"})
    literal = str(dest).replace("'", "''")
    try:
        conn = await svc.db.engine.connect()
        try:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(sa.text(f"VACUUM INTO '{literal}'"))
        finally:
            await conn.close()
    except sa.exc.SQLAlchemyError:
        # старый SQLite без VACUUM INTO — честный фоллбэк на копию файла
        if not src.exists():
            raise HTTPException(500, {"message": "файл БД не найден", "hint": str(src)})
        await asyncio.to_thread(shutil.copy2, src, dest)
    if not dest.exists():
        raise HTTPException(500, {"message": "не удалось создать копию БД"})
    os.chmod(dest, 0o600)
    return dest


# ---------------------------------------------------------------- endpoints

@router.post("/snapshots")
async def create_snapshot(request: Request):
    """Снять снапшот состояния. Тяжёлое не копируется, секреты не расшифровываются."""
    svc = request.app.state.svc
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    body = body if isinstance(body, dict) else {}
    kind = str(body.get("kind") or "manual")[:16]
    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    name = str(body.get("name") or f"snapshot-{stamp}")[:200]

    root = _root_dir(svc)
    root.mkdir(parents=True, exist_ok=True)
    # каталог получает имя до вставки строки — id даём после, чтобы путь не менялся
    base = root / f"{stamp}-{kind}"
    suffix = 0
    while base.exists():
        suffix += 1
        base = root / f"{stamp}-{kind}-{suffix}"
    base.mkdir(parents=True)

    try:
        db_copy = await _copy_db(svc, base / DB_FILE)
        db_size = db_copy.stat().st_size
        if db_size > MAX_ARTIFACT_BYTES:
            raise HTTPException(507, {
                "message": f"копия БД {db_size // 1048576} МБ превышает предел снапшота "
                           f"({MAX_ARTIFACT_BYTES // 1048576} МБ)",
                "hint": "снапшот хранит состояние, а не архив; почистите run_events/checkpoints"})

        settings = svc.settings
        manifest = {
            "manifest_version": MANIFEST_VERSION,
            "name": name,
            "kind": kind,
            "created_at": utcnow().isoformat(),
            "database": {
                "engine": "sqlite",
                "source": str(_db_path(settings.database_url)),
                "file": DB_FILE,
                "sha256": _sha256(db_copy),
                "size_bytes": db_size,
                "table_counts": await _table_counts(svc),
            },
            "config": {
                "data_dir": str(settings.data_dir),
                "host": settings.host,
                "port": settings.port,
                "legacy_token_auth": settings.legacy_token_auth,
                "session_ttl_hours": settings.session_ttl_hours,
                "cookie_secure": settings.cookie_secure,
                # ключи настроек — да, значения (зашифрованные) — только внутри БД
                "settings_keys_count": 0,
            },
            "git": await _git(ROOT.parent),
            "state": await _state(svc),
            "secrets": {
                "plaintext_included": False,
                "encryption_key_included": False,
                "note": "ключи провайдеров хранятся только как sha256-отпечаток; "
                        "Fernet-ключ (secret.key) в снапшот не копируется",
            },
            "excluded": EXCLUDED,
            "files": [],
            "derived_stores": [],
        }
        manifest["config"]["settings_keys_count"] = len(manifest["state"]["settings_keys"])
        manifest["files"] = [{"name": DB_FILE, "sha256": manifest["database"]["sha256"],
                              "size_bytes": db_size}]

        # Производные хранилища копируем ПОСЛЕ БД и строго в остаток общего
        # предела: БД — состояние, индексы — производное от него, и при нехватке
        # места жертвуем именно ими (их можно перестроить, базу — нет).
        derived = await copy_into_snapshot(
            data_dir=Path(settings.data_dir), snapshot_base=base,
            per_file_limit=MAX_ARTIFACT_BYTES,
            total_limit=max(0, MAX_ARTIFACT_BYTES - db_size))
        manifest["derived_stores"] = derived
        manifest["files"].extend(
            {"name": d["snapshot_file"], "sha256": d["sha256"], "size_bytes": d["size_bytes"]}
            for d in derived if d.get("copied"))

        manifest_path = base / MANIFEST_FILE
        manifest_path.write_text(json.dumps(_jsonable(manifest), ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        os.chmod(manifest_path, 0o600)
        manifest["files"].append({"name": MANIFEST_FILE, "sha256": _sha256(manifest_path),
                                  "size_bytes": manifest_path.stat().st_size})
        total = sum(int(f["size_bytes"]) for f in manifest["files"])

        async with svc.db.session() as s:
            res = await s.execute(sa.insert(snapshots_t).values(
                name=name, kind=kind, path=str(base), manifest=_jsonable(manifest),
                size_bytes=total, created_at=utcnow()))
            sid = int(res.inserted_primary_key[0])
            await s.commit()
            row = await fetch_one(s, snapshots_t, sid)
    except Exception:
        shutil.rmtree(base, ignore_errors=True)   # не оставляем полуфабрикат
        raise

    await svc.bus.emit("snapshot.created", id=sid, name=name, kind=kind, size_bytes=total)
    return {"snapshot": _jsonable(row), "path": str(base), "size_bytes": total}


@router.get("/snapshots")
async def list_snapshots(request: Request):
    svc = request.app.state.svc
    async with svc.db.session() as s:
        rows = rows_dicts((await s.execute(
            sa.select(snapshots_t).order_by(snapshots_t.c.id.desc()).limit(100))).fetchall())
    items = []
    for row in rows:
        manifest = row.get("manifest") or {}
        items.append({
            "id": row["id"], "name": row["name"], "kind": row["kind"],
            "created_at": row["created_at"], "size_bytes": row["size_bytes"],
            "path": row["path"],
            "exists": Path(row["path"]).is_dir() if row.get("path") else False,
            "git_head": (manifest.get("git") or {}).get("head"),
            "table_counts": (manifest.get("database") or {}).get("table_counts", {}),
        })
    return {"snapshots": _jsonable(items)}


@router.get("/snapshots/{snapshot_id}")
async def get_snapshot(snapshot_id: int, request: Request):
    svc = request.app.state.svc
    async with svc.db.session() as s:
        row = await fetch_one(s, snapshots_t, snapshot_id)
    if not row:
        raise HTTPException(404, {"message": "снапшот не найден"})
    return {"snapshot": _jsonable(row)}


@router.get("/snapshots/{snapshot_id}/restore-preview")
async def restore_preview(snapshot_id: int, request: Request):
    """Что именно изменится при откате. Ничего не меняет."""
    svc = request.app.state.svc
    async with svc.db.session() as s:
        row = await fetch_one(s, snapshots_t, snapshot_id)
    if not row:
        raise HTTPException(404, {"message": "снапшот не найден"})

    manifest = row.get("manifest") or {}
    base = Path(row.get("path") or "")
    db_copy = base / DB_FILE
    warnings: list[str] = []

    artifact_present = db_copy.is_file()
    checksum_ok = False
    if artifact_present:
        checksum_ok = _sha256(db_copy) == (manifest.get("database") or {}).get("sha256")
        if not checksum_ok:
            warnings.append("контрольная сумма копии БД не совпадает с манифестом — "
                            "артефакт повреждён или подменён")
    else:
        warnings.append(f"файл снапшота отсутствует: {db_copy}")

    snap_counts = (manifest.get("database") or {}).get("table_counts", {}) or {}
    now_counts = await _table_counts(svc)
    changes = [{"table": t, "current": now_counts.get(t, 0), "snapshot": snap_counts.get(t, 0),
                "delta": snap_counts.get(t, 0) - now_counts.get(t, 0)}
               for t in sorted(set(snap_counts) | set(now_counts))
               if snap_counts.get(t, 0) != now_counts.get(t, 0)]

    now_state = await _state(svc)
    if now_state["tasks"]:
        warnings.append(f"сейчас активны задачи ({len(now_state['tasks'])}) — "
                        f"откат вернёт их к состоянию снапшота")
    if now_state["missions"]:
        warnings.append(f"сейчас активны миссии ({len(now_state['missions'])})")

    snap_git = manifest.get("git") or {}
    cur_git = await _git(ROOT.parent)
    git_block = {
        "snapshot_head": snap_git.get("head"), "current_head": cur_git.get("head"),
        "same": bool(snap_git.get("head")) and snap_git.get("head") == cur_git.get("head"),
        "snapshot_branch": snap_git.get("branch"), "current_branch": cur_git.get("branch"),
        "note": "снапшот не откатывает код: git-ссылки записаны только для сверки",
    }
    if not git_block["same"] and snap_git.get("head"):
        warnings.append("HEAD репозитория отличается от снапшота — код придётся "
                        "переключать вручную (git checkout)")

    snap_fp = {p.get("id"): p.get("key_fingerprint")
               for p in (manifest.get("state") or {}).get("providers", [])}
    now_fp = {p["id"]: p["key_fingerprint"] for p in now_state["providers"]}
    key_changes = [{"provider_id": pid, "was_in_snapshot": pid in snap_fp,
                    "key_same": snap_fp.get(pid) == now_fp.get(pid)}
                   for pid in sorted(set(snap_fp) | set(now_fp))]

    derived_entries = manifest.get("derived_stores") or []
    derived_preview = {"copied": [], "omitted": []}
    for entry in derived_entries:
        rel = str(entry.get("relative_path") or "")
        item = {"relative_path": rel, "size_bytes": entry.get("size_bytes"),
                "expected_sha256": entry.get("sha256")}
        if entry.get("copied"):
            snap_file = base / str(entry.get("snapshot_file") or "")
            item["present"] = snap_file.is_file()
            item["will_replace"] = str(Path(svc.settings.data_dir) / rel)
            if not item["present"]:
                warnings.append(f"производный индекс отсутствует в снапшоте: {rel} — "
                                f"после отката его придётся перестроить")
            derived_preview["copied"].append(item)
        else:
            item["reason"] = entry.get("reason") or "not copied; rebuild required"
            item["rebuildable"] = True
            derived_preview["omitted"].append(item)
    if derived_preview["omitted"]:
        warnings.append(f"производные индексы не попали в снапшот "
                        f"({len(derived_preview['omitted'])}) — откат БД пройдёт, "
                        f"но их нужно перестроить (переиндексация)")

    return _jsonable({
        "snapshot": {"id": row["id"], "name": row["name"], "kind": row["kind"],
                     "created_at": row["created_at"]},
        "artifact_present": artifact_present,
        "checksum_ok": checksum_ok,
        "restorable": artifact_present and checksum_ok
        and _db_path(svc.settings.database_url) is not None,
        "requires_approval": True,
        "approval_kind": RESTORE_KIND,
        "database": {"changes": changes, "current": now_counts, "snapshot": snap_counts},
        "active_now": {"tasks": now_state["tasks"], "missions": now_state["missions"],
                       "runs": now_state["runs"]},
        "git": git_block,
        "providers": key_changes,
        "derived_stores": derived_preview,
        "will_replace": [str(_db_path(svc.settings.database_url) or "")]
        + [x["will_replace"] for x in derived_preview["copied"]],
        "will_not_touch": ["secret.key", "token", "рабочее дерево git", "веса моделей"],
        "warnings": warnings,
    })


async def _approval_already_used(svc, approval_id: int) -> bool:
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(events_t.c.data).where(
            events_t.c.kind == "snapshot.restored"))).fetchall()
    return any((r[0] or {}).get("approval_id") == approval_id for r in rows)


@router.post("/snapshots/{snapshot_id}/restore")
async def restore(snapshot_id: int, request: Request):
    """Откат. Молча не выполняется НИКОГДА: нужен approved-approval.

    Без `approval_id` — заводим approval и отвечаем 202. С неодобренным — 403.
    """
    svc = request.app.state.svc
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}

    async with svc.db.session() as s:
        row = await fetch_one(s, snapshots_t, snapshot_id)
    if not row:
        raise HTTPException(404, {"message": "снапшот не найден"})

    manifest = row.get("manifest") or {}
    base = Path(row.get("path") or "")
    db_copy = base / DB_FILE
    target = _db_path(svc.settings.database_url)
    if target is None:
        raise HTTPException(501, {"message": "файловый откат поддержан только для SQLite"})
    if not db_copy.is_file():
        raise HTTPException(410, {"message": "артефакт снапшота отсутствует на диске",
                                  "hint": str(db_copy)})

    approval_id = body.get("approval_id")
    if approval_id in (None, "", 0):
        appr = await svc.approvals.create(
            kind=RESTORE_KIND,
            preview=(f"Откат BOSSMAN к снапшоту #{snapshot_id} «{row.get('name')}» "
                     f"от {row.get('created_at')}.\n"
                     f"Будет заменена база: {target}\n"
                     f"Текущее состояние сохраняется рядом как pre-restore."))
        raise HTTPException(202, {
            "message": "откат требует подтверждения — он не выполняется автоматически",
            "approval_id": appr.get("id"), "snapshot_id": snapshot_id,
            "hint": "подтвердите approval и повторите запрос с approval_id"})

    try:
        approval_id = int(approval_id)
    except (TypeError, ValueError):
        raise HTTPException(400, {"message": "approval_id должен быть числом"})

    async with svc.db.session() as s:
        appr = await fetch_one(s, approvals_t, approval_id)
    if not appr or appr.get("kind") != RESTORE_KIND:
        raise HTTPException(403, {"message": "подтверждение отката не найдено",
                                  "hint": "запросите откат без approval_id — approval заведётся"})
    if appr.get("status") != "approved":
        raise HTTPException(403, {
            "message": f"откат запрещён: подтверждение в статусе «{appr.get('status')}»",
            "hint": "нужен approval со статусом approved"})
    if await _approval_already_used(svc, approval_id):
        raise HTTPException(403, {"message": "это подтверждение уже использовано",
                                  "hint": "одно подтверждение — один откат"})

    if _sha256(db_copy) != (manifest.get("database") or {}).get("sha256"):
        raise HTTPException(409, {"message": "контрольная сумма артефакта не совпадает",
                                  "hint": "снапшот повреждён — откат отменён"})

    # 1. подстраховка: текущее состояние уходит рядом, откат обратим
    safety_dir = _root_dir(svc) / f"pre-restore-{utcnow().strftime('%Y%m%d-%H%M%S')}"
    safety_dir.mkdir(parents=True, exist_ok=True)
    safety_db = safety_dir / DB_FILE
    await _copy_db(svc, safety_db)
    derived_entries = manifest.get("derived_stores") or []
    safety_derived = await safety_copy_current(
        data_dir=Path(svc.settings.data_dir), safety_dir=safety_dir, entries=derived_entries)

    # реестр снапшотов переживает откат: точки отката — не то состояние, которое
    # откатывают, иначе после первого же отката вернуться было бы некуда
    async with svc.db.session() as s:
        keep_rows = rows_dicts((await s.execute(sa.select(snapshots_t))).fetchall())

    # 2. подмена файла БД: соединения закрываем, WAL/SHM сносим — иначе SQLite
    #    «воскресит» часть старых страниц из журнала
    await svc.db.engine.dispose()
    for extra in (f"{target}-wal", f"{target}-shm"):
        Path(extra).unlink(missing_ok=True)
    await asyncio.to_thread(shutil.copy2, db_copy, target)
    os.chmod(target, 0o600)
    await svc.db.create_all()          # доступность + идемпотентные миграции
    await svc.db.ping()

    # 3. возвращаем реестр снапшотов (включая только что снятую pre-restore-копию)
    async with svc.db.session() as s:
        have = {r[0] for r in (await s.execute(sa.select(snapshots_t.c.id))).fetchall()}
        missing = [r for r in keep_rows if r["id"] not in have]
        if missing:
            # SQLite DateTime may come back as string after JSON roundtrip or driver
            from datetime import datetime as _dt

            for r in missing:
                ca = r.get("created_at")
                if isinstance(ca, str):
                    try:
                        # handle '2026-08-30T20:16:08.231921' or with Z
                        r["created_at"] = _dt.fromisoformat(ca.replace("Z", ""))
                    except Exception:
                        r["created_at"] = _dt.now()
            await s.execute(sa.insert(snapshots_t), missing)
            await s.commit()

    # 4. производные хранилища — только после того, как БД жива: индекс без
    #    своей базы бесполезен, а вот база без индекса работает (переиндексация)
    derived_restore = await restore_from_snapshot(
        data_dir=Path(svc.settings.data_dir), snapshot_base=base, entries=derived_entries)
    rebuild_required = [x["relative_path"] for x in derived_restore if not x.get("restored")]

    # 5. след в НОВОЙ базе: сама строка approval после отката исчезла — повторно
    #    использовать её нельзя (404), а событие остаётся аудитом
    async with svc.db.session() as s:
        await s.execute(sa.insert(events_t).values(
            ts=utcnow(), kind="snapshot.restored",
            data={"snapshot_id": snapshot_id, "approval_id": approval_id,
                  "by": str(body.get("by") or "owner")[:120],
                  "safety_copy": str(safety_db),
                  "derived_stores": derived_restore,
                  "derived_safety_copy": safety_derived}))
        await s.commit()
    await svc.bus.emit("snapshot.restored", id=snapshot_id, approval_id=approval_id,
                       safety_copy=str(safety_db),
                       derived_rebuild_required=rebuild_required)

    return {"ok": True, "snapshot_id": snapshot_id, "approval_id": approval_id,
            "restored_to": str(target), "safety_copy": str(safety_db),
            "table_counts": await _table_counts(svc),
            "derived_stores": derived_restore,
            "derived_rebuild_required": rebuild_required,
            "note": "код (git) не откатывался — сверьте HEAD из restore-preview"}


FEATURE = Feature(name="snapshot", router=router)

__all__ = ["FEATURE", "router", "MAX_ARTIFACT_BYTES", "RESTORE_KIND"]
