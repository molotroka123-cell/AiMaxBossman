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
import base64
import binascii
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
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

# --- границы Epoch 4 -------------------------------------------------------
HOME_SLUG = gen.HOME_SLUG
MAX_PAGES = 40
MAX_COMPONENTS = 100
MAX_ASSETS = 200
MAX_ASSET_BYTES = 8 * 1024 * 1024
MAX_RESPONSIVE_RULES = 300

_TAG_RE = re.compile(r"<[a-zA-Z!/]")          # «похоже на HTML», а не случайный текст
_NOTE_RE = re.compile(r"[\r\n\t]+")

# Идентификатор страницы уезжает в ИМЯ ФАЙЛА на диске и в ссылку экспорта.
# Проверяется он до всякой склейки путей: `../../project.json` в качестве
# слага — это чтение и перезапись чужого файла, а не страница.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
# Служебные имена внутри каталога проекта: страница с таким слагом столкнулась
# бы с настоящим каталогом панели.
_RESERVED_SLUGS = frozenset({"history", "assets", "export", "pages", "project",
                             "components", "tokens", "responsive"})
# Идентификатор ассета — это ХЭШ содержимого, поэтому проверка формы заодно
# закрывает выход из каталога: `..` шестнадцатеричным не бывает.
_ASSET_ID_RE = re.compile(r"^[0-9a-f]{24}$")
_RULE_ID_RE = re.compile(r"^r[0-9]{1,9}$")

# Тип содержимого определяется ПО БАЙТАМ. Имя, пришедшее из браузера, —
# утверждение загрузившего, а не факт: `evil.html`, названный `logo.png`,
# отдавался бы как картинка ровно до первого браузера, решившего иначе.
_MAGIC: tuple[tuple[bytes, int, str, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", 0, "image/png", "png", "image"),
    (b"\xff\xd8\xff", 0, "image/jpeg", "jpg", "image"),
    (b"GIF87a", 0, "image/gif", "gif", "image"),
    (b"GIF89a", 0, "image/gif", "gif", "image"),
    (b"BM", 0, "image/bmp", "bmp", "image"),
    (b"wOFF", 0, "font/woff", "woff", "font"),
    (b"wOF2", 0, "font/woff2", "woff2", "font"),
    (b"OTTO", 0, "font/otf", "otf", "font"),
    (b"\x00\x01\x00\x00", 0, "font/ttf", "ttf", "font"),
    (b"true", 0, "font/ttf", "ttf", "font"),
    (b"ttcf", 0, "font/collection", "ttc", "font"),
)

ASSET_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Disposition": "inline",
    "Cache-Control": "no-store",
}


def sniff_asset(data: bytes) -> tuple[str, str, str] | None:
    """Байты → (content-type, расширение, род). Ничего не узнали — None.

    SVG сюда намеренно не входит: это исполняемый документ, а не картинка, и
    в экспортированном сайте он выполняется уже вне песочницы превью.
    """
    for magic, offset, content_type, ext, kind in _MAGIC:
        if data[offset:offset + len(magic)] == magic:
            return content_type, ext, kind
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "webp", "image"
    return None


# ---------------------------------------------------------------- хранилище

def _root(svc) -> Path:
    return Path(svc.settings.data_dir) / "web_designer"


def _pdir(svc, pid: int) -> Path:
    return _root(svc) / str(int(pid))


def _write_atomic(path: Path, text: str) -> None:
    """Запись через временный файл в ТОМ ЖЕ каталоге и os.replace.

    Прямая запись `current.html` рвёт файл на части при падении процесса или
    при чтении превью в тот же момент: владелец получал полупустой сайт вместо
    своего. os.replace на одной файловой системе атомарен, поэтому читатель
    видит либо старую версию целиком, либо новую целиком.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
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


_LOCKS: dict[str, asyncio.Lock] = {}


def _project_lock(pdir: Path) -> asyncio.Lock:
    """Одна правка проекта за раз внутри процесса.

    Блокировка снимает гонку «прочитал → подумал → записал» между двумя
    правками панели. Межпроцессную гонку она не закрывает — для неё есть
    сверка версии в `_save_code`, и именно она, а не блокировка, отвечает за
    то, что чужая запись не будет затёрта молча.
    """
    key = str(pdir)
    lock = _LOCKS.get(key)
    if lock is None:
        lock = _LOCKS[key] = asyncio.Lock()
    return lock


def _load_meta(pdir: Path) -> dict | None:
    try:
        raw = json.loads((pdir / "project.json").read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, ValueError):
        return None


def _save_meta(pdir: Path, meta: dict) -> None:
    _write_atomic(pdir / "project.json", json.dumps(meta, ensure_ascii=False, indent=2))


def _current_path(pdir: Path) -> Path:
    return pdir / "current.html"


def _version_path(pdir: Path, version: int) -> Path:
    return pdir / "history" / f"v{int(version)}.html"


def _check_slug(slug: str) -> str:
    """Слаг страницы → он же, но только если это действительно слаг."""
    text = str(slug or "").strip().lower()
    if not _SLUG_RE.match(text):
        raise HTTPException(
            status_code=422,
            detail=("имя страницы может состоять только из латинских букв в нижнем "
                    "регистре, цифр и дефиса и начинаться с буквы или цифры"))
    if text in _RESERVED_SLUGS:
        raise HTTPException(status_code=422,
                            detail=f"имя «{text}» занято служебным каталогом проекта")
    return text


def _contained(root: Path, path: Path) -> Path:
    """Путь обязан лежать ВНУТРИ каталога проекта. Иначе — 422, а не запись.

    Вторая линия обороны после проверки имени: она защищает и от симлинка,
    подставленного внутрь каталога проекта, и от будущего вызова, который
    забудет провалидировать имя.
    """
    resolved_root = root.resolve()
    resolved = (path if path.is_absolute() else resolved_root / path)
    try:
        resolved = resolved.resolve()
    except OSError:                             # битый симлинк — тоже отказ
        raise HTTPException(status_code=422, detail="недопустимый путь внутри проекта")
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise HTTPException(status_code=422,
                            detail="путь выходит за пределы каталога проекта")
    return resolved


def _pages_dir(pdir: Path) -> Path:
    return pdir / "pages"


def _page_path(pdir: Path, slug: str) -> Path:
    """Файл страницы. Домашняя — это `current.html`: код проекта, как и был."""
    slug = _check_slug(slug)
    if slug == HOME_SLUG:
        return _current_path(pdir)
    return _contained(pdir, _pages_dir(pdir) / f"{slug}.html")


def _read_code(pdir: Path, slug: str = HOME_SLUG) -> str:
    try:
        return _page_path(pdir, slug).read_text(encoding="utf-8")
    except OSError:
        return ""


def _pages_of(meta: dict) -> list[dict]:
    """Список страниц проекта; у старых проектов он достраивается на лету."""
    pages = [p for p in (meta.get("pages") or []) if isinstance(p, dict) and p.get("slug")]
    if not any(p.get("slug") == HOME_SLUG for p in pages):
        pages.insert(0, {"slug": HOME_SLUG, "title": meta.get("name") or "Главная",
                         "created_at": meta.get("created_at"),
                         "updated_at": meta.get("updated_at")})
    return pages


def _require_page(meta: dict, slug: str) -> dict:
    slug = _check_slug(slug)
    page = next((p for p in _pages_of(meta) if p.get("slug") == slug), None)
    if page is None:
        raise HTTPException(status_code=404, detail=f"страница «{slug}» не найдена")
    return page


def _next_id(svc) -> int:
    used = {int(d.name) for d in _root(svc).iterdir() if d.is_dir() and d.name.isdigit()}
    return (max(used) + 1) if used else 1


def _now() -> float:
    return round(time.time(), 3)


def _public_meta(meta: dict) -> dict:
    # id — ЧИСЛО. На диске он лежал строкой, а UI сравнивал его с числом через
    # ===, поэтому «последний проект» и ссылка ?project=N не срабатывали
    # никогда: панель молча открывала первый попавшийся проект.
    versions = list(meta.get("versions") or [])
    cursor = int(meta.get("cursor") or meta.get("version") or 0)
    numbers = [int(v.get("version", 0)) for v in versions]
    return {
        "id": int(meta.get("id") or 0), "name": meta.get("name", ""), "prompt": meta.get("prompt", ""),
        "template": meta.get("template", ""), "palette": meta.get("palette", ""),
        "version": int(meta.get("version", 0)),
        "created_at": meta.get("created_at"), "updated_at": meta.get("updated_at"),
        "pages": [str(p.get("slug")) for p in _pages_of(meta)],
        "home": HOME_SLUG,
        "cursor": cursor,
        "can_undo": bool(numbers) and cursor in numbers and numbers.index(cursor) > 0,
        "can_redo": bool(meta.get("redo")),
    }


def _save_code(svc, pdir: Path, html: str, note: str, *,
               expect_version: int | None = None, page: str = HOME_SLUG,
               cursor: int | None = None, keep_redo: bool = False) -> dict:
    """Записать новую текущую версию + снимок в историю. Возвращает версию.

    Единственная точка записи кода проекта, поэтому предел размера проверяется
    здесь: в схемах запросов он стоит не на всех путях — ответ модели и откат
    к версии приходят мимо них.

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
    target = _page_path(pdir, page)
    version = current_version + 1
    history = pdir / "history"
    history.mkdir(parents=True, exist_ok=True)
    # Снимок сначала, текущий файл — вторым: обрыв между ними оставляет лишний
    # снимок, обратный порядок оставил бы версию без снимка.
    _write_atomic(_version_path(pdir, version), html)
    _write_atomic(target, html)
    meta.update({
        "id": pdir.name, "version": version, "updated_at": _now(),
    })
    versions = list(meta.get("versions") or [])
    versions.append({"version": version, "note": _note(note), "ts": meta["updated_at"],
                     "chars": len(html), "page": _check_slug(page)})
    meta["versions"] = versions[-MAX_VERSIONS:]
    # Курсор истории — версия, которую владелец СЕЙЧАС видит. Обычная запись
    # ставит его на новую версию и обнуляет «вперёд»: после новой правки
    # повторять уже нечего. Отмена и повтор передают курсор сами.
    meta["cursor"] = int(cursor) if cursor is not None else version
    if not keep_redo:
        meta["redo"] = []
    _save_meta(pdir, meta)
    # Держим каталог истории в пределах MAX_VERSIONS снимков. Сортировка ЧИСЛОВАЯ:
    # по именам «v10» идёт раньше «v9», поэтому лексикографический срез удалял
    # снимки, которые остаются в списке версий, и откат к ним отвечал 404.
    snapshots = sorted(history.glob("v*.html"), key=_snapshot_number)
    for stale in snapshots[:-MAX_VERSIONS]:
        stale.unlink(missing_ok=True)
    return _public_meta(meta)


def _save_tokens(pdir: Path, tokens: dict) -> None:
    """Токены проекта — отдельный файл, а не поле в project.json.

    Их читает и превью, и экспорт, и они переписываются заметно чаще меты;
    держать их рядом значило бы переписывать меты целиком ради смены одного
    акцента. Запись атомарная по той же причине, что и всё остальное здесь:
    читатель обязан увидеть либо старый набор целиком, либо новый целиком, а
    не половину палитры.
    """
    _write_atomic(pdir / "tokens.json",
                  json.dumps(tokens, ensure_ascii=False, indent=2, sort_keys=True))


def _load_tokens(pdir: Path) -> dict:
    """Токены с диска; их отсутствие — не ошибка, а проект старше токенов."""
    try:
        raw = json.loads((pdir / "tokens.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return gen.default_tokens()
    return raw if isinstance(raw, dict) else gen.default_tokens()


def _revision_page(meta: dict, version: int) -> str:
    """Страница, которой принадлежит снимок этой версии.

    Откат обязан вернуть код в ТУ ЖЕ страницу, из которой снимок был снят.
    Иначе восстановление старой версии «Контактов» легло бы на главную, и
    владелец потерял бы сразу две страницы: ту, которую хотел вернуть, и ту,
    которую не трогал. У проектов, созданных до многостраничности, поля `page`
    в записи версии нет — там единственная страница и есть главная. Неразборчивый
    слаг тоже уводит на главную, а не роняет откат: снимок существует, и
    испорченная запись истории не повод отказать владельцу в его коде.
    """
    for entry in reversed(list(meta.get("versions") or [])):
        if not isinstance(entry, dict):
            continue
        try:
            if int(entry.get("version") or 0) != int(version):
                continue
        except (TypeError, ValueError):
            continue
        try:
            return _check_slug(str(entry.get("page") or HOME_SLUG))
        except HTTPException:
            return HOME_SLUG
    return HOME_SLUG


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
    page: str = HOME_SLUG


class GenerateIn(BaseModel):
    prompt: str = Field(default="", max_length=4000)
    name: str = Field(default="", max_length=120)
    template: str = "auto"
    palette: str = "auto"


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
    page: str = HOME_SLUG


class AiEditIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    bd_id: str | None = None
    path: str | None = None
    base_version: int | None = None
    page: str = HOME_SLUG


# ---------------------------------------------------------------- endpoints

@router.get("/web-designer/projects")
async def list_projects(request: Request):
    svc = request.app.state.svc
    await _ensure_dir_layout(svc)
    items = []
    for pdir in _root(svc).iterdir():
        if not pdir.is_dir() or not pdir.name.isdigit():
            continue
        meta = _load_meta(pdir)
        if meta:
            items.append(_public_meta(meta))
    items.sort(key=lambda m: (m.get("updated_at") or 0), reverse=True)
    return {"items": items[:MAX_PROJECTS]}


@router.post("/web-designer/projects")
async def create_project(body: ProjectIn, request: Request):
    svc = request.app.state.svc
    await _ensure_dir_layout(svc)
    existing = sum(1 for d in _root(svc).iterdir() if d.is_dir() and d.name.isdigit())
    if existing >= MAX_PROJECTS:
        # Раньше предел применялся только к списку: проекты продолжали копиться
        # на диске, а лишние просто не показывались. Отказ честнее молчания.
        raise HTTPException(status_code=409,
                            detail=f"достигнут предел в {MAX_PROJECTS} проектов — удалите ненужные")
    pid = _next_id(svc)
    pdir = _pdir(svc, pid)
    meta = {
        "id": str(pid), "name": " ".join(body.name.split())[:120],
        "prompt": body.prompt[:4000], "template": body.template,
        "palette": body.palette, "version": 0,
        "created_at": _now(), "updated_at": _now(), "versions": [],
        "cursor": 0, "redo": [],
        "pages": [{"slug": HOME_SLUG, "title": " ".join(body.name.split())[:120] or "Главная",
                   "created_at": _now(), "updated_at": _now()}],
    }
    _save_meta(pdir, meta)
    if body.template == "blank":
        blank = ("<!DOCTYPE html>\n<html lang=\"ru\">\n<head>\n<meta charset=\"utf-8\">\n"
                 "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
                 f"<title>{meta['name']}</title>\n</head>\n<body>\n\n</body>\n</html>\n")
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
    # Токены проекта заводятся сразу и согласованно с выбранной палитрой:
    # иначе первое же «поменять акцент» переписывало бы CSS генератора.
    _save_tokens(pdir, gen.tokens_from_palette((meta or {}).get("palette") or "indigo"))
    return {"meta": _public_meta(meta or {}), "code": _read_code(pdir)}


@router.get("/web-designer/templates")
async def templates():
    return {"items": gen.templates_catalog(),
            "palettes": sorted(gen.PALETTES.keys())}


@router.get("/web-designer/projects/{pid}")
async def get_project(pid: int, request: Request, page: str = HOME_SLUG):
    svc = request.app.state.svc
    pdir, meta = _require_project(svc, pid)
    _require_page(meta, page)
    return {"meta": _public_meta(meta), "code": _read_code(pdir, page), "page": page,
            "pages": _pages_of(meta),
            "versions": list(meta.get("versions") or [])[-MAX_VERSIONS:]}


@router.put("/web-designer/projects/{pid}/code")
async def put_code(pid: int, body: CodeIn, request: Request):
    svc = request.app.state.svc
    pdir, meta_now = _require_project(svc, pid)
    _require_page(meta_now, body.page)
    if not _TAG_RE.search(body.html[:2000]):
        raise HTTPException(status_code=422, detail="это не похоже на HTML-документ")
    async with _project_lock(pdir):
        meta = _save_code(svc, pdir, body.html, body.note or "правка кода",
                          expect_version=body.base_version, page=body.page)
    return {"ok": True, "meta": meta, "page": body.page}


@router.post("/web-designer/projects/{pid}/generate")
async def generate_site(pid: int, body: GenerateIn, request: Request):
    """Собрать сайт по описанию. Хранится только финал; steps — для анимации в UI."""
    svc = request.app.state.svc
    pdir, meta = _require_project(svc, pid)
    result = gen.generate(body.prompt or meta.get("prompt", ""), name=body.name or meta.get("name", ""),
                          template=body.template, palette=body.palette)
    async with _project_lock(pdir):
        meta = _save_code(svc, pdir, result["steps"][-1],
                          f"генерация: {result['template']}/{result['palette']}")
    await _sync_meta_fields(pdir, meta, result)
    return {"ok": True, "meta": meta, "template": result["template"],
            "palette": result["palette"], "steps": result["steps"]}


async def _sync_meta_fields(pdir: Path, meta: dict, result: dict) -> None:
    stored = _load_meta(pdir) or {}
    stored.update({"template": result["template"], "palette": result["palette"]})
    _save_meta(pdir, stored)
    meta.update({"template": result["template"], "palette": result["palette"]})


@router.post("/web-designer/projects/{pid}/edit")
async def edit_project(pid: int, body: EditIn, request: Request):
    """Точечная правка выбранного элемента текущего кода."""
    svc = request.app.state.svc
    pdir, meta_now = _require_project(svc, pid)
    _require_page(meta_now, body.page)
    async with _project_lock(pdir):
        html = _read_code(pdir, body.page)
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
        meta = _save_code(svc, pdir, new_html, note, expect_version=body.base_version,
                          page=body.page)
    return {"ok": True, "meta": meta, "element": described, "page": body.page}


@router.get("/web-designer/projects/{pid}/preview", response_class=HTMLResponse)
async def preview(pid: int, request: Request, page: str = HOME_SLUG):
    """HTML для iframe: с data-bd-id и пикером. Хранимый код не меняется."""
    svc = request.app.state.svc
    pdir, meta = _require_project(svc, pid)
    _require_page(meta, page)
    html = _read_code(pdir, page)
    if not html:
        raise HTTPException(status_code=409, detail="в проекте пока нет кода")
    try:
        return HTMLResponse(dom.inject_preview(html), headers=PREVIEW_HEADERS)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/web-designer/projects/{pid}/ai-edit")
async def ai_edit(pid: int, body: AiEditIn, request: Request):
    """Правка кода моделью из реестра. Модели нет — честный отказ."""
    svc = request.app.state.svc
    pdir, meta_now = _require_project(svc, pid)
    _require_page(meta_now, body.page)
    html = _read_code(pdir, body.page)
    if not html:
        raise HTTPException(status_code=409, detail="в проекте пока нет кода")
    # Версия, на которой строится правка. Ответ модели приходит через секунды,
    # и раньше он записывался поверх всего, что владелец успел сделать за это
    # время: чтение до await, запись после, без сверки. Теперь база правки
    # зафиксирована и проверяется при записи.
    base_version = int((_load_meta(pdir) or {}).get("version", 0))
    if body.base_version is not None and int(body.base_version) != base_version:
        raise HTTPException(
            status_code=409,
            detail=(f"код изменился (версия {base_version}) — обновите превью и повторите"))
    models = await svc.registry.list_models()
    if not models:
        raise HTTPException(
            status_code=409,
            detail="нет настроенной модели — добавьте модель в реестре, тогда AI-правка станет доступна")
    model = models[0]
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
        user = f"Документ:\n{html[:120000]}\n\nЗапрос: {body.prompt}"

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
            dom.op_replace(found, new_html)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"модель вернула негодный фрагмент: {exc}")
        dom._strip_bd_ids(root)
        new_html = dom.serialize(root)
    async with _project_lock(pdir):
        meta = _save_code(svc, pdir, new_html, f"AI: {_note(body.prompt)}",
                          expect_version=base_version, page=body.page)
    return {"ok": True, "meta": meta, "model": model_row.get("alias") or model_row.get("name")}


def _extract_html(text: str, fragment: bool) -> str:
    """Достать HTML из ответа модели: срезать ```-ограждения и болтовню вокруг."""
    raw = str(text or "").strip()
    fence = re.search(r"```(?:html)?\s*(.+?)```", raw, re.S)
    if fence:
        raw = fence.group(1).strip()
    if fragment:
        return raw
    match = re.search(r"<!DOCTYPE.*?</html>", raw, re.S | re.I)
    if match:
        return match.group(0)
    match = re.search(r"<html.*?</html>", raw, re.S | re.I)
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
    page = _revision_page(_load_meta(pdir) or {}, version)
    async with _project_lock(pdir):
        meta = _save_code(svc, pdir, html, f"откат к версии {version}", page=page)
    return {"ok": True, "meta": meta, "code": html, "page": page}


@router.delete("/web-designer/projects/{pid}")
async def delete_project(pid: int, request: Request):
    svc = request.app.state.svc
    pdir, _ = _require_project(svc, pid)
    shutil.rmtree(pdir, ignore_errors=True)
    return {"ok": True}
