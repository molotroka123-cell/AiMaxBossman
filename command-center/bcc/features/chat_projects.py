"""Проекты чатов — у каждого проекта своя папка (owner 31.10: «всё это пусть собирается в отдельной папке для удобного
поиска … разделять проекты, а так же выбор проекта перед созданием чата», «хороший файл менеджер … функция проекты»).

Проект = папка `<root>/<slug>`; root = `BCC_PROJECTS_ROOT` или `%USERPROFILE%\\Bossman\\Projects`. Список проектов
(имя, архив, время) — `<data_dir>/projects/projects.json`. Чат без проекта работает в `<root>/_inbox`.

Связь с чатами — существующая метка проекта треда (`threads-meta.json`, поле `project`, features/chat_threads): в ней
лежит slug проекта. Отдельного хранилища чатов, памяти или очереди здесь нет.

Задача чата получает `tasks.workspace_path` = папка проекта (чат без проекта — `_inbox`) ТОЛЬКО когда исполнитель —
локальная модель этого ПК (провайдер на loopback, не `:cloud`), агент не «только чат» и ему выданы файловые инструменты
(code.write / code.patch / terminal.run). Тогда же инструменты, которые агенту УЖЕ выданы, считают эту папку рабочим
корнем (`workspace_grant`), а независимый верификатор (finalize / review_gate) перечитывает файлы и в ней
(`verifier_roots`): ровно эта папка, не весь root, не соседние проекты. Новых инструментов агент не получает; правила
подтверждения (code.write без права terminal.run — ASK; host-команды терминала — ASK), запреты секретов/личных путей и
песочница терминала прежние. Строка «работай в папке проекта …» кладётся в КОНТЕКСТ промпта (conversation_context), а
не в сообщение владельца: роутер действий видит только набранный текст. Облачная модель (например GLM 5.3 Flash,
агент 46: `tools=[]`, `cloud_pc_access=false`) папку НЕ получает — в чате проекта ей сказано, что файлы она отдаёт
текстом в ответе; факты по агентам — `tools/prove_project_files.py`.

Файловый менеджер пульта: список / скачать / загрузить / удалить — только внутри папки проекта (путь резолвится и
проверяется по реальной папке; секреты, данные Bossman, `.bossman-mobile`, личные пути и устройства Windows — отказ).
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import secrets
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import Feature

router = APIRouter()

ROOT_ENV = "BCC_PROJECTS_ROOT"
INBOX = "_inbox"
INBOX_NAME = "Без проекта"
REGISTRY_VERSION = 1
SLUG_MAX = 40                     # = chat_threads.PROJECT_MAX_CHARS: slug живёт в метке треда
NAME_MAX = 80
LIST_MAX = 2000
SLUG_RE = re.compile(r"[^\W_][\w-]{0,39}", re.UNICODE)
_WINDOWS_DEVICE_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
                                   *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))})
#: Тот же список, что у скачивания вложений чата: браузер показывает их, не исполняя (html/svg — никогда inline).
_INLINE_SAFE = ("application/pdf", "text/plain")


def _fail(status: int, message: str, *, code: str, hint: str | None = None) -> HTTPException:
    detail: dict[str, Any] = {"message": message, "code": code}
    if hint:
        detail["hint"] = hint
    return HTTPException(status, detail)


# ======================================================================
# Корень, slug, реестр
# ======================================================================

def projects_root() -> Path:
    raw = (os.environ.get(ROOT_ENV) or "").strip()
    return Path(os.path.expanduser(raw)) if raw else Path.home() / "Bossman" / "Projects"


def _mobile_data() -> Path:
    from .chat_share_file import MOBILE_DATA
    return MOBILE_DATA


def _overlaps(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def root_refusal(root: Path, data_dir: Path) -> str | None:
    """Почему этот корень не годится для проектов (или None). Корень не может быть ни внутри данных Bossman /
    пульта, ни их предком, ни домашней папкой или корнем диска целиком, ни личной/секретной папкой."""
    from .tools_code import is_private_chat_or_personal_path
    try:
        real = root.expanduser().resolve()
    except OSError as exc:
        return f"папка проектов недоступна: {exc}"
    if real.parent == real:
        return "папка проектов не может быть корнем диска"
    with contextlib.suppress(OSError):
        if real == Path.home().resolve():
            return "папка проектов не может быть домашней папкой целиком"
    for own in (Path(data_dir), _mobile_data()):
        with contextlib.suppress(OSError):
            if _overlaps(real, own.resolve()):
                return "папка проектов пересекается с данными Bossman — укажите другую (BCC_PROJECTS_ROOT)"
    blocked, why = is_private_chat_or_personal_path(real)
    if blocked:
        return why
    return None


def slugify(name: str) -> str:
    """Имя папки из названия: буквы/цифры (любой алфавит), `-`; без пробелов, точек и зарезервированных имён."""
    text = unicodedata.normalize("NFKC", str(name or "")).casefold()
    text = re.sub(r"[^\w-]+", "-", text, flags=re.UNICODE).replace("_", "-")
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text[:SLUG_MAX].strip("-")


def slug_refusal(slug: str) -> str | None:
    from .tools_code import is_private_chat_or_personal_path
    if not slug or not SLUG_RE.fullmatch(slug):
        return "название проекта должно содержать буквы или цифры"
    if slug.split(".")[0].upper() in _WINDOWS_DEVICE_NAMES:
        return f"«{slug}» — зарезервированное имя Windows"
    blocked, _ = is_private_chat_or_personal_path(slug)
    if blocked:
        return f"«{slug}» совпадает с защищённой папкой (личные данные/секреты) — выберите другое название"
    return None


def _clean_name(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")
    if not text:
        raise _fail(422, "пустое название проекта", code="PROJECT_NAME_EMPTY")
    if len(text) > NAME_MAX:
        raise _fail(422, f"название проекта длиннее {NAME_MAX} символов", code="PROJECT_NAME_TOO_LONG")
    return text


def _registry_path(svc: Any) -> Path:
    return Path(svc.settings.data_dir) / "projects" / "projects.json"


def read_registry(svc: Any) -> dict[str, dict]:
    try:
        data = json.loads(_registry_path(svc).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, dict):
        return {}
    return {k: v for k, v in items.items() if isinstance(k, str) and SLUG_RE.fullmatch(k) and isinstance(v, dict)}


def _write_registry(svc: Any, items: dict[str, dict]) -> None:
    from .chat_threads import _write_json_atomic
    _write_json_atomic(_registry_path(svc), {"version": REGISTRY_VERSION, "items": items})


def _lock(svc: Any) -> asyncio.Lock:
    lock = getattr(svc, "_chat_projects_lock", None)
    if lock is None:
        lock = svc._chat_projects_lock = asyncio.Lock()
    return lock


def _root_or_fail(svc: Any) -> Path:
    root = projects_root()
    why = root_refusal(root, Path(svc.settings.data_dir))
    if why:
        raise _fail(409, why, code="PROJECTS_ROOT_REFUSED", hint=f"переменная {ROOT_ENV}")
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def project_dir(svc: Any, slug: str, *, create: bool = False) -> Path:
    """Папка проекта (или `_inbox`); всегда прямой потомок корня, не ссылка наружу."""
    if slug != INBOX:
        if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
            raise _fail(404, "проект не найден", code="PROJECT_NOT_FOUND")
        if slug not in read_registry(svc):
            raise _fail(404, "проект не найден", code="PROJECT_NOT_FOUND")
    root = _root_or_fail(svc)
    folder = root / slug
    if create or slug == INBOX:
        folder.mkdir(exist_ok=True)
    if folder.is_symlink() or (hasattr(folder, "is_junction") and folder.is_junction()):
        raise _fail(409, "папка проекта — ссылка на другое место; Bossman с ней не работает",
                    code="PROJECT_DIR_LINK")
    if not folder.is_dir():
        raise _fail(404, "папки проекта нет на диске", code="PROJECT_DIR_MISSING")
    if folder.resolve().parent != root:
        raise _fail(409, "папка проекта вне корня проектов", code="PROJECT_DIR_OUTSIDE")
    return folder.resolve()


def _thread_counts(svc: Any) -> dict[str, int]:
    from .chat_threads import _entry, _read_meta
    meta = _read_meta(svc)
    reg = read_registry(svc)
    counts: dict[str, int] = {}
    for thread_id in meta:
        entry = _entry(meta, thread_id)
        if entry["archived"]:
            continue
        slug = _resolve_label(entry["project"], reg)
        if slug:
            counts[slug] = counts.get(slug, 0) + 1
    return counts


def _resolve_label(label: str | None, reg: dict[str, dict]) -> str | None:
    if not label:
        return None
    if label in reg:
        return label
    slug = slugify(label)
    return slug if slug in reg else None


def _iso(ts: Any) -> str | None:
    try:
        return datetime.fromtimestamp(float(ts)).astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _public(slug: str, item: dict, root: Path, counts: dict[str, int]) -> dict:
    folder = root / slug
    return {"slug": slug, "name": str(item.get("name") or slug), "path": str(folder), "exists": folder.is_dir(),
            "archived": item.get("archived") is True, "threads": counts.get(slug, 0),
            "created_at": _iso(item.get("created_at")), "updated_at": _iso(item.get("updated_at"))}


# ======================================================================
# Чат → папка проекта; задача → workspace_path; инструменты → грант папки
# ======================================================================

def thread_project(svc: Any, thread_id: str) -> tuple[str, str]:
    """(slug, название) проекта треда; без проекта — (`_inbox`, «Без проекта»)."""
    from .chat_threads import _entry, _read_meta
    reg = read_registry(svc)
    slug = _resolve_label(_entry(_read_meta(svc), thread_id)["project"], reg)
    if slug is None:
        return INBOX, INBOX_NAME
    return slug, str(reg[slug].get("name") or slug)


def _is_loopback(url: str) -> bool:
    import ipaddress
    from urllib.parse import urlsplit
    host = (urlsplit(str(url or "")).hostname or "").strip("[]").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


async def agent_works_locally(svc: Any, agent: dict | None) -> bool:
    """Агент может работать с файлами проекта: не «только чат» и его модель — на ЭТОМ ПК (loopback, не `:cloud`)."""
    from ..db import fetch_one, models as models_t, providers as providers_t
    from ..provider_governance import is_cloud_offload
    from ..tools import chat_only
    if not isinstance(agent, dict) or chat_only(agent) or agent.get("model_id") is None:
        return False
    async with svc.db.session() as s:
        model = await fetch_one(s, models_t, int(agent["model_id"]))
        prov = await fetch_one(s, providers_t, int(model["provider_id"])) if model else None
    if not model or not prov:
        return False
    return _is_loopback(prov.get("base_url") or "") and not is_cloud_offload(prov, model)


async def _agent_row(svc: Any, agent_id: Any) -> dict | None:
    from ..db import agents as agents_t, fetch_one
    try:
        aid = int(agent_id)
    except (TypeError, ValueError):
        return None
    async with svc.db.session() as s:
        return await fetch_one(s, agents_t, aid)


#: Tools that touch files: only an agent holding one of them gets the folder (workspace_path) and the «работай в
#: папке» line. A tool-less local chat agent keeps its prompt exactly as before.
FILE_TOOLS = frozenset({"code.write", "code.patch", "terminal.run"})


def prompt_note(name: str, folder: Path, *, can_write: bool, inbox: bool = False, sandbox: bool = False) -> str:
    if inbox:
        return (f"[Рабочая папка этого чата (без проекта): {folder}. Новые файлы и код создавай там (пути относительно "
                f"неё). Другие папки и репозитории — только указывая root/cwd явно; там действуют обычные правила "
                f"подтверждения.]")
    if can_write:
        return (f"[Проект «{name}»: работай в папке проекта {folder}; все файлы и код создавай там "
                f"(code.write / code.patch с путями относительно этой папки, terminal.run с cwd в ней). "
                f"Другие папки — только указывая root/cwd явно; вне папки проекта действуют обычные правила "
                f"подтверждения.]")
    if sandbox:
        return (f"[Проект «{name}»: у тебя есть доступ к файлам ТОЛЬКО этой папки проекта {folder}: code.read, "
                f"code.write, code.patch с путями относительно неё. Создавай там файлы и код. Терминала и других "
                f"папок ПК нет; не пытайся выйти за папку проекта.]")
    return (f"[Проект «{name}», папка проекта {folder}. У этой модели нет доступа к файлам ПК: "
            f"давай код и файлы целиком в ответе — владелец сохранит их в папку проекта.]")


def with_note(prompt: str, note: str) -> str:
    """The note goes into the CONTEXT preamble (conversation_context), never into the owner's new message: the action
    contract / router classify `current_request(prompt)` only, and a note naming code.write must not route tools."""
    from .. import conversation_context as cc
    if prompt.startswith(cc.HEADER) and cc.MARKER in prompt:
        return cc.HEADER + note + "\n\n" + prompt[len(cc.HEADER):]
    return cc.compose([note]) + prompt


def _agent_file_tools(agent: dict | None) -> bool:
    from ..tools import _agent_tool_names
    return isinstance(agent, dict) and bool(FILE_TOOLS & set(_agent_tool_names(agent)))


async def bind_chat_turn(svc: Any, thread_id: str, agent_id: Any, prompt: str) -> tuple[str, Path | None]:
    """Prompt of a chat turn with the project line, and the folder for `tasks.workspace_path` (None: no binding).

    * project chat + local agent with file tools -> folder + «работай в папке проекта …»;
    * project chat + any other agent (cloud / chat-only / no file tools) -> no folder, «нет доступа к файлам ПК»;
    * chat without a project + local agent with file tools -> `<root>/_inbox` + a short inbox line;
    * chat without a project + any other agent -> prompt unchanged.
    A refused root or a broken folder never blocks the message: it goes out exactly as before."""
    try:
        slug, name = thread_project(svc, thread_id)
        folder = await asyncio.to_thread(project_dir, svc, slug, create=True)
    except HTTPException:
        return prompt, None
    agent = await _agent_row(svc, agent_id)
    can_write = _agent_file_tools(agent) and await agent_works_locally(svc, agent)
    if slug == INBOX:
        if not can_write:
            return prompt, None
        return with_note(prompt, prompt_note(name, folder, can_write=True, inbox=True)), folder
    if not can_write and await cloud_sandbox(svc, agent):
        return with_note(prompt, prompt_note(name, folder, can_write=False, sandbox=True)), folder
    return with_note(prompt, prompt_note(name, folder, can_write=can_write)), folder if can_write else None


async def cloud_sandbox(svc: Any, agent: dict | None) -> bool:
    """Owner 31.10: every CLOUD agent (chat-only included) works inside a project folder at most."""
    return isinstance(agent, dict) and agent.get("model_id") is not None and not await agent_works_locally(svc, agent)


async def sandbox_for(svc: Any, agent_id: Any, folder: Path | None) -> bool:
    """True when the task bound to `folder` runs as a project sandbox (cloud agent, a real project — never _inbox)."""
    if folder is None or folder.name == INBOX:
        return False
    return await cloud_sandbox(svc, await _agent_row(svc, agent_id))


async def set_task_workspace(svc: Any, task_id: int, folder: Path | None) -> None:
    if folder is None:
        return
    from ..db import fetch_one, tasks as tasks_t
    async with svc.db.session() as s:
        task = await fetch_one(s, tasks_t, int(task_id))
    values: dict[str, Any] = {"workspace_path": str(folder)[:500]}
    if task is not None and await sandbox_for(svc, task.get("agent_id"), folder):
        meta = dict(task.get("meta") or {}) if isinstance(task.get("meta"), dict) else {}
        meta["project_sandbox"] = True
        values["meta"] = meta
    async with svc.db.session() as s:
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == int(task_id)).values(**values))
        await s.commit()


async def workspace_grant(ctx: Any) -> Path | None:
    """Папка проекта этой задачи как рабочий корень уже выданных инструментов — или None.

    Только когда `workspace_path` задачи — существующая папка проекта (прямой потомок корня проектов, не ссылка),
    корень проектов допустим, и агент работает на локальной модели. Ничего не создаёт и не расширяет за эту папку."""
    from ..tools import project_sandboxed
    return await _grant(getattr(ctx, "svc", None), str(getattr(ctx, "workspace", "") or "").strip(),
                        getattr(ctx, "agent", None), sandbox=project_sandboxed(getattr(ctx, "task", None)))


async def task_project_root(svc: Any, task: dict) -> Path | None:
    """The same folder for the post-condition verifier (review_gate): the project folder this task's tools worked in."""
    if not isinstance(task, dict):
        return None
    agent = await _agent_row(svc, task.get("agent_id")) if task.get("agent_id") is not None else None
    from ..tools import project_sandboxed
    return await _grant(svc, str(task.get("workspace_path") or "").strip(), agent, sandbox=project_sandboxed(task))


async def verifier_roots(svc: Any, task: dict | None) -> list[Path]:
    """Roots the post-condition verifier (finalize / review_gate) may re-read: the owner's terminal roots, as before,
    plus this task's project folder — the folder its own tools wrote in — and nothing else."""
    from .tools_terminal import _roots
    roots = await _roots(svc)
    project = await task_project_root(svc, task) if task else None
    return [*roots, project] if project is not None and project not in roots else roots


async def _grant(svc: Any, raw: str, agent: Any, *, sandbox: bool = False) -> Path | None:
    if not raw or svc is None or getattr(svc, "settings", None) is None:
        return None
    root = projects_root()
    if root_refusal(root, Path(svc.settings.data_dir)):
        return None
    try:
        folder = Path(raw)
        if folder.is_symlink() or (hasattr(folder, "is_junction") and folder.is_junction()):
            return None
        real, real_root = folder.resolve(strict=True), root.resolve(strict=True)
    except OSError:
        return None
    if not real.is_dir() or real.parent != real_root:
        return None
    if real.name != INBOX and real.name not in read_registry(svc):
        return None
    if sandbox:       # cloud agent: a real project folder only (never _inbox); tools are already narrowed to the sandbox
        return real if real.name != INBOX else None
    if not await agent_works_locally(svc, agent):
        return None
    return real


# ======================================================================
# Пути файлового менеджера
# ======================================================================

def _rel_parts(rel: str | None) -> list[str]:
    from ..security import win_paths
    text = str(rel or "").replace("\\", "/").strip()
    if not text or text in (".", "/"):
        return []
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text) or "\x00" in text:
        raise _fail(422, "путь должен быть внутри папки проекта", code="PROJECT_PATH_INVALID")
    parts = [p for p in text.split("/") if p not in ("", ".")]
    for p in parts:
        if p == "..":
            raise _fail(422, "путь должен быть внутри папки проекта", code="PROJECT_PATH_INVALID")
        try:
            win_paths._screen(p)
        except win_paths.PathRefused as exc:
            raise _fail(422, f"недопустимое имя: {exc}", code="PROJECT_PATH_INVALID") from None
        if p.split(".")[0].rstrip(" ").upper() in _WINDOWS_DEVICE_NAMES:
            raise _fail(422, f"«{p}» — зарезервированное имя Windows", code="PROJECT_PATH_INVALID")
    return parts


def path_refusal(target: Path, data_dir: Path, rel: str, *, for_write: bool = False) -> str | None:
    """Те же запреты, что у `chat.share_file` и code.*: секреты, данные Bossman и пульта, личные пути (`rel` — путь
    внутри проекта: имя папки проекта и корня в проверку личных путей не входит)."""
    from .pc_agent_policy_paths import is_t3_secret_or_protected_path
    from .tools_code import is_private_chat_or_personal_path
    secret, why = is_t3_secret_or_protected_path(str(target), for_write=for_write, data_dir=data_dir)
    if secret:
        return why
    blocked, why = is_private_chat_or_personal_path(rel)
    if blocked:
        return why
    for own in (Path(data_dir), _mobile_data()):
        with contextlib.suppress(OSError):
            o = own.resolve()
            if target == o or o in target.parents:
                return "данные Bossman отдавать нельзя"
    return None


def confined(svc: Any, slug: str, rel: str | None, *, for_write: bool = False) -> tuple[Path, Path]:
    """(папка проекта, цель) — цель резолвится по реальной ФС и обязана остаться внутри папки проекта."""
    base = project_dir(svc, slug)
    parts = _rel_parts(rel)
    target = base.joinpath(*parts).resolve() if parts else base
    if target != base and base not in target.parents:
        raise _fail(403, "путь выходит за папку проекта", code="PROJECT_PATH_OUTSIDE")
    if target != base:
        why = path_refusal(target, Path(svc.settings.data_dir), _rel(base, target), for_write=for_write)
        if why:
            raise _fail(403, f"этот файл недоступен: {why}", code="PROJECT_PATH_REFUSED")
    return base, target


def _rel(base: Path, target: Path) -> str:
    return "" if target == base else target.relative_to(base).as_posix()


def _list_dir_sync(svc: Any, base: Path, folder: Path) -> dict:
    data_dir = Path(svc.settings.data_dir)
    entries, hidden, truncated = [], 0, False
    with os.scandir(folder) as it:
        for de in it:
            if len(entries) >= LIST_MAX:
                truncated = True
                break
            try:
                p = Path(de.path)
                real = p.resolve()
                if real != base and base not in real.parents:      # ссылка наружу — не показываем
                    hidden += 1
                    continue
                if path_refusal(real, data_dir, _rel(base, real)) is not None:
                    hidden += 1
                    continue
                st = de.stat()
                is_dir = de.is_dir()
            except OSError:
                hidden += 1
                continue
            entries.append({"name": de.name, "path": _rel(base, p), "is_dir": is_dir,
                            "size": None if is_dir else st.st_size, "mtime": _iso(st.st_mtime)})
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].casefold()))
    return {"entries": entries, "hidden": hidden, "truncated": truncated}


# ======================================================================
# HTTP: проекты
# ======================================================================

class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=NAME_MAX + 40)


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, max_length=NAME_MAX + 40)
    archived: bool | None = None


def _svc(request: Request) -> Any:
    return request.app.state.svc


@router.get("/projects")
async def list_projects(request: Request, archived: bool = False):
    """Проекты (папки) с числом чатов; `inbox` — куда идут файлы чатов без проекта."""
    svc = _svc(request)
    root = await asyncio.to_thread(_root_or_fail, svc)
    reg = read_registry(svc)
    counts = _thread_counts(svc)
    items = [_public(slug, item, root, counts) for slug, item in reg.items()
             if (item.get("archived") is True) == bool(archived)]
    items.sort(key=lambda it: it["name"].casefold())
    return {"root": str(root), "inbox": {"slug": INBOX, "name": INBOX_NAME, "path": str(root / INBOX)},
            "items": items}


@router.post("/projects", status_code=201)
async def create_project(body: ProjectIn, request: Request):
    svc = _svc(request)
    name = _clean_name(body.name)
    slug = slugify(name)
    why = slug_refusal(slug)
    if why:
        raise _fail(422, why, code="PROJECT_NAME_INVALID")
    root = await asyncio.to_thread(_root_or_fail, svc)
    async with _lock(svc):
        reg = read_registry(svc)
        if slug in reg:
            raise _fail(409, f"проект «{reg[slug].get('name') or slug}» уже есть", code="PROJECT_EXISTS")
        folder = root / slug
        if folder.is_symlink() or (hasattr(folder, "is_junction") and folder.is_junction()):
            raise _fail(409, "на месте папки проекта лежит ссылка", code="PROJECT_DIR_LINK")
        await asyncio.to_thread(folder.mkdir, exist_ok=True)
        now = time.time()
        reg[slug] = {"name": name, "archived": False, "created_at": now, "updated_at": now}
        await asyncio.to_thread(_write_registry, svc, reg)
    return _public(slug, reg[slug], root, {})


@router.patch("/projects/{slug}")
async def patch_project(slug: str, body: ProjectPatch, request: Request):
    """Переименовать (папка и slug не меняются — чаты и пути агентов остаются верными) или убрать в архив."""
    svc = _svc(request)
    fields = body.model_dump(exclude_unset=True)
    name = _clean_name(fields["name"]) if fields.get("name") is not None else None
    root = await asyncio.to_thread(_root_or_fail, svc)
    async with _lock(svc):
        reg = read_registry(svc)
        if slug not in reg:
            raise _fail(404, "проект не найден", code="PROJECT_NOT_FOUND")
        if name is not None:
            reg[slug]["name"] = name
        if fields.get("archived") is not None:
            reg[slug]["archived"] = bool(fields["archived"])
        reg[slug]["updated_at"] = time.time()
        await asyncio.to_thread(_write_registry, svc, reg)
    return _public(slug, reg[slug], root, _thread_counts(svc))


# ======================================================================
# HTTP: файлы проекта
# ======================================================================

@router.get("/projects/{slug}/files")
async def list_files(slug: str, request: Request, path: str | None = Query(default=None, max_length=1000)):
    svc = _svc(request)
    base, folder = await asyncio.to_thread(confined, svc, slug, path)
    if not folder.is_dir():
        raise _fail(404, "папка не найдена", code="PROJECT_PATH_NOT_FOUND")
    listing = await asyncio.to_thread(_list_dir_sync, svc, base, folder)
    rel = _rel(base, folder)
    parent = None if not rel else (rel.rsplit("/", 1)[0] if "/" in rel else "")
    return {"project": slug, "root": str(base), "path": rel, "parent": parent, **listing}


@router.get("/projects/{slug}/files/download")
async def download_file(slug: str, request: Request, path: str = Query(min_length=1, max_length=1000),
                        inline: bool = False):
    from fastapi.responses import FileResponse
    import mimetypes
    svc = _svc(request)
    _base, target = await asyncio.to_thread(confined, svc, slug, path)
    if not target.is_file():
        raise _fail(404, "файл не найден", code="PROJECT_PATH_NOT_FOUND")
    mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    safe_inline = inline and ((mime.startswith(("image/", "video/", "audio/")) and mime != "image/svg+xml")
                              or mime in _INLINE_SAFE)
    return FileResponse(target, media_type=mime, filename=target.name,
                        content_disposition_type="inline" if safe_inline else "attachment",
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})


@router.post("/projects/{slug}/files", status_code=201)
async def upload_file(slug: str, request: Request, filename: str = Query(min_length=1, max_length=255),
                      dir: str | None = Query(default=None, max_length=1000), overwrite: bool = False):
    """Файл в папку проекта: сырое тело потоком (`.part` → переименование). Предел — только свободный диск."""
    from .chat_threads import _over_budget, _upload_budget, safe_filename
    svc = _svc(request)
    base, folder = await asyncio.to_thread(confined, svc, slug, dir, for_write=True)
    if not folder.is_dir():
        raise _fail(404, "папка не найдена", code="PROJECT_PATH_NOT_FOUND")
    name = safe_filename(filename)
    _b, target = await asyncio.to_thread(confined, svc, slug, _rel(base, folder / name), for_write=True)
    if target.exists() and not (overwrite and target.is_file()):
        raise _fail(409, f"«{name}» уже есть в этой папке", code="PROJECT_FILE_EXISTS",
                    hint="загрузите с overwrite=1, чтобы заменить файл")
    budget = _upload_budget(folder)
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > budget:
        raise _over_budget(int(length), budget)
    part = folder / f".bossman-upload-{secrets.token_hex(6)}.part"
    digest, size = hashlib.sha256(), 0
    try:
        with open(part, "wb") as fh:
            async for chunk in request.stream():
                if not chunk:
                    continue
                size += len(chunk)
                if size > budget:
                    raise _over_budget(size, budget)
                digest.update(chunk)
                fh.write(chunk)
        os.replace(part, target)
    finally:
        with contextlib.suppress(OSError):
            part.unlink()
    st = target.stat()
    return {"ok": True, "project": slug, "name": name, "path": _rel(base, target), "size": size,
            "sha256": digest.hexdigest(), "mtime": _iso(st.st_mtime)}


@router.delete("/projects/{slug}/files")
async def delete_file(slug: str, request: Request, path: str = Query(min_length=1, max_length=1000),
                      confirm: str | None = Query(default=None, max_length=255)):
    """Удалить файл или ПУСТУЮ папку. `confirm` обязан совпасть с именем удаляемого — случайный вызов ничего не стирает."""
    svc = _svc(request)
    base, target = await asyncio.to_thread(confined, svc, slug, path, for_write=True)
    if target == base:
        raise _fail(422, "папку проекта целиком здесь удалить нельзя", code="PROJECT_DELETE_ROOT")
    if confirm != target.name:
        raise _fail(422, "удаление требует подтверждения именем файла", code="PROJECT_DELETE_CONFIRM_REQUIRED",
                    hint=f"confirm={target.name}")
    if target.is_dir():
        try:
            await asyncio.to_thread(target.rmdir)
        except OSError:
            raise _fail(409, "папка не пуста — сначала удалите файлы внутри", code="PROJECT_DIR_NOT_EMPTY") from None
    elif target.is_file():
        await asyncio.to_thread(target.unlink)
    else:
        raise _fail(404, "файл не найден", code="PROJECT_PATH_NOT_FOUND")
    return {"ok": True, "deleted": _rel(base, target)}


FEATURE = Feature(name="chat_projects", router=router)
