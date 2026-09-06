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


def _write_atomic(path: Path, text: str | bytes) -> None:
    """Запись через временный файл в ТОМ ЖЕ каталоге и os.replace.

    Прямая запись `current.html` рвёт файл на части при падении процесса или
    при чтении превью в тот же момент: владелец получал полупустой сайт вместо
    своего. os.replace на одной файловой системе атомарен, поэтому читатель
    видит либо старую версию целиком, либо новую целиком.

    Байты принимаются тем же путём, что и текст: ассет — такой же файл проекта,
    и половина картинки на диске ничем не лучше половины документа. Отдельной
    «почти такой же» записи для двоичных файлов не заводится намеренно —
    точка записи в модуле одна.
    """
    binary = isinstance(text, (bytes, bytearray))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb" if binary else "w",
                       **({} if binary else {"encoding": "utf-8"})) as handle:
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


# ================================================================
# Epoch 4: страницы, компоненты, токены, ассеты, отзывчивость, линт, экспорт.
#
# Общее правило раздела — то же, что и у точечной правки: документ проходит
# через `parse → serialize`, поэтому узел, которого правка не касалась,
# отдаётся ДОСЛОВНЫМ исходным тегом. Оформление (токены и брейкпоинты)
# применяется одним управляемым блоком `<style>`, а не переписыванием
# элементов: смена акцента обязана трогать один узел документа, а не тысячу.
# Любая запись кода идёт через единственную точку `_save_code` — то есть под
# блокировкой проекта, со сверкой версии и через `_write_atomic`.
# ================================================================

# --- побочные документы проекта -------------------------------------------
# Компоненты, ассеты и правила отзывчивости лежат ОТДЕЛЬНЫМИ файлами рядом с
# `project.json` по той же причине, что и токены: они читаются превью и
# экспортом и переписываются чаще меты, а мета — это история версий, которую
# нельзя трогать ради вставки одной картинки.

def _side_load(path: Path, default: dict) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(default)
    return raw if isinstance(raw, dict) else dict(default)


def _side_save(path: Path, data: dict) -> None:
    _write_atomic(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def _components_path(pdir: Path) -> Path:
    return pdir / "components.json"


def _load_components(pdir: Path) -> dict:
    """Определения компонентов: {имя: {html, version, title, updated_at}}."""
    data = _side_load(_components_path(pdir), {"items": {}})
    items = data.get("items")
    return items if isinstance(items, dict) else {}


def _save_components(pdir: Path, items: dict) -> None:
    _side_save(_components_path(pdir), {"items": items})


def _responsive_path(pdir: Path) -> Path:
    return pdir / "responsive.json"


def _load_responsive(pdir: Path) -> dict:
    """Правила брейкпоинтов проекта: {"rules": [...], "seq": N}."""
    data = _side_load(_responsive_path(pdir), {"rules": [], "seq": 0})
    rules = [r for r in (data.get("rules") or []) if isinstance(r, dict)]
    try:
        seq = int(data.get("seq") or 0)
    except (TypeError, ValueError):
        seq = 0
    return {"rules": rules, "seq": max(seq, len(rules))}


def _save_responsive(pdir: Path, state: dict) -> None:
    _side_save(_responsive_path(pdir), {"rules": list(state.get("rules") or []),
                                        "seq": int(state.get("seq") or 0)})


def _assets_dir(pdir: Path) -> Path:
    return pdir / "assets"


def _assets_path(pdir: Path) -> Path:
    return pdir / "assets.json"


def _load_assets(pdir: Path) -> list[dict]:
    data = _side_load(_assets_path(pdir), {"items": []})
    return [a for a in (data.get("items") or []) if isinstance(a, dict) and a.get("id")]


def _save_assets(pdir: Path, items: list[dict]) -> None:
    _side_save(_assets_path(pdir), {"items": items})


def _asset_url(pid: int | str, asset_id: str) -> str:
    """Служебный адрес ассета внутри панели. В экспорте он станет относительным."""
    return f"/api/web-designer/projects/{int(pid)}/assets/{asset_id}"


def _asset_member(asset: dict) -> str:
    """Имя файла ассета — в каталоге проекта и в архиве экспорта одно и то же."""
    return f"{asset['id']}.{asset.get('ext') or 'bin'}"


def _asset_file(pdir: Path, asset: dict) -> Path:
    return _contained(pdir, _assets_dir(pdir) / _asset_member(asset))


# --- проверки ввода --------------------------------------------------------

_TITLE_MAX = 120
# Селектор уезжает В ТЕЛО медиазапроса, где нет ни экранирования, ни разбора:
# `}` закрывает блок, `<` открывает чужой элемент, `;` и комментарии рвут
# правило. Это та же дыра, что закрыта для значений токенов в DOM-слое.
_UNSAFE_IN_CSS = re.compile(r"[<>{}\;]|/\*|\*/|</|@")
_SELECTOR_RE = re.compile(r"^[A-Za-z0-9 _.,:#\[\]='\"~>+()-]{1,120}$")
_CSS_PROP_RE = re.compile(r"^-{0,2}[A-Za-z_][-A-Za-z0-9_]*$")


def _clean_title(value: str, fallback: str = "") -> str:
    return " ".join(str(value or "").split())[:_TITLE_MAX] or fallback


def _check_component_name(name: str) -> str:
    """Имя компонента — идентификатор, а не текст: оно уезжает В АТРИБУТ."""
    text = str(name or "").strip().lower()
    if not dom.COMPONENT_NAME_RE.match(text):
        raise HTTPException(
            status_code=422,
            detail=("имя компонента может состоять только из латинских букв в нижнем "
                    "регистре, цифр, точки, дефиса и подчёркивания"))
    return text


def _check_asset_id(asset_id: str) -> str:
    text = str(asset_id or "").strip().lower()
    if not _ASSET_ID_RE.match(text):
        raise HTTPException(status_code=422, detail="недопустимый идентификатор ассета")
    return text


def _check_rule_id(rule_id: str) -> str:
    text = str(rule_id or "").strip()
    if not _RULE_ID_RE.match(text):
        raise HTTPException(status_code=422, detail="недопустимый идентификатор правила")
    return text


def _check_css_value(value: str, what: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail=f"{what}: пустое значение")
    if len(text) > 200 or _UNSAFE_IN_CSS.search(text):
        raise HTTPException(
            status_code=422,
            detail=(f"{what}: значение «{text[:40]}» вышло бы за пределы своего правила — "
                    "фигурные и угловые скобки, точка с запятой и комментарии запрещены"))
    return text


def _check_css_props(props: dict | None) -> dict[str, str]:
    items = props or {}
    if not isinstance(items, dict) or not items:
        raise HTTPException(status_code=422, detail="нужны props: {свойство: значение}")
    if len(items) > 40:
        raise HTTPException(status_code=422, detail="слишком много свойств в одном правиле")
    out: dict[str, str] = {}
    for prop, value in items.items():
        name = str(prop).strip().lower()
        if not _CSS_PROP_RE.match(name):
            raise HTTPException(status_code=422, detail=f"недопустимое имя css-свойства: {prop!r}")
        out[name] = _check_css_value(value, f"свойство {name}")
    return out


def _check_selector(selector: str) -> str:
    text = " ".join(str(selector or "").split())
    if not text or not _SELECTOR_RE.match(text) or _UNSAFE_IN_CSS.search(text):
        raise HTTPException(
            status_code=422,
            detail=("селектор правила выглядит не как селектор: допустимы имена тегов, "
                    "классы, идентификаторы, атрибуты и комбинаторы"))
    return text


def _check_breakpoint(name: str) -> str:
    text = str(name or "").strip().lower()
    if text not in gen.BREAKPOINTS:
        raise HTTPException(
            status_code=422,
            detail=f"неизвестный брейкпоинт «{text}»: доступны {', '.join(sorted(gen.BREAKPOINTS))}")
    return text


def _check_tokens(tokens: dict | None) -> dict[str, dict[str, str]]:
    """Набор токенов → он же, но только если каждое имя и значение безопасны.

    Значение токена попадает ВНУТРЬ `<style>`, где нет экранирования: `}`
    закрывает блок `:root`, а `</style>` — сам элемент. Имя группы и имя
    токена становятся частью имени CSS-переменной, то есть тоже разметкой.
    """
    if not isinstance(tokens, dict) or not tokens:
        raise HTTPException(status_code=422, detail="нужен непустой набор токенов")
    out: dict[str, dict[str, str]] = {}
    total = 0
    for group, values in tokens.items():
        name = str(group).strip().lower()
        if not dom.TOKEN_GROUP_RE.match(name):
            raise HTTPException(status_code=422, detail=f"недопустимая группа токенов: {group!r}")
        if not isinstance(values, dict) or not values:
            raise HTTPException(status_code=422, detail=f"группа «{name}» пуста")
        bucket: dict[str, str] = {}
        for token, value in values.items():
            key = str(token).strip().lower()
            if not dom.TOKEN_NAME_RE.match(key):
                raise HTTPException(status_code=422, detail=f"недопустимое имя токена: {token!r}")
            bucket[key] = _check_css_value(value, f"токен {name}.{key}")
            total += 1
            if total > 400:
                raise HTTPException(status_code=422, detail="слишком много токенов в наборе")
        out[name] = bucket
    return out


def _require_version(pdir: Path, expect: int | None) -> int:
    """Сверка версии перед записью: чужая работа не затирается молча.

    Вызывается ВНУТРИ блокировки проекта — вместе с `_save_code` это и есть
    compare-and-set: прочитали версию, убедились, что она та же, записали.
    """
    current = int((_load_meta(pdir) or {}).get("version", 0))
    if expect is not None and int(expect) != current:
        raise HTTPException(
            status_code=409,
            detail=(f"код изменился за время правки: вы правили версию {int(expect)}, "
                    f"сейчас сохранена {current}. Обновите превью и повторите — "
                    "чужая правка не затёрта"))
    return current


# --- применение оформления к документу -------------------------------------

def _with_project_styles(html: str, tokens: dict, rules: list[dict]) -> str:
    """Токены и брейкпоинты → два управляемых блока `<style>` в `<head>`.

    Именно здесь живёт обещание «смена акцента не переписывает элементы»:
    меняется ОДИН узел документа — `<style id="bd-tokens">`, — а всё остальное
    отдаётся дословным исходным текстом. Блок брейкпоинтов идёт после блока
    токенов: медиазапросы обязаны перебивать базовые значения.
    """
    out = dom.set_managed_style(html, dom.TOKENS_STYLE_ID, gen.render_tokens_css(tokens))
    return dom.set_managed_style(out, dom.RESPONSIVE_STYLE_ID, gen.render_responsive_css(rules))


def _find_nav(root) -> object | None:
    """Управляемая панелью навигация — по служебному атрибуту, а не по классу.

    `find_by_attr` из DOM-слоя здесь не годится: `data-bd-nav` пишется без
    значения, а сравнение по пустой строке совпало бы с ЛЮБЫМ элементом без
    этого атрибута — навигация подменяла бы первый попавшийся тег.
    """
    for element in dom.walk_elements(root):
        if dom.NAV_ATTR in element.attrs:
            return element
    return None


def _with_nav(html: str, pages: list[dict], current: str) -> str:
    """Вставить (или обновить) навигацию по страницам сайта.

    Ссылки ведут на соседние ФАЙЛЫ, поэтому экспортированный сайт листается
    без панели и без сервера — просто открытые файлы в браузере. Проект из
    одной страницы навигации не получает: ссылка на саму себя — это шум.
    """
    if len(pages) < 2:
        return html
    root = dom.parse_document(html)
    fresh = dom.parse_fragment(gen.render_nav_html(pages, current))
    existing = _find_nav(root)
    if existing is not None:
        parent = existing.parent
        if parent is not None:
            index = parent.children.index(existing)
            parent.children[index:index + 1] = fresh
            for node in fresh:
                node.parent = parent
            existing.parent = None
            return dom.serialize(root)
    host = next((n for n in dom.walk_elements(root) if n.tag == "body"), None)
    if host is None:
        return html
    dom.insert_nodes(host, fresh, "prepend")
    return dom.serialize(root)


def _restyle_pages(svc, pdir: Path, note: str) -> dict:
    """Переприменить токены и брейкпоинты ко ВСЕМ страницам проекта.

    Зовётся под блокировкой проекта. Страница, у которой ничего не поменялось,
    НЕ переписывается: пустая версия в истории — это шум, из-за которого
    история перестаёт что-либо значить.
    """
    tokens = _load_tokens(pdir)
    rules = _load_responsive(pdir)["rules"]
    meta = _load_meta(pdir) or {}
    touched: list[str] = []
    for page in _pages_of(meta):
        slug = str(page.get("slug") or "")
        html = _read_code(pdir, slug)
        if not html:
            continue
        updated = _with_project_styles(html, tokens, rules)
        if updated != html:
            _save_code(svc, pdir, updated, note, page=slug)
            touched.append(slug)
    return {"pages": touched, "meta": _public_meta(_load_meta(pdir) or {})}


# --- ассеты ----------------------------------------------------------------

def _decode_asset(data: str) -> bytes:
    try:
        raw = base64.b64decode(str(data or ""), validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422,
                            detail="содержимое файла должно быть строкой base64")
    if not raw:
        raise HTTPException(status_code=422, detail="пустой файл")
    if len(raw) > MAX_ASSET_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"файл больше предела в {MAX_ASSET_BYTES // (1024 * 1024)} МБ")
    return raw


def _asset_record(raw: bytes, name: str) -> dict:
    """Байты → запись ассета. Тип берётся ИЗ БАЙТОВ, имя — только подпись.

    Имя, пришедшее из браузера, — утверждение загрузившего: `evil.html`,
    названный `logo.png`, отдавался бы как картинка ровно до первого браузера,
    решившего иначе. Поэтому и расширение файла на диске, и Content-Type
    берутся из сигнатуры, а имя владельца остаётся подписью в списке.
    """
    sniffed = sniff_asset(raw)
    if sniffed is None:
        raise HTTPException(
            status_code=422,
            detail=("это не картинка и не шрифт: распознаются PNG, JPEG, GIF, BMP, WebP "
                    "и шрифты WOFF/WOFF2/TTF/OTF. SVG не принимается — это исполняемый "
                    "документ, а в экспортированном сайте он уже вне песочницы"))
    content_type, ext, kind = sniffed
    return {
        "id": hashlib.sha256(raw).hexdigest()[:24],
        "name": _clean_title(name, "файл"),
        "content_type": content_type, "ext": ext, "kind": kind,
        "bytes": len(raw), "created_at": _now(),
    }


def _asset_mapping(pid: int | str, assets: list[dict]) -> dict[str, str]:
    """Служебный URL → путь в экспорте. Ключ длиннее — заменяется раньше."""
    return {_asset_url(pid, a["id"]): f"assets/{_asset_member(a)}" for a in assets}


# --- экспорт ---------------------------------------------------------------

def _export_members(svc, pid: int, pdir: Path, meta: dict) -> list[tuple[str, bytes]]:
    """Самодостаточный сайт: список (имя файла, содержимое) и ничего лишнего.

    Каждое имя проверяется `is_safe_archive_name` ПЕРЕД тем, как попасть в
    архив или в каталог: абсолютный путь или `..` в имени члена — классический
    zip-slip, при распаковке файл ложится ВНЕ каталога назначения.
    """
    tokens = _load_tokens(pdir)
    rules = _load_responsive(pdir)["rules"]
    assets = _load_assets(pdir)
    mapping = _asset_mapping(pid, assets)
    pages = _pages_of(meta)
    members: list[tuple[str, bytes]] = []
    for page in pages:
        slug = str(page.get("slug") or "")
        html = _read_code(pdir, slug)
        if not html:
            continue
        html = _with_project_styles(html, tokens, rules)
        html = _with_nav(html, pages, slug)
        html = gen.rewrite_asset_urls(html, mapping)
        members.append((gen.page_file_name(slug), html.encode("utf-8")))
    for asset in assets:
        path = _asset_file(pdir, asset)
        try:
            members.append((f"assets/{_asset_member(asset)}", path.read_bytes()))
        except OSError:
            continue                      # файла нет — запись осиротела, не повод падать
    for name, _ in members:
        if not gen.is_safe_archive_name(name):
            raise HTTPException(status_code=422,
                                detail=f"имя файла «{name}» не годится для экспорта")
    return members


def _export_archive(members: list[tuple[str, bytes]]) -> bytes:
    """Архив собирается в памяти и только из проверенных имён."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members:
            if not gen.is_safe_archive_name(name):
                continue
            archive.writestr(name, payload)
    return buffer.getvalue()


# ---------------------------------------------------------------- модели запросов Epoch 4

class PageIn(BaseModel):
    slug: str = Field(min_length=1, max_length=48)
    title: str = Field(default="", max_length=_TITLE_MAX)
    base_version: int | None = None


class PagePatchIn(BaseModel):
    slug: str | None = Field(default=None, max_length=48)
    title: str | None = Field(default=None, max_length=_TITLE_MAX)
    base_version: int | None = None


class ComponentIn(BaseModel):
    name: str = Field(min_length=1, max_length=48)
    title: str = Field(default="", max_length=_TITLE_MAX)
    page: str = HOME_SLUG
    bd_id: str | None = None
    path: str | None = None
    tag: str | None = Field(default=None, max_length=40)
    html: str | None = Field(default=None, max_length=MAX_HTML_CHARS)


class ComponentUpdateIn(BaseModel):
    html: str = Field(min_length=1, max_length=MAX_HTML_CHARS)
    title: str | None = Field(default=None, max_length=_TITLE_MAX)
    base_version: int | None = None


class ComponentInsertIn(BaseModel):
    page: str = HOME_SLUG
    bd_id: str | None = None
    path: str | None = None
    tag: str | None = Field(default=None, max_length=40)
    position: str = "append"                   # append | prepend | before | after
    base_version: int | None = None


class TokensIn(BaseModel):
    tokens: dict[str, dict[str, str]]
    # По умолчанию присланное СЛИВАЕТСЯ с текущим набором: панель шлёт один
    # изменённый акцент, а не всю палитру, и не должна случайно стереть шрифты.
    replace: bool = False
    base_version: int | None = None


class AssetIn(BaseModel):
    name: str = Field(default="", max_length=_TITLE_MAX)
    # base64 раздувает байты на треть; предел с запасом, точная проверка — по
    # РАСПАКОВАННОМУ размеру в `_decode_asset`.
    data: str = Field(min_length=4, max_length=(MAX_ASSET_BYTES * 4) // 3 + 1024)


class RuleIn(BaseModel):
    breakpoint: str = Field(min_length=1, max_length=8)
    selector: str = Field(min_length=1, max_length=120)
    props: dict[str, str]


# ---------------------------------------------------------------- страницы

@router.get("/web-designer/projects/{pid}/pages")
async def list_pages(pid: int, request: Request):
    svc = request.app.state.svc
    _, meta = _require_project(svc, pid)
    return {"items": _pages_of(meta), "home": HOME_SLUG, "limit": MAX_PAGES}


@router.post("/web-designer/projects/{pid}/pages")
async def create_page(pid: int, body: PageIn, request: Request):
    """Новая страница проекта: файл на диске + запись в мете + снимок версии."""
    svc = request.app.state.svc
    pdir, _ = _require_project(svc, pid)
    slug = _check_slug(body.slug)
    async with _project_lock(pdir):
        _require_version(pdir, body.base_version)
        meta = _load_meta(pdir) or {}
        pages = _pages_of(meta)
        if any(str(p.get("slug")) == slug for p in pages):
            raise HTTPException(status_code=409, detail=f"страница «{slug}» уже есть")
        if len(pages) >= MAX_PAGES:
            raise HTTPException(status_code=409,
                                detail=f"достигнут предел в {MAX_PAGES} страниц — удалите ненужные")
        title = _clean_title(body.title, slug)
        entry = {"slug": slug, "title": title, "created_at": _now(), "updated_at": _now()}
        meta["pages"] = pages + [entry]
        _save_meta(pdir, meta)
        html = _with_project_styles(gen.blank_page_html(title), _load_tokens(pdir),
                                    _load_responsive(pdir)["rules"])
        result = _save_code(svc, pdir, html, f"новая страница «{slug}»", page=slug)
    return {"ok": True, "page": entry, "meta": result,
            "pages": _pages_of(_load_meta(pdir) or {})}


@router.post("/web-designer/projects/{pid}/pages/{slug}/rename")
async def rename_page(pid: int, slug: str, body: PagePatchIn, request: Request):
    """Переименование страницы: и слаг (имя файла), и человеческий заголовок.

    Домашняя страница слаг не меняет: это точка входа сайта (`index.html`) и
    цель всех ссылок навигации — переименовав её, владелец получил бы сайт,
    который открывается пустым каталогом.
    """
    svc = request.app.state.svc
    pdir, _ = _require_project(svc, pid)
    async with _project_lock(pdir):
        _require_version(pdir, body.base_version)
        meta = _load_meta(pdir) or {}
        page = dict(_require_page(meta, slug))
        source = str(page.get("slug"))
        target = _check_slug(body.slug) if body.slug else source
        if target != source:
            if source == HOME_SLUG:
                raise HTTPException(
                    status_code=409,
                    detail="домашнюю страницу нельзя переименовать: это точка входа сайта")
            if any(str(p.get("slug")) == target for p in _pages_of(meta)):
                raise HTTPException(status_code=409, detail=f"страница «{target}» уже есть")
        page["slug"] = target
        page["title"] = _clean_title(body.title, page.get("title") or target) \
            if body.title is not None else (page.get("title") or target)
        page["updated_at"] = _now()
        meta["pages"] = [page if str(p.get("slug")) == source else p for p in _pages_of(meta)]
        _save_meta(pdir, meta)
        result = _public_meta(_load_meta(pdir) or {})
        if target != source:
            html = _read_code(pdir, source)
            if html:
                result = _save_code(svc, pdir, html,
                                    f"переименование «{source}» → «{target}»", page=target)
            _page_path(pdir, source).unlink(missing_ok=True)
    return {"ok": True, "page": page, "meta": result,
            "pages": _pages_of(_load_meta(pdir) or {})}


@router.post("/web-designer/projects/{pid}/pages/{slug}/duplicate")
async def duplicate_page(pid: int, slug: str, body: PageIn, request: Request):
    """Копия страницы под новым слагом. Копируется код, а не ссылка на него."""
    svc = request.app.state.svc
    pdir, _ = _require_project(svc, pid)
    target = _check_slug(body.slug)
    async with _project_lock(pdir):
        _require_version(pdir, body.base_version)
        meta = _load_meta(pdir) or {}
        source_page = _require_page(meta, slug)
        pages = _pages_of(meta)
        if any(str(p.get("slug")) == target for p in pages):
            raise HTTPException(status_code=409, detail=f"страница «{target}» уже есть")
        if len(pages) >= MAX_PAGES:
            raise HTTPException(status_code=409,
                                detail=f"достигнут предел в {MAX_PAGES} страниц — удалите ненужные")
        html = _read_code(pdir, str(source_page.get("slug")))
        if not html:
            raise HTTPException(status_code=409, detail="у исходной страницы пока нет кода")
        title = _clean_title(body.title, f"{source_page.get('title') or slug} (копия)")
        entry = {"slug": target, "title": title, "created_at": _now(), "updated_at": _now()}
        meta["pages"] = pages + [entry]
        _save_meta(pdir, meta)
        result = _save_code(svc, pdir, html,
                            f"копия страницы «{source_page.get('slug')}» → «{target}»",
                            page=target)
    return {"ok": True, "page": entry, "meta": result,
            "pages": _pages_of(_load_meta(pdir) or {})}


@router.delete("/web-designer/projects/{pid}/pages/{slug}")
async def delete_page(pid: int, slug: str, request: Request):
    """Удаление страницы. Домашнюю удалить нельзя: сайт без входа — не сайт."""
    svc = request.app.state.svc
    pdir, _ = _require_project(svc, pid)
    async with _project_lock(pdir):
        meta = _load_meta(pdir) or {}
        page = _require_page(meta, slug)
        target = str(page.get("slug"))
        if target == HOME_SLUG:
            raise HTTPException(
                status_code=409,
                detail="домашнюю страницу удалить нельзя: это точка входа сайта")
        meta["pages"] = [p for p in _pages_of(meta) if str(p.get("slug")) != target]
        meta["updated_at"] = _now()
        _save_meta(pdir, meta)
        # Снимки истории остаются: удалена страница, а не право владельца
        # вернуться к её коду.
        _page_path(pdir, target).unlink(missing_ok=True)
    return {"ok": True, "removed": target, "pages": _pages_of(_load_meta(pdir) or {})}


# ---------------------------------------------------------------- компоненты
#
# ЧТО ЭКЗЕМПЛЯР ГАРАНТИРУЕТ, А ЧТО — НЕТ.
#
# Экземпляр — это обычная разметка в странице, помеченная двумя служебными
# атрибутами: `data-bd-component` (имя определения) и `data-bd-cver` (версия
# определения, из которой он собран). Никакой ссылки, никакой подстановки во
# время показа: экспортированный сайт — это файлы, а не движок шаблонов.
#
# Гарантируется:
#   * обновление определения ПЕРЕСОБИРАЕТ все экземпляры на всех страницах
#     проекта из новой разметки — предсказуемо и целиком;
#   * содержимое элементов со `data-bd-slot` переносится в пересобранный
#     экземпляр, иначе «обновить компонент» означало бы «стереть текст на всех
#     страницах»;
#   * `id` экземпляра сохраняется: на него ссылаются якоря и стили;
#   * каждая изменённая страница пишется через `_save_code`, то есть попадает
#     в историю версий и может быть откачена по отдельности.
#
# Не гарантируется (и это осознанный выбор в пользу предсказуемости):
#   * правки ВНУТРИ экземпляра вне слотов не переживают обновления определения
#     — они перезаписываются разметкой определения; правьте слоты;
#   * экземпляры в СНИМКАХ истории не переписываются: снимок — это то, что
#     было, а не то, что стало;
#   * удаление определения НЕ трогает уже вставленные экземпляры: они
#     остаются обычной разметкой страницы и просто перестают обновляться.
#     Удалять чужую вёрстку по кнопке «удалить определение» — сюрприз, а не
#     функция.

def _require_component(pdir: Path, name: str) -> tuple[str, dict]:
    name = _check_component_name(name)
    record = _load_components(pdir).get(name)
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail=f"компонент «{name}» не найден")
    return name, record


def _component_public(name: str, record: dict, instances: int | None = None) -> dict:
    item = {
        "name": name, "title": record.get("title") or name,
        "html": record.get("html") or "", "version": int(record.get("version") or 1),
        "created_at": record.get("created_at"), "updated_at": record.get("updated_at"),
    }
    if instances is not None:
        item["instances"] = instances
    return item


def _count_instances(pdir: Path, meta: dict, name: str) -> int:
    total = 0
    for page in _pages_of(meta):
        html = _read_code(pdir, str(page.get("slug") or ""))
        if not html:
            continue
        total += len(dom.component_instances(dom.parse_document(html), name))
    return total


def _resolve_in_page(html: str, bd_id: str | None, path: str | None, tag: str | None):
    """Найти элемент страницы по выделению превью и сверить его тег.

    Тег, echo которого прислал клиент, — это то, ЧЕМ элемент был в превью.
    Нумерация bd-N привязана к версии документа: если код с тех пор изменился,
    тот же номер указывает уже на другой элемент, и вставка ушла бы в чужой
    тег. Несовпадение — 409 «выделение устарело», а не молчаливая правка.
    """
    root = dom.parse_document(html)
    dom.assign_bd_ids(root)
    element = dom.resolve_element(root, bd_id, path)
    if element is None:
        raise HTTPException(status_code=404,
                            detail="элемент не найден — обновите превью и выберите заново")
    expect = str(tag or "").strip().lower()
    if expect and element.tag != expect:
        raise HTTPException(
            status_code=409,
            detail=(f"выделение устарело: номер указывает на <{element.tag}>, а выбран был "
                    f"<{expect}> — обновите превью и выберите заново"))
    return root, element


def _sync_component_pages(svc, pdir: Path, name: str, definition: str,
                          version: int, note: str) -> dict[str, int]:
    """Пересобрать экземпляры компонента на всех страницах. Под блокировкой."""
    meta = _load_meta(pdir) or {}
    touched: dict[str, int] = {}
    for page in _pages_of(meta):
        slug = str(page.get("slug") or "")
        html = _read_code(pdir, slug)
        if not html:
            continue
        try:
            updated, count = dom.sync_component(html, name, definition, version)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        if count and updated != html:
            _save_code(svc, pdir, updated, note, page=slug)
            touched[slug] = count
    return touched


@router.get("/web-designer/projects/{pid}/components")
async def list_components(pid: int, request: Request):
    svc = request.app.state.svc
    pdir, meta = _require_project(svc, pid)
    items = _load_components(pdir)
    return {"items": [_component_public(name, items[name], _count_instances(pdir, meta, name))
                      for name in sorted(items)],
            "limit": MAX_COMPONENTS}


@router.post("/web-designer/projects/{pid}/components")
async def create_component(pid: int, body: ComponentIn, request: Request):
    """Определить компонент из выбранного элемента (или из присланной разметки).

    Определение хранится ДОСЛОВНО: разметку владельца никто не переписывает,
    из неё берётся ровно один корневой элемент.
    """
    svc = request.app.state.svc
    pdir, meta = _require_project(svc, pid)
    name = _check_component_name(body.name)
    async with _project_lock(pdir):
        items = _load_components(pdir)
        if name in items:
            raise HTTPException(status_code=409,
                                detail=f"компонент «{name}» уже есть — обновите его определение")
        if len(items) >= MAX_COMPONENTS:
            raise HTTPException(status_code=409,
                                detail=f"достигнут предел в {MAX_COMPONENTS} компонентов")
        definition = body.html
        if definition is None:
            page = _require_page(_load_meta(pdir) or {}, body.page)
            html = _read_code(pdir, str(page.get("slug")))
            if not html:
                raise HTTPException(status_code=409, detail="на странице пока нет кода")
            _, element = _resolve_in_page(html, body.bd_id, body.path, body.tag)
            definition = dom.outer_html(element)
        try:                                  # ровно один корень и годное имя
            dom.render_component(definition, name, 1)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        record = {"title": _clean_title(body.title, name), "html": definition,
                  "version": 1, "created_at": _now(), "updated_at": _now()}
        items[name] = record
        _save_components(pdir, items)
    return {"ok": True, "component": _component_public(name, record, 0)}


@router.put("/web-designer/projects/{pid}/components/{name}")
async def update_component(pid: int, name: str, body: ComponentUpdateIn, request: Request):
    """Новое определение + пересборка всех экземпляров на всех страницах."""
    svc = request.app.state.svc
    pdir, _ = _require_project(svc, pid)
    async with _project_lock(pdir):
        _require_version(pdir, body.base_version)
        key, record = _require_component(pdir, name)
        try:
            dom.render_component(body.html, key, 1)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        version = int(record.get("version") or 1) + 1
        items = _load_components(pdir)
        items[key] = {**record, "html": body.html, "version": version,
                      "title": _clean_title(body.title, record.get("title") or key)
                      if body.title is not None else (record.get("title") or key),
                      "updated_at": _now()}
        _save_components(pdir, items)
        touched = _sync_component_pages(svc, pdir, key, body.html, version,
                                        f"компонент «{key}» → версия {version}")
        meta = _public_meta(_load_meta(pdir) or {})
    return {"ok": True, "component": _component_public(key, items[key], sum(touched.values())),
            "pages": touched, "meta": meta}


@router.post("/web-designer/projects/{pid}/components/{name}/insert")
async def insert_component(pid: int, name: str, body: ComponentInsertIn, request: Request):
    """Вставить экземпляр компонента рядом с выбранным элементом страницы."""
    svc = request.app.state.svc
    pdir, _ = _require_project(svc, pid)
    async with _project_lock(pdir):
        _require_version(pdir, body.base_version)
        key, record = _require_component(pdir, name)
        page = _require_page(_load_meta(pdir) or {}, body.page)
        slug = str(page.get("slug"))
        html = _read_code(pdir, slug)
        if not html:
            raise HTTPException(status_code=409, detail="на странице пока нет кода")
        root, target = _resolve_in_page(html, body.bd_id, body.path, body.tag)
        try:
            node = dom.render_component(record.get("html") or "", key,
                                        int(record.get("version") or 1))
            dom.insert_nodes(target, [node], body.position)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        dom._strip_bd_ids(root)
        meta = _save_code(svc, pdir, dom.serialize(root),
                          f"вставлен компонент «{key}»", page=slug)
    return {"ok": True, "meta": meta, "page": slug,
            "component": _component_public(key, record)}


@router.delete("/web-designer/projects/{pid}/components/{name}")
async def delete_component(pid: int, name: str, request: Request):
    """Убрать определение. Уже вставленные экземпляры остаются вёрсткой страниц."""
    svc = request.app.state.svc
    pdir, meta = _require_project(svc, pid)
    async with _project_lock(pdir):
        key, _ = _require_component(pdir, name)
        items = _load_components(pdir)
        items.pop(key, None)
        _save_components(pdir, items)
        left = _count_instances(pdir, _load_meta(pdir) or {}, key)
    return {"ok": True, "removed": key, "instances_left": left,
            "note": "экземпляры остались в страницах как обычная разметка"}


# ---------------------------------------------------------------- токены

@router.get("/web-designer/projects/{pid}/tokens")
async def get_tokens(pid: int, request: Request):
    """Набор токенов проекта и CSS, который из него получается."""
    svc = request.app.state.svc
    pdir, _ = _require_project(svc, pid)
    tokens = _load_tokens(pdir)
    return {"tokens": tokens, "css": gen.render_tokens_css(tokens),
            "groups": list(gen.TOKEN_GROUPS), "style_id": dom.TOKENS_STYLE_ID}


@router.put("/web-designer/projects/{pid}/tokens")
async def put_tokens(pid: int, body: TokensIn, request: Request):
    """Обновить токены и применить их ко всем страницам проекта.

    Применение идёт через ОДИН управляемый блок `<style id="bd-tokens">`:
    смена акцента меняет один узел документа, а не переписывает инлайновый
    стиль каждого элемента. Иначе первая же смена темы стирала бы дословное
    написание всей вёрстки владельца.
    """
    svc = request.app.state.svc
    pdir, _ = _require_project(svc, pid)
    incoming = _check_tokens(body.tokens)
    async with _project_lock(pdir):
        _require_version(pdir, body.base_version)
        tokens = {} if body.replace else _load_tokens(pdir)
        for group, values in incoming.items():
            merged = dict(tokens.get(group) or {})
            merged.update(values)
            tokens[group] = merged
        _save_tokens(pdir, tokens)
        applied = _restyle_pages(svc, pdir, "обновлены токены оформления")
    return {"ok": True, "tokens": tokens, "css": gen.render_tokens_css(tokens),
            "pages": applied["pages"], "meta": applied["meta"]}


# ---------------------------------------------------------------- отзывчивость

@router.get("/web-designer/projects/{pid}/responsive")
async def list_rules(pid: int, request: Request):
    svc = request.app.state.svc
    pdir, _ = _require_project(svc, pid)
    state = _load_responsive(pdir)
    return {"items": state["rules"], "breakpoints": gen.BREAKPOINTS,
            "css": gen.render_responsive_css(state["rules"]),
            "limit": MAX_RESPONSIVE_RULES, "style_id": dom.RESPONSIVE_STYLE_ID}


@router.post("/web-designer/projects/{pid}/responsive")
async def create_rule(pid: int, body: RuleIn, request: Request):
    """Правило для брейкпоинта: селектор + свойства → настоящий медиазапрос."""
    svc = request.app.state.svc
    pdir, _ = _require_project(svc, pid)
    rule = {"breakpoint": _check_breakpoint(body.breakpoint),
            "selector": _check_selector(body.selector),
            "props": _check_css_props(body.props)}
    async with _project_lock(pdir):
        state = _load_responsive(pdir)
        if len(state["rules"]) >= MAX_RESPONSIVE_RULES:
            raise HTTPException(
                status_code=409,
                detail=f"достигнут предел в {MAX_RESPONSIVE_RULES} правил — удалите ненужные")
        seq = int(state["seq"]) + 1
        rule["id"] = f"r{seq}"
        rule["created_at"] = _now()
        state["rules"].append(rule)
        state["seq"] = seq
        _save_responsive(pdir, state)
        applied = _restyle_pages(svc, pdir, f"брейкпоинт {rule['breakpoint']}: {rule['selector']}")
    return {"ok": True, "rule": rule, "items": state["rules"],
            "css": gen.render_responsive_css(state["rules"]),
            "pages": applied["pages"], "meta": applied["meta"]}


@router.delete("/web-designer/projects/{pid}/responsive/{rule_id}")
async def delete_rule(pid: int, rule_id: str, request: Request):
    svc = request.app.state.svc
    pdir, _ = _require_project(svc, pid)
    key = _check_rule_id(rule_id)
    async with _project_lock(pdir):
        state = _load_responsive(pdir)
        rest = [r for r in state["rules"] if str(r.get("id")) != key]
        if len(rest) == len(state["rules"]):
            raise HTTPException(status_code=404, detail=f"правило «{key}» не найдено")
        state["rules"] = rest
        _save_responsive(pdir, state)
        applied = _restyle_pages(svc, pdir, f"удалено правило {key}")
    return {"ok": True, "removed": key, "items": rest,
            "css": gen.render_responsive_css(rest),
            "pages": applied["pages"], "meta": applied["meta"]}


# ---------------------------------------------------------------- ассеты

@router.get("/web-designer/projects/{pid}/assets")
async def list_assets(pid: int, request: Request):
    svc = request.app.state.svc
    pdir, _ = _require_project(svc, pid)
    items = [{**a, "url": _asset_url(pid, a["id"]), "file": _asset_member(a)}
             for a in _load_assets(pdir)]
    return {"items": items, "limit": MAX_ASSETS, "max_bytes": MAX_ASSET_BYTES}


@router.post("/web-designer/projects/{pid}/assets")
async def upload_asset(pid: int, body: AssetIn, request: Request):
    """Загрузить картинку или шрифт в каталог проекта.

    Идентификатор — хэш СОДЕРЖИМОГО: одна и та же картинка, загруженная
    дважды, остаётся одним файлом, а `..` в идентификаторе невозможен по
    построению. Тип определяется по байтам, а не по имени файла.
    """
    svc = request.app.state.svc
    pdir, _ = _require_project(svc, pid)
    raw = _decode_asset(body.data)
    record = _asset_record(raw, body.name)
    async with _project_lock(pdir):
        items = _load_assets(pdir)
        existing = next((a for a in items if a.get("id") == record["id"]), None)
        if existing is None:
            if len(items) >= MAX_ASSETS:
                raise HTTPException(
                    status_code=409,
                    detail=f"достигнут предел в {MAX_ASSETS} файлов — удалите ненужные")
            _write_atomic(_asset_file(pdir, record), raw)
            items.append(record)
            _save_assets(pdir, items)
        else:
            record = existing
            if not _asset_file(pdir, record).is_file():   # запись есть, файла нет
                _write_atomic(_asset_file(pdir, record), raw)
    return {"ok": True, "asset": {**record, "url": _asset_url(pid, record["id"]),
                                  "file": _asset_member(record)}}


@router.get("/web-designer/projects/{pid}/assets/{asset_id}")
async def get_asset(pid: int, asset_id: str, request: Request):
    """Отдать файл ассета. Content-Type берётся ИЗ БАЙТОВ при каждой отдаче.

    Не из записи в assets.json и тем более не из имени файла: запись могла быть
    сделана другой версией панели или подправлена руками, а браузер поверит
    заголовку. `nosniff` закрывает вторую половину — угадывание типа браузером.
    """
    svc = request.app.state.svc
    pdir, _ = _require_project(svc, pid)
    key = _check_asset_id(asset_id)
    record = next((a for a in _load_assets(pdir) if a.get("id") == key), None)
    if record is None:
        raise HTTPException(status_code=404, detail="файл не найден")
    try:
        raw = _asset_file(pdir, record).read_bytes()
    except OSError:
        raise HTTPException(status_code=404, detail="файл не найден на диске")
    sniffed = sniff_asset(raw)
    content_type = sniffed[0] if sniffed else "application/octet-stream"
    return Response(content=raw, media_type=content_type, headers=ASSET_HEADERS)


@router.delete("/web-designer/projects/{pid}/assets/{asset_id}")
async def delete_asset(pid: int, asset_id: str, request: Request):
    svc = request.app.state.svc
    pdir, _ = _require_project(svc, pid)
    key = _check_asset_id(asset_id)
    async with _project_lock(pdir):
        items = _load_assets(pdir)
        record = next((a for a in items if a.get("id") == key), None)
        if record is None:
            raise HTTPException(status_code=404, detail="файл не найден")
        _asset_file(pdir, record).unlink(missing_ok=True)
        _save_assets(pdir, [a for a in items if a.get("id") != key])
    # Ссылки на удалённый файл в коде страниц остаются: панель не переписывает
    # вёрстку владельца молча. Битую картинку видно, а исчезнувший блок — нет.
    return {"ok": True, "removed": key}
