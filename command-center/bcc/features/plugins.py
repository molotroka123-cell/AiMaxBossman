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

from fastapi import APIRouter, Depends, Request

from ..plugin_security import (
    PluginSecurityError,
    confine_path,
    redact,
    safe_get,
)
from ..tools import REGISTRY, ToolContext, ToolResult, ToolSpec
from ..v2 import openrouter_identity
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
               {"model": {"type": "string"}, "messages": {"type": "array"},
                "max_tokens": {"type": "integer"}}, ("model", "messages")),
    # --- cloud LLM: existing provider + Cost Governor authority, ASK ---
    Capability("openrouter", "chat", "llm.cloud.use", "ask", False, "",
               "OPENROUTER_API_KEY", ("openrouter.ai",),
               "Облачная модель через существующий провайдер + Cost Governor (ASK).",
               {"model": {"type": "string"}, "messages": {"type": "array"},
                "max_tokens": {"type": "integer"}}, ("model", "messages")),
    # --- external connectors (credential-gated; read=allow, write/send=ask) ---
    Capability("github", "repo_read", "repo:read", "allow", False, "", "GITHUB_TOKEN",
               ("api.github.com",), "Чтение публичного репозитория: метаданные, каталог, файл (read-only; токен необязателен).",
               {"repo": {"type": "string"}, "path": {"type": "string"}, "ref": {"type": "string"}},
               ("repo",)),
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


# Один кред — одно имя, но старые имена продолжают читаться: у владельца уже
# задана переменная, и молча перестать её видеть — это регресс, а не наведение
# порядка (audit-11, OR-003).
_CRED_ALIASES: dict[str, tuple[str, ...]] = {
    "OPENROUTER_API_KEY": (openrouter_identity.ENV_API_KEY, *openrouter_identity.LEGACY_ENV_API_KEYS),
}


def _cred(ref: str) -> str | None:
    """Резолв креда из окружения. None → отсутствует (капабилити inert).

    Ключ, введённый владельцем в интерфейсе, лежит зашифрованным в vault, а не в
    окружении; его подхватывает `resolve_cred` там, где доступен svc. Здесь
    остаётся окружение — общий путь для всех остальных кредов.
    """
    if not ref:
        return "n/a"
    for name in _CRED_ALIASES.get(ref, (ref,)):
        val = (os.environ.get(name) or "").strip()
        if val:
            return val
    return None


async def resolve_cred(ref: str, svc) -> str | None:
    """Кред с учётом хранилища: для OpenRouter ключ провайдера — источник правды.

    Владелец, подключивший OpenRouter на странице поставщика, справедливо ждёт,
    что плагин заработает от того же ключа; раньше плагин смотрел ТОЛЬКО в
    окружение и отвечал «нет креда» рядом с работающим провайдером.
    """
    if ref == "OPENROUTER_API_KEY" and svc is not None:
        try:
            found = await openrouter_identity.resolve(svc.db, svc.vault)
        except Exception:                       # noqa: BLE001 — БД недоступна: остаётся окружение
            return _cred(ref)
        return found.key or None
    return _cred(ref)


def _skip_no_cred(cap: Capability) -> ToolResult:
    return ToolResult(
        content=f"SKIP_EXTERNAL_CREDENTIAL: {cap.plugin_id}.{cap.capability} — "
                f"нет креда {cap.credential_ref}. Побочного эффекта нет.",
        one_line=f"{cap.tool_name}: no credential", error=True)


# ------------------------------------------------------------- SQL read-only guard
import re as _re
_SQL_STRING = _re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"")
_SQL_RO_PRAGMA = _re.compile(r"\bpragma\s+(?:table_info|index_list|index_xinfo|index_info)\s*\(",
                             _re.I)
_SQL_WRITE = _re.compile(r"\b(insert|update|delete|drop|alter|create|attach|detach|"
                         r"vacuum|replace|reindex|pragma|begin|commit|rollback|savepoint|"
                         r"release)\b", _re.I)


def sql_read_only_ok(sql: str) -> bool:
    """True только для одиночного read-only оператора (fail-closed).

    * ровно один оператор (`;` внутри — отказ);
    * строковые литералы вырезаются ПЕРЕД сканом ключевых слов — данные не
      триггерят ни ложных отказов, ни обходов;
    * write-токены запрещены везде, включая data-modifying CTE
      (`WITH … DELETE/INSERT/UPDATE`) и PRAGMA-запись; разрешены только формы
      `pragma (table_info|index_list|index_xinfo|index_info)(`;
    * оператор обязан начинаться с select/with/pragma.
    Драйверная гарантия остаётся: соединение `mode=ro` (_run_sqlite_read).
    """
    s = str(sql or "").strip()
    if s.endswith(";"):
        s = s[:-1].rstrip()
    if not s or ";" in s:
        return False
    scrubbed = _SQL_RO_PRAGMA.sub(" ", _SQL_STRING.sub(" ", s))
    if _SQL_WRITE.search(scrubbed):
        return False
    return bool(_re.match(r"^\s*(select|with|pragma)\b", s, _re.I))


# ------------------------------------------------------------- handlers

def _known_secret_values() -> set[str]:
    """Значения настроенных кредов — для скраба из внешнего контента/ошибок.
    Сами значения НИКОГДА не попадают в логи/аудит (см. plugin_security.redact)."""
    return {os.environ[ref] for ref in {c.credential_ref for c in MANIFEST}
            if ref and os.environ.get(ref)}


async def _h_http_get(args, ctx: ToolContext) -> ToolResult:
    url = str(args.get("url") or "")
    try:
        r = await safe_get(url, max_bytes=1_000_000, timeout=15.0)
    except PluginSecurityError as exc:
        return ToolResult(content=f"blocked: {exc}", one_line=f"http.get blocked: {exc}", error=True)
    body = r.content[:1_000_000].decode("utf-8", "replace")
    # настроенные секреты не должны утекать через эхо внешнего контента
    body = redact(body, secret_values=_known_secret_values())
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
    from pathlib import Path
    # as_uri() кодирует `#`, `%`, `?` и пробелы: без этого `#` в имени папки
    # обрывал путь и отрезал `?mode=ro` — открывался другой файл и без read-only.
    uri = f"{Path(os.path.abspath(path)).as_uri()}?mode=ro"
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
    # Пустой путь, "." или подкаталог законно дают каталог внутри vault: read_text
    # по нему бросал сырой PermissionError/IsADirectoryError с АБСОЛЮТНЫМ путём.
    if not p.is_file():
        return ToolResult(content="blocked: not a note file inside the vault",
                          one_line="obsidian.read blocked", error=True)
    try:
        text = p.read_text("utf-8", "replace")
    except OSError as exc:
        return ToolResult(content=f"read error: {type(exc).__name__} ({p.name})",
                          one_line="obsidian.read error", error=True)
    return ToolResult(content=text[:200_000],
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



# --------------------------------------------------- реальные обработчики 06.10 (ollama / openrouter / github / mcp)
# Эти четыре капабилити раньше отвечали общей заглушкой NOT_TESTED_LIVE. Теперь
# каждая делает настоящую работу на тех же границах: фиксированные хосты, SSRF-
# защита (safe_get), политика ALLOW/ASK и ключи — до хендлера, в движке.

_OLLAMA_V1 = "http://127.0.0.1:11434/v1"          # единственный разрешённый адрес (петля)
_OPENROUTER_V1 = "https://openrouter.ai/api/v1"   # фиксированный адрес; из аргументов не приходит
_CHAT_ROLES = {"system", "user", "assistant"}
_CHAT_MAX_MESSAGES = 40
_CHAT_MAX_CHARS = 60_000
_CHAT_MAX_TOKENS = 2048
_MODEL_RE = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+ -]{0,198}$")
_CLOUD_TAG_RE = _re.compile(r"(?i)[:-]cloud\b")


def _validated_chat(args) -> tuple[str, list[dict], int] | str:
    """(model, messages, max_tokens) либо строка-причина отказа."""
    model = args.get("model")
    if not isinstance(model, str) or not _MODEL_RE.match(model.strip()):
        return "model: нужна строка-идентификатор модели"
    msgs = args.get("messages")
    if not isinstance(msgs, list) or not msgs or len(msgs) > _CHAT_MAX_MESSAGES:
        return f"messages: непустой список, не более {_CHAT_MAX_MESSAGES}"
    clean: list[dict] = []
    total = 0
    for m in msgs:
        if not isinstance(m, dict) or m.get("role") not in _CHAT_ROLES or not isinstance(m.get("content"), str):
            return "messages: каждый элемент = {role: system|user|assistant, content: str}"
        total += len(m["content"])
        clean.append({"role": m["role"], "content": m["content"]})
    if total > _CHAT_MAX_CHARS:
        return f"messages: суммарно не более {_CHAT_MAX_CHARS} символов"
    try:
        mt = int(args.get("max_tokens") or 512)
    except (TypeError, ValueError):
        return "max_tokens: целое число"
    return model.strip(), clean, max(1, min(mt, _CHAT_MAX_TOKENS))


async def _chat_via_provider(label: str, base_url: str, api_key: str | None, model: str,
                             messages: list[dict], max_tokens: int, *, local: bool) -> ToolResult:
    from .. import providers as _prov
    try:
        adapter = _prov.build_adapter("openai_compat", base_url, api_key)
        res = await adapter.chat(model, messages, max_tokens=max_tokens, temperature=0.2, timeout=90.0)
    except _prov.ProviderError as exc:
        return ToolResult(content=f"{label}: {exc}", one_line=f"{label}: provider error", error=True,
                          data={"performed": False})
    except Exception as exc:                         # noqa: BLE001 — сеть/SDK: данные, не падение
        return ToolResult(content=f"{label}: {type(exc).__name__}", one_line=f"{label}: error",
                          error=True, data={"performed": False})
    text = redact(res.text or "", secret_values=_known_secret_values() | ({api_key} if api_key else set()))
    if not text:
        return ToolResult(content=f"{label}: модель вернула пустой ответ", one_line=f"{label}: empty",
                          error=True, data={"performed": False})
    return ToolResult(content=text, one_line=f"{label} {res.model or model}: {res.tokens_out} tok",
                      external=True,
                      data={"performed": True, "model": res.model or model, "local": local,
                            "tokens_in": res.tokens_in, "tokens_out": res.tokens_out, "finish": res.finish})


async def _h_ollama_chat(args, ctx: ToolContext) -> ToolResult:
    v = _validated_chat(args)
    if isinstance(v, str):
        return ToolResult(content=f"blocked: {v}", one_line="ollama.chat blocked", error=True)
    model, messages, max_tokens = v
    # Ollama маркирует облачные модели суффиксом `-cloud`/`:cloud`: плагин объявлен
    # как cloud_policy=never, облако через него не ходит.
    if _CLOUD_TAG_RE.search(model):
        return ToolResult(content="blocked: облачные модели Ollama запрещены этим плагином (cloud_policy=never)",
                          one_line="ollama.chat blocked (cloud)", error=True)
    return await _chat_via_provider("ollama.chat", _OLLAMA_V1, None, model, messages, max_tokens, local=True)


async def _openrouter_key(svc) -> str | None:
    """Ключ: файл ключей владельца → хранилище/окружение (тот же порядок, что у cloud-worker)."""
    try:
        from .coding_tasks import _worker_key
        return await _worker_key("OPENROUTER_API_KEY", svc)
    except Exception:                                # noqa: BLE001
        return await resolve_cred("OPENROUTER_API_KEY", svc)


async def _h_openrouter_chat(args, ctx: ToolContext) -> ToolResult:
    v = _validated_chat(args)
    if isinstance(v, str):
        return ToolResult(content=f"blocked: {v}", one_line="openrouter.chat blocked", error=True)
    model, messages, max_tokens = v
    # Этот адаптер — бесплатный путь: платные модели идут только через основной
    # провайдер с Cost Governor и бюджетом. Здесь деньги не тратятся никогда.
    if not model.endswith(":free"):
        return ToolResult(content="blocked: плагин openrouter.chat принимает только бесплатные модели (…:free); "
                                  "платные идут через основной провайдер с лимитом расходов",
                          one_line="openrouter.chat blocked (paid model)", error=True)
    key = await _openrouter_key(getattr(ctx, "svc", None))
    if not key:
        return _skip_no_cred(next(c for c in MANIFEST if c.tool_name == "plugin:openrouter.chat"))
    return await _chat_via_provider("openrouter.chat", _OPENROUTER_V1, key, model, messages, max_tokens, local=False)


_GH_REPO_RE = _re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_GH_REF_RE = _re.compile(r"^[A-Za-z0-9_./-]{1,200}$")
_GH_FILE_LIMIT = 200_000


def _gh_path_ok(path: str) -> bool:
    if len(path) > 300 or "\\" in path or "\x00" in path:
        return False
    return all(seg not in ("", ".", "..") for seg in path.split("/")) if path else True


async def _h_github_repo_read(args, ctx: ToolContext) -> ToolResult:
    """Чтение публичного репозитория через api.github.com (только GET).

    Токен не обязателен: публичные репозитории читаются анонимно (лимит GitHub ниже).
    Если GITHUB_TOKEN задан, он уходит только на api.github.com — allowed_hosts
    запрещает любой redirect на другой хост, так что заголовок не утечёт."""
    import base64
    import json as _json
    from urllib.parse import quote
    repo = str(args.get("repo") or "").strip()
    path = str(args.get("path") or "").strip().strip("/")
    ref = str(args.get("ref") or "").strip()
    if not _GH_REPO_RE.match(repo) or not _gh_path_ok(path) or (ref and not _GH_REF_RE.match(ref)):
        return ToolResult(content="blocked: repo = owner/name, path без '..', ref — имя ветки/тега",
                          one_line="github.repo_read blocked", error=True)
    url = f"https://api.github.com/repos/{repo}" + (f"/contents/{quote(path)}" if path else "")
    if ref and path:
        url += f"?ref={quote(ref, safe='')}"
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = await safe_get(url, allowed_hosts={"api.github.com"}, max_bytes=2_000_000,
                           timeout=20.0, headers=headers)
    except PluginSecurityError as exc:
        return ToolResult(content=f"blocked: {exc}", one_line=f"github.repo_read blocked: {exc}", error=True)
    except Exception as exc:                         # noqa: BLE001 — сеть: данные, не падение
        return ToolResult(content=f"github.repo_read: {type(exc).__name__}", one_line="github.repo_read error", error=True)
    if r.status_code != 200:
        why = {404: "не найдено (репозиторий/путь)", 403: "доступ/лимит GitHub (rate limit)",
               401: "токен отклонён", 429: "лимит запросов GitHub"}.get(r.status_code, f"HTTP {r.status_code}")
        return ToolResult(content=f"github.repo_read: {why}", one_line=f"github.repo_read {r.status_code}",
                          error=True, data={"status": r.status_code})
    try:
        body = r.json()
    except ValueError:
        return ToolResult(content="github.repo_read: ответ не JSON", one_line="github.repo_read bad body", error=True)
    secrets = _known_secret_values()
    if isinstance(body, list):                       # каталог
        items = [{"name": str(i.get("name")), "type": str(i.get("type")), "size": i.get("size")}
                 for i in body[:500] if isinstance(i, dict)]
        text = redact(_json.dumps(items, ensure_ascii=False), secret_values=secrets)
        return ToolResult(content=text, one_line=f"github.repo_read {repo}/{path}: {len(items)} entries",
                          external=True, data={"kind": "dir", "entries": items})
    if not isinstance(body, dict):
        return ToolResult(content="github.repo_read: неожиданная форма ответа", one_line="github.repo_read bad body", error=True)
    if body.get("type") == "file":                   # файл
        if body.get("encoding") != "base64" or int(body.get("size") or 0) > _GH_FILE_LIMIT * 4:
            return ToolResult(content="github.repo_read: файл слишком большой или без base64-содержимого",
                              one_line="github.repo_read too large", error=True, data={"size": body.get("size")})
        try:
            raw = base64.b64decode(str(body.get("content") or ""))
        except (ValueError, TypeError):
            return ToolResult(content="github.repo_read: повреждённое содержимое", one_line="github.repo_read bad body", error=True)
        text = redact(raw.decode("utf-8", "replace")[:_GH_FILE_LIMIT], secret_values=secrets)
        return ToolResult(content=text, one_line=f"github.repo_read {repo}/{path}: {len(raw)} bytes",
                          external=True, data={"kind": "file", "size": len(raw), "sha": body.get("sha")})
    meta = {k: body.get(k) for k in ("full_name", "description", "default_branch", "private", "language",
                                     "stargazers_count", "forks_count", "pushed_at", "license")}
    if isinstance(meta.get("license"), dict):
        meta["license"] = meta["license"].get("spdx_id")
    text = redact(_json.dumps(meta, ensure_ascii=False), secret_values=secrets)
    return ToolResult(content=text, one_line=f"github.repo_read {repo}", external=True,
                      data={"kind": "repo", "meta": meta})


async def _h_mcp_tool_list(args, ctx: ToolContext) -> ToolResult:
    """Инструменты УЖЕ настроенного MCP-сервера (имя из таблицы mcp_servers).

    Произвольную команду плагин запустить не может: сервер берётся только из
    настроенных, а launch_refusal (allowlist бинарников) применяется перед
    стартом процесса — те же правила, что у обычного connect."""
    svc = getattr(ctx, "svc", None)
    name = str(args.get("server") or "").strip()
    if svc is None or not name:
        return ToolResult(content="blocked: нужен server (имя настроенного MCP-сервера) и сервисы Command Center",
                          one_line="mcp.tool_list blocked", error=True)
    from fastapi import HTTPException
    from . import tools_mcp as _m
    try:
        row = await _m._server_row(svc, name, by_name=True)
    except HTTPException:
        return ToolResult(content=f"blocked: MCP-сервер {name!r} не настроен", one_line="mcp.tool_list unknown server", error=True)
    if not row.get("enabled", True):
        return ToolResult(content=f"blocked: MCP-сервер {name!r} выключен", one_line="mcp.tool_list disabled", error=True)
    spec = _m._spec_from_row(row)
    refusal = _m.launch_refusal(spec)
    if refusal:
        return ToolResult(content=f"blocked: {refusal}", one_line="mcp.tool_list blocked (allowlist)", error=True)
    rt = _m.runtime_of(svc)
    try:
        await rt.ensure(spec)
        views = await rt.list_tools(spec.id)
    except (_m.MCPUnavailable, _m.MCPCallError) as exc:
        return ToolResult(content=f"MCP-сервер {name} недоступен: {exc}", one_line="mcp.tool_list unavailable", error=True)
    tools = [{"name": _m.sanitize_text(v.name, 120),
              "description": _m.untrusted_description(spec.id, v.name, getattr(v, "description", ""))}
             for v in views[:200]]
    import json as _json
    return ToolResult(content=_json.dumps(tools, ensure_ascii=False)[:_m.MCP_OUTPUT_LIMIT],
                      one_line=f"mcp.tool_list {name}: {len(tools)} tools", external=True,
                      data={"server": name, "tools": [t["name"] for t in tools]})


async def _h_generic_external(cap: Capability):
    async def handler(args, ctx: ToolContext) -> ToolResult:
        if await resolve_cred(cap.credential_ref, getattr(ctx, "svc", None)) is None:
            return _skip_no_cred(cap)
        # Кред есть, но эта среда не выполняет реальные внешние мутации в рамках
        # приёмки: честный отказ вместо необеспеченного PASS. Политика (ASK) и
        # anti-replay уже применены движком ДО хендлера. Действие НЕ выполнено —
        # значит это ошибка: иначе модель видит успешный вызов и докладывает
        # «отправлено» про письмо, которого не было.
        return ToolResult(
            content=f"NOT_TESTED_LIVE: {cap.tool_name} — живой вызов внешнего "
                    f"сервиса в этой сборке не выполняется; действие НЕ выполнено.",
            one_line=f"{cap.tool_name}: not performed", error=True,
            data={"ready": True, "performed": False})
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
    if cap.tool_name == "plugin:ollama.chat":
        return _h_ollama_chat
    if cap.tool_name == "plugin:openrouter.chat":
        return _h_openrouter_chat
    if cap.tool_name == "plugin:github.repo_read":
        return _h_github_repo_read
    if cap.tool_name == "plugin:mcp.tool_list":
        return _h_mcp_tool_list
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

async def _status_rows(svc=None) -> list[dict]:
    rows = []
    for cap in MANIFEST:
        cred = await resolve_cred(cap.credential_ref, svc)
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
async def list_plugins(request: Request = None):     # noqa: RUF013 — FastAPI подставляет запрос сам
    """Статус адаптеров. Health НЕ дергает внешние сервисы (non-destructive);
    сырые секреты не отдаются никогда — только configured/missing/n/a.
    Ключ, введённый в интерфейсе, тоже считается кредом: статус берётся тем же
    резолвером, что и вызов, иначе страница врёт про «missing»."""
    plugins: dict[str, dict] = {}
    svc = getattr(getattr(request, "app", None), "state", None)
    for row in await _status_rows(getattr(svc, "svc", None)):
        p = plugins.setdefault(row["plugin"], {
            "plugin": row["plugin"], "enabled": True, "health": "idle",
            "capabilities": [], "credential": row["credential"]})
        p["capabilities"].append({k: row[k] for k in
                                  ("capability", "scope", "policy", "destructive", "permission")})
    return {"plugins": list(plugins.values()), "count": len(plugins)}


FEATURE = Feature(name="plugins", router=router, setup=setup)
