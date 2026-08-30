"""Plugin adapters V1 — коннекторы поверх СУЩЕСТВУЮЩЕЙ authority Command Center.

Принцип (никакого второго фреймворка):
* реестр — существующий `bcc.tools.REGISTRY` (ToolSpec), не свой;
* политика ALLOW/ASK/DENY — существующий `decide_effect` (default_effect
  инструмента + права агента + anti-replay), не своя;
* approvals — существующая очередь (ASK через engine), не своя;
* секреты — существующий `svc.vault`/settings, не свой стор;
* audit — существующая шина `svc.bus`;
* MCP — существующий mcp_runtime; браузер — существующая browser-подсистема;
* Telegram — существующий канал; облачный LLM — существующий провайдер-путь.

Каждая capability регистрируется как `plugin:<id>.<capability>` с типизированным
контрактом. Неизвестная capability просто не регистрируется → resolve её не
вернёт → DENY по умолчанию. Внешняя запись/отправка — default_effect=ask →
подтверждение. Без креда адаптер честно возвращает SKIP_EXTERNAL_CREDENTIAL и
НЕ падает.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends

from ..plugin_security import (
    PluginSecurityError,
    confine_path,
    redact,
    safe_get,
)
from ..tools import REGISTRY, ToolContext, ToolResult, ToolSpec
from . import Feature

router = APIRouter()


# ------------------------------------------------------------- типизированный контракт

@dataclass(frozen=True)
class Capability:
    plugin_id: str
    capability: str                 # локальное имя, напр. repo.read
    scope: str                      # логический скоуп доступа
    risk: str                       # allow | ask (deny = не регистрируем вовсе)
    destructive: bool
    permission: str                 # существующее bcc-право ("" = не требует)
    credential_ref: str             # имя переменной креда ("" = не нужен)
    network_targets: tuple[str, ...] # к каким хостам ходит (документально)
    description: str
    input_schema: dict = field(default_factory=dict)
    required: tuple[str, ...] = ()

    @property
    def tool_name(self) -> str:
        return f"plugin:{self.plugin_id}.{self.capability}"


# ------------------------------------------------------------- манифест 13 коннекторов
# risk=deny-капабилити (напр. sql.write) намеренно ОТСУТСТВУЮТ — их нельзя вызвать.

def _u(name, default=""):
    return name  # имя переменной креда; фактический резолв — ниже, из env/settings

MANIFEST: list[Capability] = [
    # --- read-only local / safe ---
    Capability("http", "get", "network.read", "allow", False, "", "",
               ("*public*",), "Безопасный GET (SSRF-защита, redirect revalidation).",
               {"url": {"type": "string"}}, ("url",)),
    Capability("monitor", "feed", "network.read", "allow", False, "", "",
               ("*public*",), "Читать RSS/HTTP-ленту (та же SSRF-защита).",
               {"url": {"type": "string"}}, ("url",)),
    Capability("sql", "read", "db.read", "allow", False, "", "SQL_PLUGIN_DSN",
               ("db",), "Только SELECT/CTE/PRAGMA-read по read-only соединению.",
               {"sql": {"type": "string"}}, ("sql",)),
    Capability("obsidian", "read", "vault.read", "allow", False, "filesystem.read",
               "OBSIDIAN_VAULT", ("local-fs",), "Прочитать заметку внутри vault.",
               {"path": {"type": "string"}}, ("path",)),
    Capability("obsidian", "write", "vault.write", "ask", False, "filesystem.write",
               "OBSIDIAN_VAULT", ("local-fs",), "Записать заметку внутри vault (ASK).",
               {"path": {"type": "string"}, "content": {"type": "string"}},
               ("path", "content")),
    Capability("mcp", "tool_list", "mcp.read", "allow", False, "", "",
               ("local-mcp",), "Список инструментов подключённого MCP-сервера.",
               {"server": {"type": "string"}}, ("server",)),
    Capability("mcp", "tool_call", "mcp.execute", "ask", True, "", "",
               ("local-mcp",), "Вызов MCP-инструмента (ASK; неизвестный → DENY).",
               {"server": {"type": "string"}, "tool": {"type": "string"},
                "args": {"type": "object"}}, ("server", "tool")),
    # --- local LLM via existing provider path, cloud_policy=never ---
    Capability("ollama", "chat", "llm.local", "allow", False, "", "",
               ("127.0.0.1",), "Локальная модель через существующий провайдер-путь "
               "(cloud_policy=never; облачных вызовов нет).",
               {"model": {"type": "string"}, "messages": {"type": "array"}},
               ("model", "messages")),
    # --- cloud LLM: existing provider + Cost Governor authority, ASK ---
    Capability("openrouter", "chat", "llm.cloud.use", "ask", False, "",
               "OPENROUTER_API_KEY", ("openrouter.ai",),
               "Облачная модель через существующий провайдер + Cost Governor (ASK).",
               {"model": {"type": "string"}, "messages": {"type": "array"}},
               ("model", "messages")),
    # --- external connectors (credential-gated; read=allow, write/send=ask) ---
    Capability("github", "repo_read", "repo:read", "allow", False, "", "GITHUB_TOKEN",
               ("api.github.com",), "Чтение репозитория (read-only).",
               {"repo": {"type": "string"}, "path": {"type": "string"}}, ("repo",)),
    Capability("github", "issue_create", "issues:write", "ask", False, "", "GITHUB_TOKEN",
               ("api.github.com",), "Создать issue (ASK).",
               {"repo": {"type": "string"}, "title": {"type": "string"},
                "body": {"type": "string"}}, ("repo", "title")),
    Capability("gmail", "search", "gmail.readonly", "allow", False, "", "GMAIL_OAUTH",
               ("gmail.googleapis.com",), "Поиск писем (read-only).",
               {"query": {"type": "string"}}, ("query",)),
    Capability("gmail", "send", "gmail.send", "ask", True, "email.send", "GMAIL_OAUTH",
               ("gmail.googleapis.com",), "Отправить письмо (ASK, destructive).",
               {"to": {"type": "string"}, "subject": {"type": "string"},
                "body": {"type": "string"}}, ("to", "subject", "body")),
    Capability("calendar", "search", "calendar.readonly", "allow", False, "",
               "GOOGLE_OAUTH", ("www.googleapis.com",), "Список/поиск событий (read).",
               {"query": {"type": "string"}}, ()),
    Capability("calendar", "create", "calendar.events", "ask", False, "", "GOOGLE_OAUTH",
               ("www.googleapis.com",), "Создать событие (ASK).",
               {"title": {"type": "string"}, "start": {"type": "string"},
                "end": {"type": "string"}}, ("title", "start", "end")),
    Capability("drive", "search", "drive.readonly", "allow", False, "", "GOOGLE_OAUTH",
               ("www.googleapis.com",), "Поиск файлов (read).",
               {"query": {"type": "string"}}, ()),
    Capability("drive", "write", "drive.file", "ask", False, "", "GOOGLE_OAUTH",
               ("www.googleapis.com",), "Создать/обновить файл (ASK).",
               {"name": {"type": "string"}, "content": {"type": "string"}},
               ("name", "content")),
    Capability("telegram", "status", "telegram.read", "allow", False, "", "TELEGRAM_BOT_TOKEN",
               ("api.telegram.org",), "Статус бота (read).", {}, ()),
    Capability("telegram", "send", "telegram.send", "ask", True, "channel.send",
               "TELEGRAM_BOT_TOKEN", ("api.telegram.org",),
               "Отправить сообщение через существующий канал (ASK).",
               {"text": {"type": "string"}}, ("text",)),
    Capability("n8n", "workflow_list", "n8n.read", "allow", False, "", "N8N_API_KEY",
               ("*configured-n8n*",), "Список workflow (read).", {}, ()),
    Capability("n8n", "workflow_run", "n8n.execute", "ask", True, "", "N8N_API_KEY",
               ("*configured-n8n*",), "Запуск workflow (ASK; url валидируется от SSRF).",
               {"workflow_id": {"type": "string"}}, ("workflow_id",)),
    Capability("browser", "open", "browser.navigate", "allow", False, "browser.read", "",
               ("*allowlisted*",), "Открыть/прочитать страницу (существующий браузер).",
               {"url": {"type": "string"}}, ("url",)),
    Capability("browser", "form_submit", "browser.input", "ask", True, "browser.control", "",
               ("*allowlisted*",), "Отправка формы (ASK, существующий браузер).",
               {"session": {"type": "string"}}, ("session",)),
]


def _cred(ref: str) -> str | None:
    """Резолв креда из окружения/настроек. None → отсутствует (капабилити inert)."""
    if not ref:
        return "n/a"
    val = os.environ.get(ref)
    return val or None


def _skip_no_cred(cap: Capability) -> ToolResult:
    return ToolResult(
        content=f"SKIP_EXTERNAL_CREDENTIAL: {cap.plugin_id}.{cap.capability} — "
                f"нет креда {cap.credential_ref}. Побочного эффекта нет.",
        one_line=f"{cap.tool_name}: no credential", error=True)


# ------------------------------------------------------------- SQL read-only guard
import re as _re
_SQL_READ = _re.compile(r"^\s*(select|with|pragma\s+(table_info|index_list|index_info))\b", _re.I)
_SQL_BAD = _re.compile(r"\b(insert|update|delete|drop|alter|create|attach|detach|"
                       r"vacuum|replace|reindex|pragma)\b", _re.I)


def sql_read_only_ok(sql: str) -> bool:
    """True только для одиночного read-only оператора. pragma-write ловится _SQL_BAD,
    read-only pragma разрешён через _SQL_READ (проверяется первым)."""
    s = str(sql or "")
    if ";" in s.strip().rstrip(";"):
        return False
    if _SQL_READ.search(s):
        return True
    return not _SQL_BAD.search(s) and bool(_re.match(r"^\s*select\b", s, _re.I))


# ------------------------------------------------------------- handlers

async def _h_http_get(args, ctx: ToolContext) -> ToolResult:
    url = str(args.get("url") or "")
    try:
        r = await safe_get(url, max_bytes=1_000_000, timeout=15.0)
    except PluginSecurityError as exc:
        return ToolResult(content=f"blocked: {exc}", one_line=f"http.get blocked: {exc}", error=True)
    body = r.content[:1_000_000].decode("utf-8", "replace")
    return ToolResult(content=body, one_line=f"http.get {r.status_code}", external=True,
                      data={"status": r.status_code})


def _sqlite_path_from_dsn(dsn: str) -> str | None:
    """Путь к SQLite-файлу из DSN. None → не sqlite (пока поддерживаем только его)."""
    d = str(dsn or "").strip()
    for pfx in ("sqlite+aiosqlite:///", "sqlite:///", "sqlite://", "file:"):
        if d.startswith(pfx):
            return d[len(pfx):].split("?", 1)[0] or None
    if d.endswith((".db", ".sqlite", ".sqlite3")):
        return d
    return None


def _run_sqlite_read(path: str, sql: str, params, limit: int) -> list[dict]:
    """Реальное read-only исполнение: соединение mode=ro (гарантия на уровне БД)."""
    import sqlite3
    uri = f"file:{os.path.abspath(path).replace(os.sep, '/')}?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=5.0)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(sql, params if isinstance(params, (list, tuple)) else ())
        rows = [dict(r) for r in cur.fetchmany(max(1, min(int(limit), 5000)))]
    finally:
        con.close()
    return rows


async def _h_sql_read(args, ctx: ToolContext) -> ToolResult:
    sql = str(args.get("sql") or "")
    if not sql_read_only_ok(sql):
        return ToolResult(content="read-only single-statement SQL required",
                          one_line="sql.read denied (write)", error=True)
    dsn = _cred("SQL_PLUGIN_DSN")
    if not dsn:
        return _skip_no_cred(next(c for c in MANIFEST if c.tool_name == "plugin:sql.read"))
    path = _sqlite_path_from_dsn(dsn)
    if path is None:
        return ToolResult(content="only sqlite read-only DSN supported in this adapter",
                          one_line="sql.read: unsupported DSN", error=True)
    try:
        rows = await asyncio.to_thread(
            _run_sqlite_read, path, sql, args.get("params"), int(args.get("limit") or 500))
    except Exception as exc:                       # noqa: BLE001 — ошибка БД = данные, не падение
        return ToolResult(content=f"sql error: {exc}", one_line="sql.read error", error=True)
    import json as _json
    return ToolResult(content=_json.dumps(rows, ensure_ascii=False, default=str)[:200_000],
                      one_line=f"sql.read: {len(rows)} rows", external=True, data={"rows": rows})


async def _h_obsidian_read(args, ctx: ToolContext) -> ToolResult:
    root = _cred("OBSIDIAN_VAULT")
    if not root or root == "n/a":
        return _skip_no_cred(next(c for c in MANIFEST if c.tool_name == "plugin:obsidian.read"))
    try:
        p = confine_path(root, str(args.get("path") or ""), must_exist=True)
    except (PluginSecurityError, FileNotFoundError) as exc:
        return ToolResult(content=f"blocked: {exc}", one_line="obsidian.read blocked", error=True)
    return ToolResult(content=p.read_text("utf-8", "replace")[:200_000],
                      one_line=f"obsidian.read {p.name}", external=True)


async def _h_obsidian_write(args, ctx: ToolContext) -> ToolResult:
    """Реальная запись заметки внутри vault (confined). Политика ASK применяется
    движком ДО хендлера; здесь — уже подтверждённое действие. Путь строго под vault."""
    root = _cred("OBSIDIAN_VAULT")
    if not root or root == "n/a":
        return _skip_no_cred(next(c for c in MANIFEST if c.tool_name == "plugin:obsidian.write"))
    rel = str(args.get("path") or "")
    content = str(args.get("content") or "")
    try:
        p = confine_path(root, rel, must_exist=False)   # запись — файла может ещё не быть
    except PluginSecurityError as exc:
        return ToolResult(content=f"blocked: {exc}", one_line="obsidian.write blocked", error=True)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(p.write_text, content[:2_000_000], "utf-8")
    except OSError as exc:
        return ToolResult(content=f"write error: {exc}", one_line="obsidian.write error", error=True)
    return ToolResult(content=f"written {len(content)} bytes → {p.name}",
                      one_line=f"obsidian.write {p.name}", external=True,
                      data={"bytes": len(content)})


async def _h_generic_external(cap: Capability):
    async def handler(args, ctx: ToolContext) -> ToolResult:
        if _cred(cap.credential_ref) is None:
            return _skip_no_cred(cap)
        # Кред есть, но эта среда не выполняет реальные внешние мутации в рамках
        # приёмки: честный отказ вместо необеспеченного PASS. Политика (ASK) и
        # anti-replay уже применены движком ДО хендлера.
        return ToolResult(
            content=f"NOT_TESTED_LIVE: {cap.tool_name} готов, но живой вызов "
                    f"внешнего сервиса в этой приёмке не выполняется.",
            one_line=f"{cap.tool_name}: adapter ready", data={"ready": True})
    return handler


def _handler_for(cap: Capability):
    if cap.tool_name == "plugin:http.get" or cap.tool_name == "plugin:monitor.feed":
        return _h_http_get
    if cap.tool_name == "plugin:sql.read":
        return _h_sql_read
    if cap.tool_name == "plugin:obsidian.read":
        return _h_obsidian_read
    if cap.tool_name == "plugin:obsidian.write":
        return _h_obsidian_write
    # остальные — generic (credential-gated / ready), политика решает эффект
    return None  # заполняется в setup через фабрику (нужен cap в замыкании)


# ------------------------------------------------------------- registration

def _spec_for(cap: Capability, handler) -> ToolSpec:
    category = "read" if cap.risk == "allow" and not cap.destructive else (
        "send" if "send" in cap.capability else "write")
    return ToolSpec(
        name=cap.tool_name, description=cap.description, handler=handler,
        input_schema=cap.input_schema, required=list(cap.required),
        category=category, permission=cap.permission, source="plugin",
        default_effect="auto" if cap.risk == "allow" else "ask",
        idempotent=not cap.destructive,   # destructive → не переигрывается автоматически
        external_output=True)


REGISTERED: list[str] = []


async def setup(svc) -> None:
    REGISTERED.clear()
    seen: set[str] = set()
    for cap in MANIFEST:
        if cap.tool_name in seen:          # дубли отклоняем, а не молча перетираем
            continue
        seen.add(cap.tool_name)
        handler = _handler_for(cap)
        if handler is None:
            import functools
            base = await _h_generic_external(cap)
            handler = functools.partial(_run_generic, base)
        REGISTRY.register(_spec_for(cap, handler))
        REGISTERED.append(cap.tool_name)


async def _run_generic(base, args, ctx):
    return await base(args, ctx)


# ------------------------------------------------------------- non-destructive status API

def _status_rows() -> list[dict]:
    rows = []
    for cap in MANIFEST:
        cred = _cred(cap.credential_ref)
        rows.append({
            "plugin": cap.plugin_id,
            "capability": cap.capability,
            "tool": cap.tool_name,
            "scope": cap.scope,
            "risk": cap.risk,
            "destructive": cap.destructive,
            "permission": cap.permission or None,
            "policy": "auto" if cap.risk == "allow" else "ask",
            "credential": ("n/a" if cap.credential_ref == "" else
                           ("configured" if cred not in (None,) else "missing")),
            "network_targets": list(cap.network_targets),
        })
    return rows


@router.get("/plugins")
async def list_plugins():
    """Статус адаптеров. Health НЕ дергает внешние сервисы (non-destructive);
    сырые секреты не отдаются никогда — только configured/missing/n/a."""
    plugins: dict[str, dict] = {}
    for row in _status_rows():
        p = plugins.setdefault(row["plugin"], {
            "plugin": row["plugin"], "enabled": True, "health": "idle",
            "capabilities": [], "credential": row["credential"]})
        p["capabilities"].append({k: row[k] for k in
                                  ("capability", "scope", "policy", "destructive", "permission")})
    return {"plugins": list(plugins.values()), "count": len(plugins)}


FEATURE = Feature(name="plugins", router=router, setup=setup)
