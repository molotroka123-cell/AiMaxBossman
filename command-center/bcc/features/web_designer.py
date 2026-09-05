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

import json
import re
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .. import web_designer_dom as dom
from .. import web_designer_gen as gen
from . import Feature

router = APIRouter()

FEATURE = Feature(name="web_designer", router=router)

MAX_HTML_CHARS = dom.MAX_HTML_CHARS
MAX_PROJECTS = 100
MAX_VERSIONS = 50
AI_MAX_TOKENS = 8192

_TAG_RE = re.compile(r"<[a-zA-Z!/]")          # «похоже на HTML», а не случайный текст
_NOTE_RE = re.compile(r"[\r\n\t]+")


# ---------------------------------------------------------------- хранилище

def _root(svc) -> Path:
    return Path(svc.settings.data_dir) / "web_designer"


def _pdir(svc, pid: int) -> Path:
    return _root(svc) / str(int(pid))


def _load_meta(pdir: Path) -> dict | None:
    try:
        raw = json.loads((pdir / "project.json").read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, ValueError):
        return None


def _save_meta(pdir: Path, meta: dict) -> None:
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "project.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _current_path(pdir: Path) -> Path:
    return pdir / "current.html"


def _version_path(pdir: Path, version: int) -> Path:
    return pdir / "history" / f"v{int(version)}.html"


def _read_code(pdir: Path) -> str:
    try:
        return _current_path(pdir).read_text(encoding="utf-8")
    except OSError:
        return ""


def _next_id(svc) -> int:
    used = {int(d.name) for d in _root(svc).iterdir() if d.is_dir() and d.name.isdigit()}
    return (max(used) + 1) if used else 1


def _now() -> float:
    return round(time.time(), 3)


def _public_meta(meta: dict) -> dict:
    return {
        "id": meta["id"], "name": meta.get("name", ""), "prompt": meta.get("prompt", ""),
        "template": meta.get("template", ""), "palette": meta.get("palette", ""),
        "version": int(meta.get("version", 0)),
        "created_at": meta.get("created_at"), "updated_at": meta.get("updated_at"),
    }


def _save_code(svc, pdir: Path, html: str, note: str) -> dict:
    """Записать новую текущую версию + снимок в историю. Возвращает версию."""
    meta = _load_meta(pdir) or {}
    version = int(meta.get("version", 0)) + 1
    _current_path(pdir).write_text(html, encoding="utf-8")
    history = pdir / "history"
    history.mkdir(parents=True, exist_ok=True)
    _version_path(pdir, version).write_text(html, encoding="utf-8")
    meta.update({
        "id": pdir.name, "version": version, "updated_at": _now(),
    })
    versions = list(meta.get("versions") or [])
    versions.append({"version": version, "note": _note(note), "ts": meta["updated_at"],
                     "chars": len(html)})
    meta["versions"] = versions[-MAX_VERSIONS:]
    _save_meta(pdir, meta)
    # держим каталог истории в пределах MAX_VERSIONS снимков
    for old in sorted(history.glob("v*.html"))[:-MAX_VERSIONS]:
        old.unlink(missing_ok=True)
    return _public_meta(meta)


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


class GenerateIn(BaseModel):
    prompt: str = Field(default="", max_length=4000)
    name: str = Field(default="", max_length=120)
    template: str = "auto"
    palette: str = "auto"


class EditIn(BaseModel):
    op: str = Field(min_length=1, max_length=16)
    bd_id: str | None = None
    path: str | None = None
    text: str | None = None
    props: dict[str, str] | None = None
    attrs: dict[str, str] | None = None
    html: str | None = Field(default=None, max_length=MAX_HTML_CHARS)


class AiEditIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    bd_id: str | None = None
    path: str | None = None


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
    pid = _next_id(svc)
    pdir = _pdir(svc, pid)
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
    return {"meta": _public_meta(meta or {}), "code": _read_code(pdir)}


@router.get("/web-designer/templates")
async def templates():
    return {"items": gen.templates_catalog(),
            "palettes": sorted(gen.PALETTES.keys())}


@router.get("/web-designer/projects/{pid}")
async def get_project(pid: int, request: Request):
    svc = request.app.state.svc
    pdir, meta = _require_project(svc, pid)
    return {"meta": _public_meta(meta), "code": _read_code(pdir),
            "versions": list(meta.get("versions") or [])[-MAX_VERSIONS:]}


@router.put("/web-designer/projects/{pid}/code")
async def put_code(pid: int, body: CodeIn, request: Request):
    svc = request.app.state.svc
    pdir, _ = _require_project(svc, pid)
    if not _TAG_RE.search(body.html[:2000]):
        raise HTTPException(status_code=422, detail="это не похоже на HTML-документ")
    meta = _save_code(svc, pdir, body.html, body.note or "правка кода")
    return {"ok": True, "meta": meta}


@router.post("/web-designer/projects/{pid}/generate")
async def generate_site(pid: int, body: GenerateIn, request: Request):
    """Собрать сайт по описанию. Хранится только финал; steps — для анимации в UI."""
    svc = request.app.state.svc
    pdir, meta = _require_project(svc, pid)
    result = gen.generate(body.prompt or meta.get("prompt", ""), name=body.name or meta.get("name", ""),
                          template=body.template, palette=body.palette)
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
    pdir, _ = _require_project(svc, pid)
    html = _read_code(pdir)
    if not html:
        raise HTTPException(status_code=409, detail="в проекте пока нет кода")
    try:
        new_html, described = dom.apply_edit(html, body.model_dump())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    note = f"{body.op}: {described.get('tag')}"
    if described.get("text"):
        note += f" «{described['text'][:40]}»"
    meta = _save_code(svc, pdir, new_html, note)
    return {"ok": True, "meta": meta, "element": described}


@router.get("/web-designer/projects/{pid}/preview", response_class=HTMLResponse)
async def preview(pid: int, request: Request):
    """HTML для iframe: с data-bd-id и пикером. Хранимый код не меняется."""
    svc = request.app.state.svc
    pdir, _ = _require_project(svc, pid)
    html = _read_code(pdir)
    if not html:
        raise HTTPException(status_code=409, detail="в проекте пока нет кода")
    try:
        return HTMLResponse(dom.inject_preview(html))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/web-designer/projects/{pid}/ai-edit")
async def ai_edit(pid: int, body: AiEditIn, request: Request):
    """Правка кода моделью из реестра. Модели нет — честный отказ."""
    svc = request.app.state.svc
    pdir, _ = _require_project(svc, pid)
    html = _read_code(pdir)
    if not html:
        raise HTTPException(status_code=409, detail="в проекте пока нет кода")
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
    meta = _save_code(svc, pdir, new_html, f"AI: {_note(body.prompt)}")
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
    meta = _save_code(svc, pdir, html, f"откат к версии {version}")
    return {"ok": True, "meta": meta, "code": html}


@router.delete("/web-designer/projects/{pid}")
async def delete_project(pid: int, request: Request):
    svc = request.app.state.svc
    pdir, _ = _require_project(svc, pid)
    shutil.rmtree(pdir, ignore_errors=True)
    return {"ok": True}
