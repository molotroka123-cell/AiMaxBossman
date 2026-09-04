"""F-012 — верификация по СВЕЖИМ внешним доказательствам, а не по самоотчёту.

Инвариант ядра: intent → action → … → fresh observation → verification.
До этого модуля «completed» мог наступить, когда воркер просто ПОВТОРЯЛ
строку критерия, а LLM-ревьюер отвечал «PASS» — текст выдавал себя за
доказательство. Здесь текст не имеет авторитета: сравниваются ОЖИДАЕМОЕ
внешнее состояние и НАБЛЮДЁННОЕ заново (файл перечитан, строка перезапрошена,
страница переснята). Что нельзя проверить детерминированно — UNVERIFIED, и
дальше только человек/независимая проверка; самоотчёт НИКОГДА не повышается
до VERIFIED.

Минимальный совместимый контракт (без нового движка):
  ExpectedState → observe() → ObservedState → compare → VerificationResult
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import sqlalchemy as sa

Status = Literal["VERIFIED", "FAILED", "UNVERIFIED"]

# Таблицы, по которым разрешена детерминированная проверка «строка есть/поле
# равно» (read-only, allowlist — модель не может указать произвольную таблицу).
_DB_ALLOWLIST = {"tasks", "task_runs", "facts", "approvals", "tool_calls"}


@dataclass(frozen=True, slots=True)
class ExpectedState:
    kind: str                     # file | db | browser | app
    target: str                   # путь / таблица / url|session / app_id
    expect: dict = field(default_factory=dict)
    # file: {"exists": True, "sha256": "...", "contains": "text", "min_bytes": N}
    # db:   {"where": {col: val}, "equals": {col: val}}
    # browser: {"title_contains": "...", "url_contains": "..."}
    # app: {"running": True}  (default True — MODULE 3, bcc/features/apps_control.py)


@dataclass(frozen=True, slots=True)
class ObservedState:
    kind: str
    target: str
    observed: dict
    observed_at: float


@dataclass(frozen=True, slots=True)
class Evidence:
    source: str                   # "file:reopen" | "db:query" | "browser:snapshot"
    detail: str
    hash: str = ""


@dataclass(frozen=True, slots=True)
class VerificationResult:
    status: Status
    expected: ExpectedState | None
    observed: ObservedState | None
    evidence: tuple[Evidence, ...]
    reason: str

    @property
    def verified(self) -> bool:
        return self.status == "VERIFIED"


def _contained(path: Path, roots: list[Path]) -> bool:
    p = path.resolve()
    for r in roots:
        try:
            p.relative_to(r.resolve())
            return True
        except ValueError:
            continue
    return False


def parse_expected(raw: Any) -> list[ExpectedState]:
    """Из meta.review.evidence (список dict) → typed. Невалидные записи
    отбрасываются молча НЕ будут: они дают UNVERIFIED через пустой список."""
    out: list[ExpectedState] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip().lower()
        target = str(item.get("target") or "").strip()
        if kind in ("file", "db", "browser", "app") and target:
            out.append(ExpectedState(kind=kind, target=target,
                                     expect=dict(item.get("expect") or {})))
    return out


# ------------------------------------------------------------- observers

async def _observe_file(exp: ExpectedState, *, roots: list[Path]) -> tuple[ObservedState, Evidence]:
    p = Path(exp.target).expanduser()
    if not p.is_absolute():
        p = (roots[0] / p) if roots else p
    p = p.resolve()
    if roots and not _contained(p, roots):
        obs = {"error": "path outside allowed roots", "exists": False}
        return ObservedState("file", str(p), obs, time.time()), Evidence("file:reopen", "refused: outside roots")
    if not p.exists() or not p.is_file():
        return (ObservedState("file", str(p), {"exists": False}, time.time()),
                Evidence("file:reopen", "file absent"))
    data = p.read_bytes()                       # свежее чтение, не кэш
    sha = hashlib.sha256(data).hexdigest()
    text = data.decode("utf-8", "replace")
    obs = {"exists": True, "sha256": sha, "bytes": len(data), "text": text}
    return (ObservedState("file", str(p), obs, time.time()),
            Evidence("file:reopen", f"{p} sha256={sha[:12]} bytes={len(data)}", sha))


async def _observe_db(exp: ExpectedState, *, svc) -> tuple[ObservedState, Evidence]:
    table = exp.target
    if table not in _DB_ALLOWLIST:
        return (ObservedState("db", table, {"error": "table not allowed"}, time.time()),
                Evidence("db:query", f"refused table {table}"))
    from .. import db as _db
    from ..v2 import tables as _v2t
    tbl = getattr(_db, table, None)
    if tbl is None:                 # Table не имеет bool() — явная проверка на None
        tbl = getattr(_v2t, table, None)
    if tbl is None:
        return (ObservedState("db", table, {"error": "table unknown"}, time.time()),
                Evidence("db:query", f"unknown table {table}"))
    where = dict(exp.expect.get("where") or {})
    conds = [tbl.c[k] == v for k, v in where.items() if k in tbl.c]
    if len(conds) != len(where):
        return (ObservedState("db", table, {"error": "unknown column in where"}, time.time()),
                Evidence("db:query", "unknown column"))
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(tbl).where(sa.and_(*conds)).limit(1))).first() if conds else None
    observed = dict(row._mapping) if row is not None else {}
    return (ObservedState("db", table, {"row": observed, "found": row is not None}, time.time()),
            Evidence("db:query", f"{table} where {where} -> {'found' if row is not None else 'absent'}"))


async def _observe_browser(exp: ExpectedState, *, svc, task: dict) -> tuple[ObservedState, Evidence]:
    """Свежий снимок ПОСЛЕДНЕЙ сессии этой задачи (не чужой). Нет сессии —
    наблюдать нечего → UNVERIFIED."""
    try:
        from ..features.browser import _mgr as _bmgr
        from .tables import browser_sessions as bs_t
        async with svc.db.session() as s:
            row = (await s.execute(sa.select(bs_t.c.id).where(sa.and_(
                bs_t.c.task_id == task.get("id"), bs_t.c.status == "running"))
                .order_by(bs_t.c.id.desc()).limit(1))).first()
        if row is None:
            return (ObservedState("browser", exp.target, {"error": "no live session"}, time.time()),
                    Evidence("browser:snapshot", "no session for task"))
        snap = await _bmgr(svc).snapshot(int(row._mapping["id"]), actor="verifier", approved=True)
        obs = {"title": str(getattr(snap, "title", "") or (snap or {}).get("title", "")),
               "url": str(getattr(snap, "url", "") or (snap or {}).get("url", ""))}
        return (ObservedState("browser", exp.target, obs, time.time()),
                Evidence("browser:snapshot", f"title={obs['title'][:60]!r} url={obs['url'][:80]}"))
    except Exception as exc:  # noqa: BLE001 — наблюдение недоступно → UNVERIFIED, не PASS
        return (ObservedState("browser", exp.target, {"error": str(exc)[:200]}, time.time()),
                Evidence("browser:snapshot", f"observe failed: {exc}"))


async def _observe_app(exp: ExpectedState, *, svc) -> tuple[ObservedState, Evidence]:
    """Свежее состояние процесса приложения (MODULE 3): переиспользует
    bcc/features/apps_control.process_info — тот же наблюдатель, что и
    ручка GET /apps/{id}/process, никакого второго источника правды."""
    try:
        from ..features import apps_control as _apps
        info = _apps.process_info(exp.target, svc.settings.data_dir)
    except Exception as exc:  # noqa: BLE001 — наблюдение недоступно → UNVERIFIED, не PASS
        return (ObservedState("app", exp.target, {"error": str(exc)[:200]}, time.time()),
                Evidence("app:status", f"observe failed: {exc}"))
    obs = {"running": bool(info.get("running")), "pid": info.get("pid"),
           "port_busy": info.get("port_busy")}
    return (ObservedState("app", exp.target, obs, time.time()),
            Evidence("app:status", f"running={obs['running']} pid={obs['pid']}"))


# ------------------------------------------------------------- compare

def _compare(exp: ExpectedState, obs: ObservedState) -> tuple[Status, str]:
    o = obs.observed
    if "error" in o:
        return "UNVERIFIED", f"наблюдение невозможно: {o['error']}"
    e = exp.expect
    if exp.kind == "file":
        if e.get("exists", True) and not o.get("exists"):
            return "FAILED", "файл отсутствует при свежем чтении"
        if not e.get("exists", True):
            return ("VERIFIED", "файл отсутствует, как и ожидалось") if not o.get("exists") \
                else ("FAILED", "файл существует, а ожидалось отсутствие")
        if e.get("sha256") and e["sha256"] != o.get("sha256"):
            return "FAILED", "sha256 не совпадает с ожидаемым"
        if e.get("contains") and str(e["contains"]) not in (o.get("text") or ""):
            return "FAILED", f"в файле нет ожидаемого фрагмента {str(e['contains'])[:40]!r}"
        if e.get("min_bytes") and int(o.get("bytes") or 0) < int(e["min_bytes"]):
            return "FAILED", "файл меньше ожидаемого размера"
        if not any(k in e for k in ("sha256", "contains", "min_bytes", "exists")):
            return "UNVERIFIED", "ожидание файла не задаёт проверяемого свойства"
        return "VERIFIED", "свежее чтение файла совпало с ожиданием"
    if exp.kind == "db":
        if not o.get("found"):
            return "FAILED", "строка не найдена свежим запросом"
        row = o.get("row") or {}
        for k, v in (e.get("equals") or {}).items():
            if row.get(k) != v:
                return "FAILED", f"поле {k}: ожидалось {v!r}, наблюдается {row.get(k)!r}"
        return "VERIFIED", "свежий запрос подтвердил ожидаемую строку"
    if exp.kind == "browser":
        if e.get("title_contains") and str(e["title_contains"]) not in (o.get("title") or ""):
            return "FAILED", "заголовок страницы не содержит ожидаемого"
        if e.get("url_contains") and str(e["url_contains"]) not in (o.get("url") or ""):
            return "FAILED", "URL страницы не содержит ожидаемого"
        if not (e.get("title_contains") or e.get("url_contains")):
            return "UNVERIFIED", "ожидание браузера не задаёт проверяемого свойства"
        return "VERIFIED", "свежий снимок страницы совпал с ожиданием"
    if exp.kind == "app":
        want_running = bool(e.get("running", True))
        got_running = bool(o.get("running"))
        if want_running != got_running:
            return ("FAILED", f"приложение {'не ' if not got_running else ''}запущено, "
                              f"а ожидалось {'запущено' if want_running else 'не запущено'}")
        return "VERIFIED", "свежая проверка процесса совпала с ожиданием"
    return "UNVERIFIED", f"неизвестный вид ожидания {exp.kind}"


async def verify(expected: ExpectedState, *, svc, task: dict,
                 roots: list[Path] | None = None) -> VerificationResult:
    if expected.kind == "file":
        obs, ev = await _observe_file(expected, roots=list(roots or []))
    elif expected.kind == "db":
        obs, ev = await _observe_db(expected, svc=svc)
    elif expected.kind == "browser":
        obs, ev = await _observe_browser(expected, svc=svc, task=task)
    elif expected.kind == "app":
        obs, ev = await _observe_app(expected, svc=svc)
    else:
        return VerificationResult("UNVERIFIED", expected, None, (), "неизвестный вид ожидания")
    status, reason = _compare(expected, obs)
    return VerificationResult(status, expected, obs, (ev,), reason)


async def verify_all(expected: list[ExpectedState], *, svc, task: dict,
                     roots: list[Path] | None = None) -> tuple[Status, str, list[VerificationResult]]:
    """Агрегат: любой FAILED → FAILED; иначе любой UNVERIFIED → UNVERIFIED;
    иначе (все VERIFIED, список непустой) → VERIFIED. Пустой список — UNVERIFIED:
    отсутствие ожиданий не есть доказательство."""
    if not expected:
        return "UNVERIFIED", "нет структурированных ожиданий — самоотчёт не является доказательством", []
    results = [await verify(e, svc=svc, task=task, roots=roots) for e in expected]
    if any(r.status == "FAILED" for r in results):
        r = next(r for r in results if r.status == "FAILED")
        return "FAILED", f"{r.expected.kind}:{r.expected.target}: {r.reason}", results
    if any(r.status == "UNVERIFIED" for r in results):
        r = next(r for r in results if r.status == "UNVERIFIED")
        return "UNVERIFIED", f"{r.expected.kind}:{r.expected.target}: {r.reason}", results
    return "VERIFIED", "; ".join(f"{r.expected.kind}:{r.expected.target} ✓" for r in results), results
