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

EH-02 (TRUTH-003 §3–§8): виды `terminal`, `github`, `memory`, `schedule`, `process`
добавлены как наблюдатели ПОСТ-СОСТОЯНИЯ. Правило одно для всех: ответ API,
exit code 0, «инструмент вызван», подписанный receipt — не доказательство;
доказательство — свежее чтение состояния мира. Нет читаемого пост-условия —
UNVERIFIED, не PASS. Недоступная сеть/сервис — UNVERIFIED, не PASS.
"""
from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import sqlalchemy as sa

Status = Literal["VERIFIED", "FAILED", "UNVERIFIED"]

# Таблицы, по которым разрешена детерминированная проверка «строка есть/поле
# равно» (read-only, allowlist — модель не может указать произвольную таблицу).
_DB_ALLOWLIST = {"tasks", "task_runs", "facts", "approvals", "tool_calls"}
KINDS = ("file", "db", "browser", "app", "terminal", "github", "memory", "schedule", "process")
GIT_LS_REMOTE_TIMEOUT_S = 20.0


def payload_digest(payload: Any) -> str:
    import json as _json
    return hashlib.sha256(_json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ExpectedState:
    kind: str                     # file | db | browser | app | terminal | github | memory | schedule | process
    target: str                   # путь / таблица / url|session / app_id
    expect: dict = field(default_factory=dict)
    # file: {"exists": True, "sha256": "...", "contains": "text", "min_bytes": N}
    # db:   {"where": {col: val}, "equals": {col: val}}
    # browser: {"title_contains": "...", "url_contains": "..."}
    # app: {"running": True}  (default True — MODULE 3, bcc/features/apps_control.py)
    # terminal: target = id сессии terminal_sessions; {"exit_code": 0, "path": "...", "exists": True|False,
    #           "sha256": "...", "contains": "..."} — exit code сам по себе даёт только UNVERIFIED (исполнено ≠ эффект)
    # github: target = "<remote> <ref>"; {"sha": "<40 hex>"} | {"exists": True|False} — свежий `git ls-remote`
    # memory: target = subject факта; {"predicate": "...", "contains": "...", "object": "...", "current": True}
    # schedule: target = id расписания; {"exists": True, "enabled": True, "kind": "...", "interval_minutes": N,
    #           "daily_time": "HH:MM", "payload_digest": "<sha256 task_template>"}
    # process: target = pid; {"running": True|False}


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
        if kind in KINDS and target:
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


async def _observe_terminal(exp: ExpectedState, *, svc, roots: list[Path]) -> tuple[ObservedState, Evidence]:
    """Сессия терминала: exit code — факт ИСПОЛНЕНИЯ; эффект — только через
    объявленный путь (перечитывается как файл)."""
    try:
        from .tables import terminal_sessions as ts_t
        async with svc.db.session() as s:
            row = (await s.execute(sa.select(ts_t).where(ts_t.c.id == exp.target))).first()
    except Exception as exc:  # noqa: BLE001
        return (ObservedState("terminal", exp.target, {"error": str(exc)[:200]}, time.time()),
                Evidence("terminal:session", f"observe failed: {exc}"))
    if row is None:
        return (ObservedState("terminal", exp.target, {"error": "terminal session not found"}, time.time()),
                Evidence("terminal:session", "session absent"))
    m = row._mapping
    obs: dict[str, Any] = {"status": m.get("status"), "exit_code": m.get("exit_code"), "command": str(m.get("command") or "")[:200]}
    ev_detail = f"session {exp.target} status={obs['status']} exit={obs['exit_code']}"
    if exp.expect.get("path"):
        fexp = ExpectedState("file", str(exp.expect["path"]),
                             {k: v for k, v in exp.expect.items() if k in ("exists", "sha256", "contains", "min_bytes")})
        fobs, fev = await _observe_file(fexp, roots=roots)
        obs["file"] = fobs.observed
        ev_detail += f"; {fev.detail}"
    return ObservedState("terminal", exp.target, obs, time.time()), Evidence("terminal:session+file", ev_detail)


def _ls_remote(remote: str, ref: str) -> tuple[dict[str, Any], str]:
    try:
        cp = subprocess.run(["git", "ls-remote", "--", remote, ref], capture_output=True, text=True,
                            timeout=GIT_LS_REMOTE_TIMEOUT_S, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"error": f"remote unreachable: {type(exc).__name__}"}, "ls-remote failed"
    if cp.returncode != 0:
        return {"error": f"remote query failed: {cp.stderr.strip()[:160]}"}, "ls-remote non-zero"
    lines = [ln.split("\t") for ln in cp.stdout.splitlines() if "\t" in ln]
    return {"found": bool(lines), "sha": lines[0][0] if lines else "", "ref": lines[0][1] if lines else ""}, \
        f"ls-remote {remote} {ref} -> {lines[0][0][:12] if lines else 'absent'}"


async def _observe_github(exp: ExpectedState) -> tuple[ObservedState, Evidence]:
    """Свежий запрос удалённого состояния: LOCAL_COMMIT != REMOTE_PUSH, API_200 != REMOTE_STATE."""
    parts = shlex.split(exp.target) if exp.target else []
    if len(parts) != 2:
        return (ObservedState("github", exp.target, {"error": "target must be '<remote> <ref>'"}, time.time()),
                Evidence("github:ls-remote", "bad target"))
    remote, ref = parts
    obs, detail = _ls_remote(remote, ref)
    return ObservedState("github", exp.target, obs, time.time()), Evidence("github:ls-remote", detail, obs.get("sha", "") or "")


async def _observe_memory(exp: ExpectedState, *, svc) -> tuple[ObservedState, Evidence]:
    """Независимое чтение факта из durable-хранилища (FactStore), не из ответа записи."""
    try:
        from .memory.facts import FactStore
        rows = await FactStore(svc).search(subject=exp.target, predicate=exp.expect.get("predicate") or None,
                                           current_only=bool(exp.expect.get("current", True)), limit=50)
    except Exception as exc:  # noqa: BLE001
        return (ObservedState("memory", exp.target, {"error": str(exc)[:200]}, time.time()),
                Evidence("memory:readback", f"observe failed: {exc}"))
    return (ObservedState("memory", exp.target, {"found": bool(rows), "facts": rows}, time.time()),
            Evidence("memory:readback", f"subject={exp.target!r} facts={len(rows)}"))


async def _observe_schedule(exp: ExpectedState, *, svc) -> tuple[ObservedState, Evidence]:
    """Свежая строка `schedules`: идентичность, каденция, enabled, digest шаблона."""
    try:
        from .. import db as _db
        async with svc.db.session() as s:
            row = (await s.execute(sa.select(_db.schedules).where(_db.schedules.c.id == int(exp.target)))).first()
    except (ValueError, TypeError):
        return (ObservedState("schedule", exp.target, {"error": "schedule id must be an int"}, time.time()),
                Evidence("schedule:row", "bad id"))
    except Exception as exc:  # noqa: BLE001
        return (ObservedState("schedule", exp.target, {"error": str(exc)[:200]}, time.time()),
                Evidence("schedule:row", f"observe failed: {exc}"))
    if row is None:
        return ObservedState("schedule", exp.target, {"exists": False}, time.time()), Evidence("schedule:row", "absent")
    m = dict(row._mapping)
    obs = {"exists": True, "enabled": bool(m.get("enabled")), "kind": m.get("kind"), "interval_minutes": m.get("interval_minutes"),
           "daily_time": m.get("daily_time"), "next_run_at": str(m.get("next_run_at") or ""),
           "payload_digest": payload_digest(m.get("task_template") or {})}
    return ObservedState("schedule", exp.target, obs, time.time()), Evidence("schedule:row", f"id={exp.target} enabled={obs['enabled']} kind={obs['kind']}")


async def _observe_process(exp: ExpectedState) -> tuple[ObservedState, Evidence]:
    try:
        pid = int(exp.target)
    except (TypeError, ValueError):
        return ObservedState("process", exp.target, {"error": "pid must be an int"}, time.time()), Evidence("process:pid", "bad pid")
    try:
        import psutil
        running = psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except Exception:  # noqa: BLE001 — без psutil: сигнал 0
        try:
            os.kill(pid, 0); running = True
        except ProcessLookupError:
            running = False
        except PermissionError:
            running = True
    return ObservedState("process", exp.target, {"running": bool(running), "pid": pid}, time.time()), Evidence("process:pid", f"pid {pid} running={running}")


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
    if exp.kind == "terminal":
        if "exit_code" in e and o.get("exit_code") != e["exit_code"]:
            return "FAILED", f"exit code {o.get('exit_code')!r}, ожидалось {e['exit_code']!r}"
        if o.get("status") not in ("finished", "exited", "completed", "done", None) and o.get("exit_code") is None:
            return "UNVERIFIED", f"команда ещё не завершилась (status={o.get('status')})"
        if not e.get("path"):
            # COMMAND_EXECUTED ≠ DESIRED_SIDE_EFFECT_VERIFIED: без объявленного эффекта — только исполнение
            return "UNVERIFIED", "exit code доказывает лишь исполнение команды; эффект не объявлен (path/exists/sha256/contains)"
        fo = o.get("file") or {}
        if "error" in fo:
            return "UNVERIFIED", f"наблюдение файла невозможно: {fo['error']}"
        fexp = ExpectedState("file", str(e["path"]), {k: v for k, v in e.items() if k in ("exists", "sha256", "contains", "min_bytes")})
        return _compare(fexp, ObservedState("file", str(e["path"]), fo, obs.observed_at))
    if exp.kind == "github":
        if e.get("exists") is False:
            return ("VERIFIED", "ref отсутствует на remote, как и ожидалось") if not o.get("found") \
                else ("FAILED", "ref существует на remote, а ожидалось отсутствие")
        if not o.get("found"):
            return "FAILED", "ref не найден свежим ls-remote"
        if e.get("sha"):
            if not str(o.get("sha", "")).startswith(str(e["sha"])):
                return "FAILED", f"remote sha {str(o.get('sha'))[:12]} ≠ ожидаемому {str(e['sha'])[:12]}"
            return "VERIFIED", "remote ref указывает на ожидаемый sha (свежий ls-remote)"
        if e.get("exists", True):
            return "VERIFIED", "ref существует на remote (свежий ls-remote)"
        return "UNVERIFIED", "ожидание github не задаёт проверяемого свойства"
    if exp.kind == "memory":
        facts = o.get("facts") or []
        if not facts:
            return "FAILED", "факт не найден независимым чтением"
        want_obj, want_sub = e.get("object"), e.get("contains")
        hits = [f for f in facts if (want_obj is None or f.get("object") == want_obj)
                and (want_sub is None or str(want_sub) in str(f.get("statement") or "") + str(f.get("object") or ""))]
        if not hits:
            return "FAILED", "прочитанный факт не содержит ожидаемого содержимого"
        if e.get("current", True) and not any(f.get("current") for f in hits):
            return "FAILED", "факт есть, но не актуален (перекрыт/истёк)"
        return "VERIFIED", "факт прочитан заново из хранилища и совпал"
    if exp.kind == "schedule":
        if e.get("exists", True) is False:
            return ("VERIFIED", "расписание отсутствует, как и ожидалось") if not o.get("exists") \
                else ("FAILED", "расписание существует, а ожидалось удаление")
        if not o.get("exists"):
            return "FAILED", "расписание не найдено свежим чтением"
        for key in ("enabled", "kind", "interval_minutes", "daily_time", "payload_digest"):
            if key in e and o.get(key) != e[key]:
                return "FAILED", f"{key}: ожидалось {e[key]!r}, наблюдается {o.get(key)!r}"
        if not any(k in e for k in ("enabled", "kind", "interval_minutes", "daily_time", "payload_digest", "exists")):
            return "UNVERIFIED", "ожидание расписания не задаёт проверяемого свойства"
        return "VERIFIED", "свежая строка расписания совпала с ожиданием"
    if exp.kind == "process":
        want = bool(e.get("running", True))
        if bool(o.get("running")) != want:
            return "FAILED", f"процесс {'жив' if o.get('running') else 'не найден'}, а ожидалось {'жив' if want else 'завершён'}"
        return "VERIFIED", "свежая проверка pid совпала с ожиданием"
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
    elif expected.kind == "terminal":
        obs, ev = await _observe_terminal(expected, svc=svc, roots=list(roots or []))
    elif expected.kind == "github":
        obs, ev = await _observe_github(expected)
    elif expected.kind == "memory":
        obs, ev = await _observe_memory(expected, svc=svc)
    elif expected.kind == "schedule":
        obs, ev = await _observe_schedule(expected, svc=svc)
    elif expected.kind == "process":
        obs, ev = await _observe_process(expected)
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
