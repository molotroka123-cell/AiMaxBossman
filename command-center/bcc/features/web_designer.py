"""Веб-дизайнер (V2-фича): визуальная панель «пишем сайт» в Command Center.

Что даёт владельцу:
* проект = сайт: хранится в data_dir обычными файлами (HTML + метаданные),
  никакой магии в БД — код сайта можно открыть и без BOSSMAN;
* лайв-превью: GET .../preview отдаёт код с детерминированной нумерацией
  элементов и скриптом-пикером, iframe в UI показывает его вживую;
* точечные правки: клик по элементу в превью → инспектор → операция
  (текст, стиль, атрибуты, замена, удаление) применяется на сервере;
* генерация сайта по описанию: детерминированные шаблоны (см.
  bcc/web_designer_gen.py), UI показывает сборку пошагово, как стрим;
* версии: каждое сохранение — снимок, откат в один клик;
* AI-правка: если в реестре есть модель, правит выбранный элемент или весь
  код по текстовому запросу. Модели нет — честный 409, а не сломанная кнопка.

Границы модуля: не исполняет JS сайта, не ходит в интернет, не пишет в БД.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import stat
import tempfile
import time
from html import escape
from pathlib import Path
from weakref import WeakValueDictionary

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .. import web_designer_dom as dom
from .. import web_designer_gen as gen
from . import Feature

router = APIRouter()

FEATURE = Feature(name="web_designer", router=router)

# Превью — это ЧУЖОЙ код: его пишет модель, вставляет владелец, правит генератор.
# Отдаётся он с /api, то есть с origin самой панели, поэтому без песочницы скрипт
# внутри превью получил бы ровно те же права, что и панель: cookie сессии уходит
# автоматически с любым fetch, а CSRF-токен лежит в localStorage того же origin —
# то есть страница-визитка могла бы дойти до terminal.run. `sandbox allow-scripts`
# в CSP даёт кадру НЕПРОЗРАЧНЫЙ origin: ни cookie, ни localStorage, ни /api.
# Заголовок дублирует атрибут iframe намеренно: защита не должна зависеть от того,
# что клиент не забыл её выставить. Скрипты внутри при этом продолжают работать —
# пикер общается с панелью через postMessage, ему origin не нужен.
PREVIEW_HEADERS = {
    "Content-Security-Policy": "sandbox allow-scripts; form-action 'none'; frame-ancestors 'self'",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}

MAX_HTML_CHARS = dom.MAX_HTML_CHARS
MAX_PROJECTS = 100
MAX_VERSIONS = 50
AI_MAX_TOKENS = 8192
# Сколько документа помещается в запрос к модели при правке ВСЕГО документа.
# Раньше документ просто резался до этой длины, а ответ сохранялся как «полный
# документ»: всё, что было дальше, исчезало из сайта без единого сообщения.
# Теперь это ГРАНИЦА ОТКАЗА, а не тихое усечение.
AI_DOCUMENT_LIMIT = 120_000

_TAG_RE = re.compile(r"<[a-zA-Z!/]")          # «похоже на HTML», а не случайный текст
_NOTE_RE = re.compile(r"[\r\n\t]+")


# ---------------------------------------------------------------- хранилище

def _root(svc) -> Path:
    root = Path(svc.settings.data_dir) / "web_designer"
    _safe_path(root)
    return root


def _safe_path(path: Path) -> None:
    """Refuse symlinks and Windows junction/reparse components before I/O."""
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise HTTPException(status_code=403, detail="Путь проекта недоступен; доступ запрещён") from exc
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise HTTPException(status_code=403,
                                detail="Путь проекта содержит ссылку или junction; доступ запрещён")


def _safe_project(pdir: Path) -> None:
    for path in (pdir, pdir / "project.json", pdir / "current.html", pdir / "history"):
        _safe_path(path)


def _pdir(svc, pid: int) -> Path:
    if int(pid) < 1:
        raise HTTPException(status_code=404, detail="проект не найден")
    pdir = _root(svc) / str(int(pid))
    _safe_project(pdir)
    return pdir


def _write_atomic(path: Path, text: str) -> None:
    """Запись через временный файл в ТОМ ЖЕ каталоге и os.replace.

    Прямая запись `current.html` рвёт файл на части при падении процесса или
    при чтении превью в тот же момент: владелец получал полупустой сайт вместо
    своего. os.replace на одной файловой системе атомарен, поэтому читатель
    видит либо старую версию целиком, либо новую целиком.
    """
    _safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _safe_path(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


_LOCKS: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


class _ProjectLock:
    """One read/modify/write transaction across tasks AND BCC processes.

    Reuse the canonical POSIX/Windows advisory-lock utility. Its bounded wait
    runs off the event loop. Cancellation waits for acquisition cleanup, so a
    cancelled request cannot leave a late-acquired OS lock behind.
    """

    def __init__(self, pdir: Path) -> None:
        from bossman_shared.fable_budget import _CrossProcessFileLock
        key = str(pdir.absolute())
        self.local = _LOCKS.get(key)
        if self.local is None:
            self.local = asyncio.Lock()
            _LOCKS[key] = self.local
        # Outside the project: deleting a project must not unlink a held lock
        # inode and let another process acquire a different lock with its name.
        self.shared = _CrossProcessFileLock(pdir.parent / ".locks" / pdir.name)
        self.held = False

    def locked(self) -> bool:
        return self.local.locked()

    def _acquire(self) -> None:
        _safe_path(self.shared.path)
        try:
            self.shared.__enter__()
            self.held = True
        except BaseException:
            # The existing lock utility leaves its handle open on timeout.
            if self.shared._handle is not None:
                self.shared._handle.close()
                self.shared._handle = None
            raise

    def _release(self) -> None:
        if self.held:
            self.held = False
            self.shared.__exit__(None, None, None)

    async def __aenter__(self):
        from bossman_shared.fable_budget import BudgetExhausted
        await self.local.acquire()
        pending = asyncio.create_task(asyncio.to_thread(self._acquire))
        try:
            await asyncio.shield(pending)
        except asyncio.CancelledError:
            try:
                await pending
            finally:
                self._release()
                self.local.release()
            raise
        except (BudgetExhausted, OSError) as exc:
            self.local.release()
            raise HTTPException(status_code=409, detail="Проект занят другим процессом; запись не выполнена") from exc
        except BaseException:
            self.local.release()
            raise
        return self

    async def __aexit__(self, *args):
        try:
            self._release()
        finally:
            self.local.release()


def _project_lock(pdir: Path) -> _ProjectLock:
    return _ProjectLock(pdir)


def _load_meta(pdir: Path) -> dict | None:
    _safe_project(pdir)
    try:
        raw = json.loads((pdir / "project.json").read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, ValueError):
        return None


def _save_meta(pdir: Path, meta: dict) -> None:
    _write_atomic(pdir / "project.json", json.dumps(meta, ensure_ascii=False, indent=2))


def _current_path(pdir: Path) -> Path:
    path = pdir / "current.html"
    _safe_path(path)
    return path


def _version_path(pdir: Path, version: int) -> Path:
    path = pdir / "history" / f"v{int(version)}.html"
    _safe_path(path)
    return path


def _read_code(pdir: Path, meta: dict | None = None) -> str:
    # project.json is the commit record, installed only after its history file.
    # current.html can already contain an uncommitted write after a crash.
    meta = _load_meta(pdir) if meta is None else meta
    version = int((meta or {}).get("version", 0))
    if version == 0:
        return ""
    try:
        return _version_path(pdir, version).read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=409, detail="Сохранённая версия проекта недоступна; код не подтверждён") from exc


def _next_id(svc) -> int:
    used = {int(d.name) for d in _root(svc).iterdir() if d.is_dir() and d.name.isdigit()}
    return (max(used) + 1) if used else 1


def _now() -> float:
    return round(time.time(), 3)


def _public_meta(meta: dict) -> dict:
    # id — ЧИСЛО. На диске он лежал строкой, а UI сравнивал его с числом через
    # ===, поэтому «последний проект» и ссылка ?project=N не срабатывали
    # никогда: панель молча открывала первый попавшийся проект.
    return {
        "id": int(meta.get("id") or 0), "name": meta.get("name", ""), "prompt": meta.get("prompt", ""),
        "template": meta.get("template", ""), "palette": meta.get("palette", ""),
        "version": int(meta.get("version", 0)),
        "created_at": meta.get("created_at"), "updated_at": meta.get("updated_at"),
    }


def _save_code(svc, pdir: Path, html: str, note: str, *,
               expect_version: int | None = None, fields: dict | None = None) -> dict:
    """Записать новую текущую версию + снимок в историю. Возвращает версию.

    Единственная точка записи кода проекта, поэтому предел размера проверяется
    здесь: в схемах запросов он стоит не на всех путях — ответ модели и откат
    к версии приходят мимо них.

    `fields` — поля meta, которые правка меняет заодно (шаблон, палитра). Они
    пишутся ТЕМ ЖЕ единственным сохранением: вторая запись «прочитал — изменил —
    записал» жила после освобождения блокировки, то есть там, где чужая правка
    уже не ждёт.

    `expect_version` — версия, на которой правка была построена. Если код за
    это время уже изменился (вторая вкладка, вторая AI-правка, ответ модели,
    пришедший позже), запись ОТКЛОНЯЕТСЯ с 409. Молча затирать чужую работу
    последним пришедшим — худшее, что может сделать редактор.
    """
    if len(html) > MAX_HTML_CHARS:
        raise HTTPException(status_code=413,
                            detail=f"документ больше предела в {MAX_HTML_CHARS} символов")
    meta = _load_meta(pdir) or {}
    current_version = int(meta.get("version", 0))
    if expect_version is not None and int(expect_version) != current_version:
        raise HTTPException(
            status_code=409,
            detail=(f"код изменился за время правки: вы правили версию {int(expect_version)}, "
                    f"сейчас сохранена {current_version}. Обновите превью и повторите — "
                    "чужая правка не затёрта"))
    version = current_version + 1
    history = pdir / "history"
    history.mkdir(parents=True, exist_ok=True)
    # Снимок сначала, текущий файл — вторым: обрыв между ними оставляет лишний
    # снимок, обратный порядок оставил бы версию без снимка.
    _write_atomic(_version_path(pdir, version), html)
    _write_atomic(_current_path(pdir), html)
    meta.update({
        "id": pdir.name, "version": version, "updated_at": _now(),
    })
    meta.update(fields or {})
    versions = list(meta.get("versions") or [])
    versions.append({"version": version, "note": _note(note), "ts": meta["updated_at"],
                     "chars": len(html)})
    meta["versions"] = versions[-MAX_VERSIONS:]
    _save_meta(pdir, meta)
    # Держим каталог истории в пределах MAX_VERSIONS снимков. Сортировка ЧИСЛОВАЯ:
    # по именам «v10» идёт раньше «v9», поэтому лексикографический срез удалял
    # снимки, которые остаются в списке версий, и откат к ним отвечал 404.
    snapshots = sorted(history.glob("v*.html"), key=_snapshot_number)
    for stale in snapshots[:-MAX_VERSIONS]:
        stale.unlink(missing_ok=True)
    return _public_meta(meta)


def _snapshot_number(path: Path) -> int:
    try:
        return int(path.stem[1:])
    except ValueError:
        return -1


def _note(note: str) -> str:
    return " ".join(str(note or "").split())[:120]


async def _ensure_dir_layout(svc) -> None:
    _root(svc).mkdir(parents=True, exist_ok=True)


def _require_project(svc, pid: int) -> tuple[Path, dict]:
    pdir = _pdir(svc, pid)
    meta = _load_meta(pdir)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"проект {pid} не найден")
    return pdir, meta


# ---------------------------------------------------------------- модели запросов

class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    prompt: str = Field(default="", max_length=4000)
    template: str = "auto"                     # auto | blank | ид шаблона
    palette: str = "auto"


class CodeIn(BaseModel):
    html: str = Field(min_length=1, max_length=MAX_HTML_CHARS)
    note: str = Field(default="", max_length=200)
    base_version: int | None = None            # версия, на которой правка построена


class GenerateIn(BaseModel):
    prompt: str = Field(default="", max_length=4000)
    name: str = Field(default="", max_length=120)
    template: str = "auto"
    palette: str = "auto"
    base_version: int | None = None


class EditIn(BaseModel):
    op: str = Field(min_length=1, max_length=16)
    bd_id: str | None = None
    path: str | None = None
    tag: str | None = Field(default=None, max_length=40)   # чем элемент был в превью
    text: str | None = None
    props: dict[str, str] | None = None
    attrs: dict[str, str] | None = None
    html: str | None = Field(default=None, max_length=MAX_HTML_CHARS)
    base_version: int | None = None            # версия, которую видел выбиравший
    replace_children: bool = False             # согласие снести вложенные теги


class AiEditIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    bd_id: str | None = None
    path: str | None = None
    base_version: int | None = None
    model_id: int | None = None                # выбор владельца; None — по здоровью


def _rank_models(models: list[dict]) -> list[dict]:
    """Registry models, healthiest first — the same ranking the router uses.

    Owner audit 2026-09-08, F3a: `model = models[0]` meant "the first row ever
    inserted", whatever its health, and no way to choose. The default is now
    the canonical health rank; the owner's explicit `model_id` wins over it."""
    from .. import model_health as mh
    def key(row: dict):
        return mh.HealthRecord.from_dict(row.get("health")).rank_key()
    return sorted(models, key=key)


@router.get("/web-designer/models")
async def ai_models(request: Request):
    """Models the AI edit may use, with health and the server's default marked."""
    from .. import model_health as mh
    svc = request.app.state.svc
    models = _rank_models(await svc.registry.list_models())
    items = []
    for i, row in enumerate(models):
        rec = mh.HealthRecord.from_dict(row.get("health"))
        items.append({"id": row["id"], "alias": row.get("alias") or row.get("name"), "name": row.get("name"),
                      "kind": row.get("kind"), "provider_id": row.get("provider_id"),
                      "health": {"status": rec.status, "detail": rec.detail},
                      "default": i == 0})
    return {"items": items, "default_model_id": models[0]["id"] if models else None,
            "chosen_by": "health_rank"}


# ---------------------------------------------------------------- endpoints

@router.get("/web-designer/projects")
async def list_projects(request: Request):
    svc = request.app.state.svc
    await _ensure_dir_layout(svc)
    items = []
    for pdir in _root(svc).iterdir():
        if not pdir.is_dir() or not pdir.name.isdigit():
            continue
        try:
            meta = _load_meta(pdir)
        except HTTPException as exc:
            if exc.status_code != 403:
                raise
            continue  # unsafe entries are not read or exposed in the list
        if meta:
            items.append(_public_meta(meta))
    items.sort(key=lambda m: (m.get("updated_at") or 0), reverse=True)
    return {"items": items[:MAX_PROJECTS]}


@router.post("/web-designer/projects")
async def create_project(body: ProjectIn, request: Request):
    svc = request.app.state.svc
    await _ensure_dir_layout(svc)
    async with _project_lock(_root(svc) / "_catalog"):
        existing = sum(1 for d in _root(svc).iterdir() if d.is_dir() and d.name.isdigit())
        if existing >= MAX_PROJECTS:
            # Раньше предел применялся только к списку: проекты продолжали копиться
            # на диске, а лишние просто не показывались. Отказ честнее молчания.
            raise HTTPException(status_code=409,
                                detail=f"достигнут предел в {MAX_PROJECTS} проектов — удалите ненужные")
        pid = _next_id(svc)
        pdir = _pdir(svc, pid)
        async with _project_lock(pdir):
            meta = {
                "id": str(pid), "name": " ".join(body.name.split())[:120],
                "prompt": body.prompt[:4000], "template": body.template,
                "palette": body.palette, "version": 0,
                "created_at": _now(), "updated_at": _now(), "versions": [],
            }
            _save_meta(pdir, meta)
            if body.template == "blank":
                blank = ("<!DOCTYPE html>\n<html lang=\"ru\">\n<head>\n<meta charset=\"utf-8\">\n"
                         "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
                         # Имя проекта — ввод владельца. Без экранирования
                         # «Кафе"></title><script>…» становился живым скриптом внутри
                         # СОХРАНЁННОГО сайта, который потом экспортируют без песочницы.
                         # Путь генератора это делал (web_designer_gen), blank — нет.
                         f"<title>{escape(meta['name'], quote=True)}</title>\n"
                         "</head>\n<body>\n\n</body>\n</html>\n")
                _save_code(svc, pdir, blank, "пустой проект")
                meta = _load_meta(pdir)
            else:
                result = gen.generate(body.prompt or body.name, name=body.name,
                                      template=body.template, palette=body.palette)
                meta["template"] = result["template"]
                meta["palette"] = result["palette"]
                meta["name"] = result["name"] if body.prompt else meta["name"]
                _save_meta(pdir, meta)
                final = result["steps"][-1]
                _save_code(svc, pdir, final, f"шаблон {result['template']}, палитра {result['palette']}")
                meta = _load_meta(pdir)
            return {"meta": _public_meta(meta or {}), "code": _read_code(pdir)}


@router.get("/web-designer/templates")
async def templates():
    return {"items": gen.templates_catalog(),
            "palettes": sorted(gen.PALETTES.keys())}


@router.get("/web-designer/projects/{pid}")
async def get_project(pid: int, request: Request):
    svc = request.app.state.svc
    pdir, meta = _require_project(svc, pid)
    return {"meta": _public_meta(meta), "code": _read_code(pdir, meta),
            "versions": list(meta.get("versions") or [])[-MAX_VERSIONS:]}


@router.put("/web-designer/projects/{pid}/code")
async def put_code(pid: int, body: CodeIn, request: Request):
    svc = request.app.state.svc
    pdir, _ = _require_project(svc, pid)
    if not _TAG_RE.search(body.html[:2000]):
        raise HTTPException(status_code=422, detail="это не похоже на HTML-документ")
    async with _project_lock(pdir):
        _require_project(svc, pid)
        meta = _save_code(svc, pdir, body.html, body.note or "правка кода",
                          expect_version=body.base_version)
    return {"ok": True, "meta": meta}


@router.post("/web-designer/projects/{pid}/generate")
async def generate_site(pid: int, body: GenerateIn, request: Request):
    """Собрать сайт по описанию. Хранится только финал; steps — для анимации в UI."""
    svc = request.app.state.svc
    pdir, meta = _require_project(svc, pid)
    base_version = body.base_version if body.base_version is not None else int(meta.get("version", 0))
    result = gen.generate(body.prompt or meta.get("prompt", ""), name=body.name or meta.get("name", ""),
                          template=body.template, palette=body.palette)
    async with _project_lock(pdir):
        _require_project(svc, pid)
        meta = _save_code(svc, pdir, result["steps"][-1],
                          f"генерация: {result['template']}/{result['palette']}",
                          expect_version=base_version,
                          fields={"template": result["template"], "palette": result["palette"]})
    return {"ok": True, "meta": meta, "template": result["template"],
            "palette": result["palette"], "steps": result["steps"]}


@router.post("/web-designer/projects/{pid}/edit")
async def edit_project(pid: int, body: EditIn, request: Request):
    """Точечная правка выбранного элемента текущего кода."""
    svc = request.app.state.svc
    pdir, meta_now = _require_project(svc, pid)
    async with _project_lock(pdir):
        _require_project(svc, pid)
        html = _read_code(pdir)
        if not html:
            raise HTTPException(status_code=409, detail="в проекте пока нет кода")
        # Выделение построено на нумерации ТОЙ версии, которую показывало превью.
        # Если код с тех пор изменился, тот же bd-N указывает уже на другой
        # элемент — и правка молча уходила бы в чужой тег.
        if body.base_version is not None:
            current = int((_load_meta(pdir) or {}).get("version", 0))
            if int(body.base_version) != current:
                raise HTTPException(
                    status_code=409,
                    detail=(f"код изменился (версия {current}, выделение сделано на "
                            f"{int(body.base_version)}) — обновите превью и выберите заново"))
        try:
            new_html, described = dom.apply_edit(html, body.model_dump())
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        note = f"{body.op}: {described.get('tag')}"
        if described.get("text"):
            note += f" «{described['text'][:40]}»"
        meta = _save_code(svc, pdir, new_html, note, expect_version=body.base_version)
    return {"ok": True, "meta": meta, "element": described}


@router.get("/web-designer/projects/{pid}/preview", response_class=HTMLResponse)
async def preview(pid: int, request: Request, nonce: str = ""):
    """HTML для iframe: с data-bd-id и пикером. Хранимый код не меняется.

    `nonce` панель придумывает сама на каждую загрузку кадра и кладёт в URL.
    Пикер возвращает его в каждом сообщении, и только по нему панель отличает
    свой пикер от чужой страницы, на которую кадр мог себя увести.
    """
    svc = request.app.state.svc
    pdir, _ = _require_project(svc, pid)
    html = _read_code(pdir)
    if not html:
        raise HTTPException(status_code=409, detail="в проекте пока нет кода")
    try:
        return HTMLResponse(dom.inject_preview(html, nonce=nonce), headers=PREVIEW_HEADERS)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/web-designer/projects/{pid}/ai-edit")
async def ai_edit(pid: int, body: AiEditIn, request: Request):
    """Правка кода моделью из реестра. Модели нет — честный отказ."""
    svc = request.app.state.svc
    pdir, base_meta = _require_project(svc, pid)
    html = _read_code(pdir, base_meta)
    if not html:
        raise HTTPException(status_code=409, detail="в проекте пока нет кода")
    # Версия, на которой строится правка. Ответ модели приходит через секунды,
    # и раньше он записывался поверх всего, что владелец успел сделать за это
    # время: чтение до await, запись после, без сверки. Теперь база правки
    # зафиксирована и проверяется при записи.
    base_version = int(base_meta.get("version", 0))
    if body.base_version is not None and int(body.base_version) != base_version:
        raise HTTPException(
            status_code=409,
            detail=(f"код изменился (версия {base_version}) — обновите превью и повторите"))
    models = await svc.registry.list_models()
    if not models:
        raise HTTPException(
            status_code=409,
            detail="нет настроенной модели — добавьте модель в реестре, тогда AI-правка станет доступна")
    if body.model_id is not None:
        model = next((m for m in models if int(m["id"]) == int(body.model_id)), None)
        if model is None:
            raise HTTPException(status_code=404, detail=f"модель {body.model_id} не найдена в реестре")
        chosen_by = "owner"
    else:
        model = _rank_models(models)[0]
        chosen_by = "health_rank"
    adapter, model_row = await svc.registry.adapter_for(int(model["id"]))

    element = None
    if body.bd_id or body.path:
        root = dom.parse_document(html)
        dom.assign_bd_ids(root)
        found = dom.resolve_element(root, body.bd_id, body.path)
        if found is None:
            raise HTTPException(status_code=404, detail="элемент не найден — обновите превью")
        element = dom.serialize(found)

    if element is not None:
        system = ("Ты — веб-дизайнер. Тебе дают HTML-фрагмент одного элемента и запрос. "
                  "Верни ТОЛЬКО заменяющий HTML-фрагмент этого же элемента, без пояснений, "
                  "без markdown-ограждений. Сохраняй смысл содержимого, меняй оформление/текст по запросу.")
        user = f"Элемент:\n{element}\n\nЗапрос: {body.prompt}"
    else:
        system = ("Ты — веб-дизайнер. Тебе дают полный HTML-документ и запрос на правку. "
                  "Верни ТОЛЬКО полный обновлённый HTML-документ, без пояснений "
                  "и без markdown-ограждений.")
        if len(html) > AI_DOCUMENT_LIMIT:
            # A9-01. Отказ вместо потери хвоста сайта: модель физически не
            # увидит документ целиком, а её ответ сохраняется КАК ПОЛНЫЙ
            # документ, и проверяется при этом только версия и общий предел
            # длины — не полнота. Правка отдельного элемента остаётся доступной
            # и на большом документе, поэтому выход есть, и он назван.
            raise HTTPException(
                status_code=413,
                detail=(f"документ длиннее {AI_DOCUMENT_LIMIT} символов — правка всего "
                        f"документа целиком отбросила бы {len(html) - AI_DOCUMENT_LIMIT} "
                        "символов. Выберите элемент и поправьте его."))
        user = f"Документ:\n{html}\n\nЗапрос: {body.prompt}"

    from ..providers import ProviderError
    try:
        result = await adapter.chat(model_row["name"],
                                    [{"role": "system", "content": system},
                                     {"role": "user", "content": user}],
                                    max_tokens=AI_MAX_TOKENS)
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=f"модель недоступна: {exc}")

    new_html = _extract_html(result.text, element is not None)
    if element is not None:
        root = dom.parse_document(html)
        dom.assign_bd_ids(root)
        found = dom.resolve_element(root, body.bd_id, body.path)
        if found is None:
            raise HTTPException(status_code=404, detail="элемент исчез при правке — повторите")
        try:
            _require_single_element(new_html)
            dom.op_replace(found, new_html)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"модель вернула негодный фрагмент: {exc}")
        dom._strip_bd_ids(root)
        new_html = dom.serialize(root)
    async with _project_lock(pdir):
        _require_project(svc, pid)
        meta = _save_code(svc, pdir, new_html, f"AI: {_note(body.prompt)}",
                          expect_version=base_version)
    return {"ok": True, "meta": meta, "model": model_row.get("alias") or model_row.get("name"),
            "model_id": int(model_row["id"]), "chosen_by": chosen_by}


def _require_single_element(fragment: str) -> None:
    """Замена элемента — ровно один элемент, и ничего вокруг него.

    Ответ без ```-ограждения приносит прелюдию и постлюдию модели («Вот
    обновлённая кнопка: … Готово!») соседними узлами, и они уезжали в сайт
    текстом страницы — правка элемента дописывала в документ болтовню.
    """
    nodes = [n for n in dom.parse_fragment(fragment) if n.kind != "text" or n.raw.strip()]
    if len(nodes) != 1 or nodes[0].kind != "element":
        raise ValueError("ожидался ровно один элемент без пояснений вокруг него")


def _extract_html(text: str, fragment: bool) -> str:
    """Достать HTML из ответа модели: срезать ```-ограждения и болтовню вокруг."""
    raw = str(text or "").strip()
    fence = re.search(r"```(?:html)?\s*(.+?)```", raw, re.S)
    if fence:
        raw = fence.group(1).strip()
    if fragment:
        return raw
    # A9-02. Раньше здесь стоял нежадный `.*?</html>`, то есть документ резался
    # по ПЕРВОМУ `</html>` — даже когда тот был строковым литералом внутри
    # `<script>var s="</html>";</script>` или в JSON-LD. Середина сайта
    # терялась молча. Настоящий конец документа — ПОСЛЕДНИЙ `</html>`, поэтому
    # поиск жадный.
    for pattern in (r"<!DOCTYPE.*</html>", r"<html.*</html>"):
        match = re.search(pattern, raw, re.S | re.I)
        if match:
            return match.group(0)
    if _TAG_RE.search(raw[:500]):
        return raw
    raise HTTPException(status_code=502, detail="модель вернула не HTML — попробуйте переформулировать")


@router.get("/web-designer/projects/{pid}/versions")
async def versions(pid: int, request: Request):
    svc = request.app.state.svc
    _, meta = _require_project(svc, pid)
    return {"items": list(meta.get("versions") or [])[-MAX_VERSIONS:]}


@router.post("/web-designer/projects/{pid}/versions/{version}/restore")
async def restore_version(pid: int, version: int, request: Request):
    svc = request.app.state.svc
    pdir, _ = _require_project(svc, pid)
    path = _version_path(pdir, version)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"версия {version} не сохранилась")
    html = path.read_text(encoding="utf-8")
    async with _project_lock(pdir):
        _require_project(svc, pid)
        meta = _save_code(svc, pdir, html, f"откат к версии {version}")
    return {"ok": True, "meta": meta, "code": html}


@router.delete("/web-designer/projects/{pid}")
async def delete_project(pid: int, request: Request):
    svc = request.app.state.svc
    pdir, _ = _require_project(svc, pid)
    async with _project_lock(pdir):
        _require_project(svc, pid)
        try:
            shutil.rmtree(pdir)
        except OSError as exc:
            raise HTTPException(status_code=409,
                                detail="Проект не удалён: файл занят или нет прав. "
                                       "Закройте файлы проекта и проверьте доступ.") from exc
        if pdir.exists():
            raise HTTPException(status_code=409, detail="Удаление проекта не подтверждено")
    return {"ok": True}
