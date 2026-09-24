"""`bossman` terminal commands (Bossman 1.2): a control surface over the ONE
Command Center backend. No second engine, memory or queue lives here.

Machine commands (exec, -p, status, events, result, approve, deny, stop, …)
write JSON / JSONL to stdout only — no banner, no colour, no spinner, no
secret — and diagnostics to stderr. Exit codes: see records.EXIT_*.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from . import CLIENT_VERSION
from .api_client import BossmanError, Client, discover
from .console import sanitize, utf8_console
from .follow import Follower, FollowOptions, interrupted_result
from .human import View, print_json_line, render_status_bar, render_title, status_cells
from .ops import (Catalog, create_draft, env_cwd, init_record, new_request_id, resolve_agent,
                  start_run)
from .records import (EXIT_BLOCKED, EXIT_CONFLICT, EXIT_DISCONNECTED, EXIT_FAIL,
                      EXIT_INTERRUPTED, EXIT_NOT_FOUND, EXIT_NOT_SUPPORTED, EXIT_OK, EXIT_USAGE,
                      STATE_EXIT, record, state_of)

EXEC_SCHEMA = "bossman.exec.v1"
MAX_INPUT_BYTES = 256 * 1024
EXEC_DEFAULT_MAX_SECONDS = 2400.0        # одна учебная попытка — 40 минут

ERROR_EXIT = {"disconnected": EXIT_DISCONNECTED, "auth": EXIT_DISCONNECTED,
              "not_found": EXIT_NOT_FOUND, "not_supported": EXIT_NOT_SUPPORTED,
              "conflict": EXIT_CONFLICT, "usage": EXIT_USAGE, "blocked": EXIT_BLOCKED}

TERMINAL_COMMANDS = ("chat", "exec", "status", "events", "result", "resume", "approve", "deny",
                     "pause", "stop", "continue", "list", "keys", "code", "evolution", "repair",
                     "run", "evolve", "start", "version", "approvals", "tasks", "market")


class UsageError(Exception):
    pass


# ----------------------------------------------------------------- output


class Out:
    """Where a command writes. Machine mode: JSON on stdout, words on stderr."""

    def __init__(self, fmt: str):
        self.fmt = fmt                        # text | json | stream-json

    @property
    def machine(self) -> bool:
        return self.fmt in ("json", "stream-json")

    def json(self, obj: dict) -> None:
        print_json_line(obj)

    def say(self, text: str) -> None:
        stream = sys.stderr if self.machine else sys.stdout
        stream.write(sanitize(text) + "\n")
        stream.flush()


def fail(out: Out, exc: BossmanError | UsageError, *, what: str = "operation") -> int:
    if isinstance(exc, UsageError):
        code, message, hint, kind = EXIT_USAGE, str(exc), None, "usage"
    else:
        code = ERROR_EXIT.get(exc.kind, EXIT_FAIL)
        message, hint, kind = exc.message, exc.hint, exc.kind
    if out.machine:
        out.json(record("error", ok=False, operation=what, error=sanitize(message),
                        hint=sanitize(hint) if hint else None, kind=kind, exit_code=code))
    sys.stderr.write(f"bossman: {sanitize(message)}\n")
    if hint:
        sys.stderr.write(f"  подсказка: {sanitize(hint)}\n")
    sys.stderr.flush()
    return code


def connect(args) -> Client:
    target = discover(getattr(args, "url", None), getattr(args, "data_dir", None))
    return Client(target)


# ----------------------------------------------------------------- parser


def _common(p: argparse.ArgumentParser) -> None:
    # SUPPRESS: a subcommand must not reset what was given before it
    # (`bossman --url X status` keeps X).
    p.add_argument("--url", default=argparse.SUPPRESS,
                   help="адрес Bossman (по умолчанию: desktop.lock / BCC_PORT / 8800)")
    p.add_argument("--data-dir", default=argparse.SUPPRESS,
                   help="каталог данных Bossman (по умолчанию BCC_DATA_DIR)")


def _fmt(p: argparse.ArgumentParser, default: str = "text") -> None:
    p.add_argument("--output-format", choices=("text", "json", "stream-json"), default=default)
    p.add_argument("--json", action="store_const", const="json", dest="output_format",
                   help="машинный вывод (JSON)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bossman", description="Bossman в терминале: пульт того же Bossman (Command Center API).",
        epilog="Подробно: docs/owner/TERMINAL.md. Старые команды serve/task/project/models — "
               "Bossman Core, их смысл не менялся.")
    p.add_argument("-p", "--print", dest="print_prompt", metavar="PROMPT",
                   help="headless: выполнить запрос и выйти (для Claude Code и скриптов)")
    p.add_argument("--version", action="store_true", help="версия клиента")
    _common(p)
    p.add_argument("--output-format", choices=("text", "json", "stream-json"), default=None)
    p.add_argument("--agent")
    p.add_argument("--model")
    p.add_argument("--max-seconds", type=float, default=None)
    p.add_argument("--approval-mode", choices=("wait", "fail"), default=None)
    p.add_argument("--on-timeout", choices=("stop", "detach"), default="stop")
    p.add_argument("--cwd")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--plain", action="store_true", help="без цвета и рамок")
    sub = p.add_subparsers(dest="cmd")

    c = sub.add_parser("chat", help="разговор с Bossman (интерактивно)")
    _common(c)
    c.add_argument("--agent", default=argparse.SUPPRESS)
    c.add_argument("--cwd", default=argparse.SUPPRESS)
    c.add_argument("--plain", action="store_true", default=argparse.SUPPRESS)
    c.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)
    c.add_argument("--no-history", action="store_true", help="не сохранять историю ввода")
    c.add_argument("--session", help="продолжить сессию (id)")

    e = sub.add_parser("exec", help="машинное задание: JSON-вход, JSONL-выход (без TTY)")
    _common(e)
    e.add_argument("--input-file", help="UTF-8 файл схемы bossman.exec.v1 ('-' = stdin)")
    e.add_argument("--text", action="store_true", help="вход — это просто текст запроса")
    e.add_argument("--output-format", choices=("text", "json", "stream-json"), default="stream-json")
    e.add_argument("--json", action="store_const", const="stream-json", dest="output_format")
    e.add_argument("--detach", action="store_true", help="поставить задачу и сразу вернуть task_id")
    e.add_argument("--wait", action="store_true", help="ждать результат (по умолчанию)")
    e.add_argument("--agent")
    e.add_argument("--model")
    e.add_argument("--title")
    e.add_argument("--request-id", help="ключ идемпотентности (повтор = та же задача)")
    e.add_argument("--max-seconds", type=float, default=None)
    e.add_argument("--approval-mode", choices=("wait", "fail"), default=None)
    e.add_argument("--on-timeout", choices=("stop", "detach"), default=None)
    e.add_argument("--cwd")
    e.add_argument("--verbose", action="store_true")

    s = sub.add_parser("status", help="подключение, сборка, модели, очередь")
    _common(s)
    _fmt(s)

    ev = sub.add_parser("events", help="события задачи (история по курсору, --follow — вживую)")
    _common(ev)
    ev.add_argument("task_id", type=int)
    ev.add_argument("--after", type=int, default=0)
    ev.add_argument("--follow", action="store_true")
    ev.add_argument("--verbose", action="store_true")
    _fmt(ev, "stream-json")

    r = sub.add_parser("result", help="итог задачи")
    _common(r)
    r.add_argument("task_id", type=int)
    _fmt(r)

    rs = sub.add_parser("resume", help="продолжить беседу (сессию) или задачу (--task)")
    _common(rs)
    rs.add_argument("session_id", nargs="?")
    rs.add_argument("--task", type=int)
    rs.add_argument("--plain", action="store_true")
    _fmt(rs)

    for name, helptext in (("approve", "одобрить разрешение (решение владельца)"),
                           ("deny", "отклонить разрешение")):
        a = sub.add_parser(name, help=helptext)
        _common(a)
        a.add_argument("approval_id", type=int)
        if name == "approve":
            a.add_argument("--yes", action="store_true", help="без вопроса (только осознанно)")
        _fmt(a)

    for name, helptext in (("pause", "пауза задачи"), ("continue", "продолжить задачу после паузы")):
        a = sub.add_parser(name, help=helptext)
        _common(a)
        a.add_argument("task_id", type=int)
        _fmt(a)

    st = sub.add_parser("stop", help="остановить задачу; --all — глобальный STOP")
    _common(st)
    st.add_argument("task_id", type=int, nargs="?")
    st.add_argument("--all", action="store_true")
    _fmt(st)

    ls = sub.add_parser("list", help="модели, агенты, навыки, инструменты, задачи, разрешения")
    _common(ls)
    ls.add_argument("what", choices=("models", "agents", "skills", "tools", "tasks", "approvals"))
    ls.add_argument("--limit", type=int, default=20)
    _fmt(ls)

    for alias, what in (("tasks", "tasks"), ("approvals", "approvals")):
        a = sub.add_parser(alias, help=f"= list {what}")
        _common(a)
        a.add_argument("--limit", type=int, default=20)
        _fmt(a)
        a.set_defaults(what=what)

    k = sub.add_parser("keys", help="ключи облачных моделей (шифруются в Bossman)")
    _common(k)
    k.add_argument("action", nargs="?", default="list", choices=("list", "set", "remove", "import-env"))
    k.add_argument("vendor", nargs="?")
    k.add_argument("forbidden_value", nargs="?", help=argparse.SUPPRESS)
    k.add_argument("--stdin", action="store_true", help="прочитать ключ из stdin (одна строка)")
    k.add_argument("--base-url", help="другой адрес API провайдера")
    k.add_argument("--yes", action="store_true")
    k.add_argument("--no-models", action="store_true", help="не регистрировать модели провайдера")
    _fmt(k)

    cd = sub.add_parser("code", help="coding task через coding path (diff + проверка); "
                                     "`code apply <id>` — применить проверенного кандидата")
    _common(cd)
    cd.add_argument("instruction")
    # `bossman code apply <task_id> [--approval-id N]`: второй позиционный
    # аргумент есть только у apply; одобряет владелец (bossman approve / веб / Telegram).
    cd.add_argument("task_id", nargs="?", help=argparse.SUPPRESS)
    cd.add_argument("--approval-id", type=int, help="apply: одобренное владельцем разрешение")
    cd.add_argument("--allow", action="append", default=[], help="разрешённый путь (обязателен)")
    cd.add_argument("--protect", action="append", default=[])
    cd.add_argument("--verify", action="append", default=[], help="тест для независимой проверки")
    cd.add_argument("--repo", help="git-репозиторий (по умолчанию текущая папка)")
    cd.add_argument("--agent")
    cd.add_argument("--timeout", type=int, default=900)
    cd.add_argument("--no-memory", action="store_true")
    _fmt(cd)

    evo = sub.add_parser("evolution", help="цикл самоулучшения 1.1 (/api/evolution)")
    _common(evo)
    evo.add_argument("action", choices=("status", "pause", "resume", "stop", "report", "start"))
    _fmt(evo)

    rp = sub.add_parser("repair", help="один цикл self-repair (1.1, bossman_coding)")
    _common(rp)
    rp.add_argument("--self", dest="self_repair", action="store_true", required=True)
    rp.add_argument("--model")
    rp.add_argument("--max-seconds", type=float, default=2400.0)
    rp.add_argument("--plain", action="store_true")
    _fmt(rp)

    ru = sub.add_parser("run", help="run automation \"<задача>\" — задача с управлением компьютером")
    _common(ru)
    ru.add_argument("scenario", choices=("automation",))
    ru.add_argument("prompt")
    ru.add_argument("--local", action="store_true", help="только локальная модель")
    ru.add_argument("--agent")
    ru.add_argument("--approval-mode", choices=("wait", "fail"), default="wait")
    ru.add_argument("--max-seconds", type=float, default=None)
    ru.add_argument("--plain", action="store_true")
    _fmt(ru)

    ev2 = sub.add_parser("evolve", help="evolve --lab: лаборатория самоулучшения")
    _common(ev2)
    ev2.add_argument("--lab", action="store_true", required=True)
    ev2.add_argument("--local-model", action="store_true")
    ev2.add_argument("--case", default="sample")
    ev2.add_argument("--variants")
    ev2.add_argument("--plain", action="store_true")
    _fmt(ev2)

    rv = sub.add_parser("review", help="Bossman Vision: проверка видео Studio (review <run>, --run — проверить заново, --stats)")
    _common(rv)
    rv.add_argument("run_id", nargs="?")
    rv.add_argument("--run", action="store_true", help="проверить сейчас (локальная vision-модель)")
    rv.add_argument("--stats", action="store_true", help="насколько Vision совпадает с владельцем + правила")
    _fmt(rv)

    rt = sub.add_parser("rate", help="оценка владельца: rate <run> good|bad [\"что не так\"] — учит Bossman Vision")
    _common(rt)
    rt.add_argument("run_id")
    rt.add_argument("verdict", choices=("good", "bad"))
    rt.add_argument("reason", nargs="*")
    _fmt(rt)

    sa = sub.add_parser("start", help="запустить Bossman (backend), если он не запущен")
    _common(sa)
    sa.add_argument("--port", type=int)
    _fmt(sa)

    mk = sub.add_parser("market", help="read-only Twitch monitor: watch/status/export/stop")
    mk.add_argument("action", choices=("watch", "status", "export", "stop"))
    mk.add_argument("--root", help="локальный каталог market ledger (по умолчанию Bossman data)")
    mk.add_argument("--cadence", type=float, default=15.0)
    mk.add_argument("--minutes", type=float, default=60.0)
    mk.add_argument("--headed", action="store_true")
    mk.add_argument("--keep-frames", type=int, default=0)

    sub.add_parser("version", help="версия клиента")
    return p


# ----------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    utf8_console()
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:            # argparse: --help (0) или ошибка (2)
        return int(exc.code or 0)
    if args.version or args.cmd == "version":
        print(f"bossman-terminal {CLIENT_VERSION}")
        return EXIT_OK
    if args.print_prompt is not None:
        return cmd_print(args)
    if args.cmd in (None, "chat"):
        from .chat import run_chat
        return run_chat(args)
    handler = globals().get("cmd_" + args.cmd.replace("-", "_"))
    if handler is None:
        parser.print_help()
        return EXIT_USAGE
    return handler(args)


def cmd_market(args) -> int:
    """Use the same read-only collector and ledger from the owner terminal."""
    from bcc.market.collector import main as collector_main

    if args.cadence <= 0 or args.minutes <= 0 or args.keep_frames < 0:
        print("bossman market: cadence/minutes must be positive; keep-frames nonnegative", file=sys.stderr)
        return EXIT_USAGE
    command = "run" if args.action == "watch" else args.action
    argv = [command]
    if args.root:
        argv += ["--root", args.root]
    if command == "run":
        argv += ["--cadence", str(args.cadence), "--minutes", str(args.minutes),
                 "--keep-frames", str(args.keep_frames)]
        if args.headed:
            argv.append("--headed")
    return collector_main(argv)


# ----------------------------------------------------------------- exec / -p


def load_exec_input(path: str | None, *, text_mode: bool) -> dict:
    """bossman.exec.v1 from a UTF-8 file or stdin. Bounded; EOF-terminated;
    a non-JSON input is refused unless --text says it is a plain prompt."""
    if path in (None, "-"):
        if sys.stdin is None or (path is None and sys.stdin.isatty()):
            raise UsageError("нет входа: --input-file <файл> или данные в stdin")
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    else:
        try:
            with open(path, "rb") as fh:
                raw = fh.read(MAX_INPUT_BYTES + 1)
        except OSError as exc:
            raise UsageError(f"не прочитать {path}: {exc.strerror}") from None
    if len(raw) > MAX_INPUT_BYTES:
        raise UsageError(f"вход больше {MAX_INPUT_BYTES} байт")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise UsageError("вход не в UTF-8") from None
    if text_mode:
        if not text.strip():
            raise UsageError("пустой запрос")
        return {"schema": EXEC_SCHEMA, "prompt": text.strip()}
    try:
        data = json.loads(text)
    except ValueError:
        raise UsageError("вход не JSON (схема bossman.exec.v1); для простого текста — --text") from None
    if not isinstance(data, dict):
        raise UsageError("вход должен быть JSON-объектом")
    if data.get("schema") != EXEC_SCHEMA:
        raise UsageError(f"неизвестная схема входа: {sanitize(data.get('schema'))!s}; нужна {EXEC_SCHEMA}")
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise UsageError("в входе нет prompt (строка)")
    allowed = {"schema", "prompt", "title", "agent", "model", "cwd", "request_id", "wait",
               "max_seconds", "approval_mode", "on_timeout"}
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise UsageError("неизвестные поля входа: " + ", ".join(sanitize(u) for u in unknown))
    if data.get("approval_mode") not in (None, "wait", "fail"):
        raise UsageError("approval_mode: wait или fail")
    if data.get("on_timeout") not in (None, "stop", "detach"):
        raise UsageError("on_timeout: stop или detach")
    return data


def cmd_exec(args) -> int:
    out = Out(args.output_format)
    try:
        spec = load_exec_input(args.input_file, text_mode=args.text)
    except UsageError as exc:
        return fail(out, exc, what="exec")
    return run_headless(
        out, args, prompt=spec["prompt"], title=args.title or spec.get("title") or "",
        agent=args.agent or spec.get("agent"), model=args.model or spec.get("model"),
        cwd=args.cwd or spec.get("cwd"),
        request_id=args.request_id or spec.get("request_id") or new_request_id(),
        detach=bool(args.detach or spec.get("wait") is False),
        max_seconds=_max_seconds(args.max_seconds, spec.get("max_seconds")),
        approval_mode=args.approval_mode or spec.get("approval_mode") or "fail",
        on_timeout=args.on_timeout or spec.get("on_timeout") or "stop", verbose=args.verbose)


def _max_seconds(cli: float | None, spec: Any) -> float | None:
    value = cli if cli is not None else spec if isinstance(spec, (int, float)) else EXEC_DEFAULT_MAX_SECONDS
    return None if not value or value <= 0 else float(value)


def cmd_print(args) -> int:
    """`bossman -p "…"` — Claude Code / scripts. Same contract as exec."""
    fmt = args.output_format or "text"
    out = Out(fmt)
    prompt = args.print_prompt
    if prompt == "-":
        prompt = sys.stdin.read(MAX_INPUT_BYTES + 1) if sys.stdin else ""
    if not (prompt or "").strip():
        return fail(out, UsageError("пустой запрос"), what="print")
    return run_headless(out, args, prompt=prompt.strip(), title="", agent=args.agent, model=args.model,
                        cwd=args.cwd, request_id=new_request_id(), detach=False,
                        max_seconds=_max_seconds(args.max_seconds, None),
                        approval_mode=args.approval_mode or "fail", on_timeout=args.on_timeout,
                        verbose=args.verbose)


def run_headless(out: Out, args, *, prompt: str, title: str, agent: str | None, model: str | None,
                 cwd: str | None, request_id: str, detach: bool, max_seconds: float | None,
                 approval_mode: str, on_timeout: str, verbose: bool) -> int:
    view = None
    if out.fmt == "text":
        from .console import make_console
        view = View(make_console(stream=sys.stderr, plain=True), plain=True, verbose=verbose)
    try:
        client = connect(args)
    except BossmanError as exc:
        return fail(out, exc, what="connect")
    with client:
        try:
            cat = Catalog.load(client)
            chosen = resolve_agent(client, cat, prompt=prompt, agent_ref=agent, model_ref=model)
            init = init_record(client, cat, chosen, cwd=env_cwd(cwd), request_id=request_id)
            if out.fmt == "stream-json":
                out.json(init)
            sub = create_draft(client, prompt=prompt, title=title, agent=chosen, request_id=request_id)
        except BossmanError as exc:
            return fail(out, exc, what="submit")
        task_id = sub.task["id"]
        submitted = record("task", subtype="replayed" if sub.replayed else "submitted", task_id=task_id,
                           status=sub.task.get("status"), request_id=request_id)
        if out.fmt == "stream-json":
            out.json(submitted)
        if detach:
            try:
                admission = start_run(client, sub)
            except BossmanError as exc:
                return fail(out, exc, what="start")
            state = "BLOCKED" if isinstance(admission, dict) and admission.get("ok") is False else "RUNNING"
            res = record("result", ok=True, task_state=state, status=(admission or {}).get("status"),
                         task_id=task_id, run_id=(admission or {}).get("run_id"), detached=True,
                         exit_code=STATE_EXIT[state])
            _emit_result(out, res)
            return res["exit_code"]

        def sink(rec: dict) -> None:
            if out.fmt == "stream-json":
                out.json(rec)
            elif view is not None:
                view.on_record(rec)

        options = FollowOptions(approval_mode=approval_mode, max_seconds=max_seconds,
                                on_timeout=on_timeout, verbose=verbose)
        follower = Follower(client, task_id, sink=sink, options=options, after=0)
        follower.model_kind_hint = (init.get("model") or {}).get("model_kind")
        try:
            res = follower.run(before=lambda: start_run(client, sub))
        except KeyboardInterrupt:
            follower.request_stop()
            try:
                follower._await_stop()
            except KeyboardInterrupt:
                pass
            res = interrupted_result(follower.state,
                                     stop_confirmed=follower.state.status in ("stopped", "cancelled"))
        except BossmanError as exc:
            return fail(out, exc, what="follow")
        _emit_result(out, res)
        return int(res.get("exit_code", EXIT_FAIL))


def _emit_result(out: Out, res: dict) -> None:
    if out.machine:
        out.json(res)
        return
    # text: the answer on stdout (like `claude -p`), the verdict on stderr
    if res.get("result"):
        sys.stdout.write(sanitize(res["result"]).rstrip("\n") + "\n")
        sys.stdout.flush()
    sys.stderr.write(f"[{res.get('task_state')}] задача {res.get('task_id')}"
                     + (f": {sanitize(res.get('error'))}" if res.get("error") else "")
                     + (f" ({sanitize(res.get('note'))})" if res.get("note") else "") + "\n")


# ----------------------------------------------------------------- status & friends


def _simple(args, fn, *, what: str) -> int:
    out = Out(getattr(args, "output_format", "text") or "text")
    try:
        client = connect(args)
    except BossmanError as exc:
        return fail(out, exc, what="connect")
    with client:
        try:
            return fn(client, out)
        except BossmanError as exc:
            return fail(out, exc, what=what)
        except UsageError as exc:
            return fail(out, exc, what=what)


def cmd_status(args) -> int:
    def run(client: Client, out: Out) -> int:
        ident = client.target.identity
        cat = Catalog.load(client)
        try:
            health = client.get("/api/health")
        except BossmanError as exc:
            health = {"ready": False, "error": exc.message}
        tasks = client.get("/api/tasks", params={"status": "queued,running,waiting_approval,paused",
                                                 "limit": 50}) or []
        approvals = client.get("/api/approvals", params={"status": "pending"}) or []
        try:
            computer = client.get("/api/computer/status")
        except BossmanError:
            computer = None
        components = {k: (v or {}).get("status") for k, v in (health.get("components") or health.get("health")
                                                              or {}).items() if isinstance(v, dict)}
        rec = record("status", ok=True, client={"name": "bossman-terminal", "version": CLIENT_VERSION},
                     bossman={"url": client.target.url, "version": ident.get("version"),
                              "build_sha": ident.get("build_sha"),
                              "source_identity": ident.get("source_identity"),
                              "started_at": ident.get("started_at"),
                              "data_dir": str(client.target.data_dir)},
                     ready=health.get("ready"), components=components or None,
                     agents=[{"id": a.get("id"), "name": sanitize(a.get("name")), "enabled": a.get("enabled"),
                              **{"model": (cat.describe_agent(a).get("model") or {}).get("alias")}}
                             for a in cat.agents],
                     models=len(cat.models),
                     active_tasks=[{"id": t.get("id"), "status": t.get("status"),
                                    "title": sanitize(t.get("title"))[:120]} for t in tasks],
                     pending_approvals=[{"id": a.get("id"), "task_id": a.get("task_id"),
                                         "kind": a.get("kind")} for a in approvals],
                     computer=({"available": computer.get("available"), "stopped": computer.get("stopped")}
                               if isinstance(computer, dict) else None),
                     exit_code=EXIT_OK)
        if out.machine:
            out.json(rec)
            return EXIT_OK
        out.say(f"Bossman {ident.get('version')} · build {ident.get('build_sha_short') or ident.get('source_identity')}"
                f" · {client.target.url}")
        out.say(f"данные: {client.target.data_dir}")
        out.say(f"готов: {'да' if health.get('ready') else 'нет'}")
        for a in rec["agents"]:
            out.say(f"  агент #{a['id']} {a['name']} · модель {a.get('model') or '—'}"
                    + ("" if a.get("enabled") else " (выключен)"))
        out.say(f"активных задач: {len(tasks)} · ждут решения: {len(approvals)}")
        return EXIT_OK
    return _simple(args, run, what="status")


def cmd_result(args) -> int:
    def run(client: Client, out: Out) -> int:
        data = client.get(f"/api/tasks/{args.task_id}")
        task = data.get("task") or {}
        runs = data.get("runs") or []
        last = runs[-1] if runs else {}
        state = state_of(task.get("status"))
        rec = record("result", ok=True, task_state=state, status=task.get("status"),
                     task_id=task.get("id"), run_id=last.get("id"), title=sanitize(task.get("title")),
                     result=sanitize(data.get("result")) if data.get("result") else None,
                     error=sanitize(data.get("error")) if data.get("error") else None,
                     usage={"tokens_in": last.get("tokens_in"), "tokens_out": last.get("tokens_out")},
                     model=last.get("model_alias"), exit_code=STATE_EXIT.get(state, EXIT_FAIL))
        if out.machine:
            out.json(rec)
        else:
            out.say(f"задача {task.get('id')} · {task.get('status')} → {state}")
            if rec.get("result"):
                out.say(rec["result"])
            if rec.get("error"):
                out.say("ошибка: " + rec["error"])
        return rec["exit_code"]
    return _simple(args, run, what="result")


RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,40}")


def _bad_run_id(args) -> int | None:
    """A run id goes into a URL path: anything but an id (`../x`, `a/b`, `x?y`) is refused locally."""
    if args.run_id is not None and not RUN_ID.fullmatch(args.run_id):
        return fail(Out(getattr(args, "output_format", "text") or "text"),
                    UsageError("run id — это id прогона Studio (буквы, цифры, _ и -), не путь"), what=args.cmd)
    return None


def cmd_review(args) -> int:
    bad = _bad_run_id(args)
    if bad is not None:
        return bad

    def run(client: Client, out: Out) -> int:
        if args.stats:
            data = client.get("/api/studio/review/stats")
            rec = record("review_stats", ok=True, **data)
            if out.machine:
                out.json(rec)
            else:
                out.say(f"отзывов владельца: {data.get('owner_feedback')} · сравнимо: {data.get('compared')} · "
                        f"совпало: {data.get('agreed')} · пропущен брак: {data.get('missed_garbage')} · "
                        f"ложная тревога: {data.get('false_alarm')}")
                for kind, title in (("bad", "брак"), ("good", "хорошо")):
                    for r in (data.get("rules") or {}).get(kind) or []:
                        out.say(f"  [{title}] {sanitize(r.get('text'))}  (run {r.get('id')})")
            return EXIT_OK
        if not args.run_id:
            out.say("нужен run: bossman review <run_id> [--run] или bossman review --stats")
            return EXIT_FAIL
        path = f"/api/studio/runs/{args.run_id}/review"
        data = client.post(path, timeout=960) if args.run else client.get(path)   # a local vision pass takes minutes
        review, feedback = data.get("review") or {}, data.get("feedback") or {}
        verdict = review.get("verdict") or "NOT_REVIEWED"
        rec = record("review", ok=verdict in ("GOOD", "BAD"), run_id=args.run_id, verdict=verdict,
                     score=review.get("score"), defects=review.get("defects") or [],
                     summary=sanitize(review.get("summary") or review.get("reason") or ""),
                     owner=feedback.get("verdict"), owner_reason=sanitize(feedback.get("reason") or ""),
                     exit_code=EXIT_OK if verdict in ("GOOD", "BAD") else EXIT_FAIL)
        if out.machine:
            out.json(rec)
        else:
            out.say(f"run {args.run_id} · Bossman Vision: {verdict}"
                    + (f" {rec['score']}/10" if rec.get("score") else "")
                    + (f" · {rec['summary']}" if rec.get("summary") else ""))
            # record() drops empty fields, so every optional one is read with .get()
            for d in rec.get("defects") or []:
                out.say("  - " + sanitize(d))
            if rec.get("owner"):
                out.say(f"владелец: {rec['owner']}" + (f" — {rec['owner_reason']}" if rec.get("owner_reason") else ""))
        return rec["exit_code"]
    return _simple(args, run, what="review")


def cmd_rate(args) -> int:
    bad = _bad_run_id(args)
    if bad is not None:
        return bad

    def run(client: Client, out: Out) -> int:
        reason = " ".join(args.reason).strip()
        data = client.post(f"/api/studio/runs/{args.run_id}/feedback", {"verdict": args.verdict, "reason": reason})
        fb = data.get("feedback") or {}
        rec = record("rate", ok=True, run_id=args.run_id, verdict=fb.get("verdict"), reason=sanitize(fb.get("reason") or ""),
                     vision_verdict=fb.get("vision_verdict"), agreed_with_vision=fb.get("agreed_with_vision"),
                     learned_rule=bool(fb.get("reason")))
        if out.machine:
            out.json(rec)
        else:
            out.say(f"записано: {rec.get('verdict')}"
                    + (f" — правило «{rec.get('reason')}» теперь применяется к каждому новому видео"
                       if rec.get("learned_rule") else ""))
            if rec.get("agreed_with_vision") is False:
                out.say(f"Bossman Vision думал иначе ({rec.get('vision_verdict')}) — это расхождение учтено в статистике")
        return EXIT_OK
    return _simple(args, run, what="rate")


def cmd_events(args) -> int:
    def run(client: Client, out: Out) -> int:
        from .records import normalize
        verbose = args.verbose
        if not args.follow:
            cursor = args.after
            status = None
            while True:
                page = client.get(f"/api/tasks/{args.task_id}/events",
                                  params={"after": cursor, "limit": 500})
                status = page.get("status")
                for ev in page.get("events") or []:
                    for rec in normalize(ev, verbose=verbose):
                        _print_rec(out, rec)
                cursor = page.get("cursor", cursor)
                if not page.get("more"):
                    break
            tail = record("cursor", task_id=args.task_id, cursor=cursor, status=status,
                          task_state=state_of(status))
            if out.machine:
                out.json(tail)
            else:
                out.say(f"(курсор {cursor}; статус {status})")
            return EXIT_OK
        follower = Follower(client, args.task_id, sink=lambda rec: _print_rec(out, rec),
                            options=FollowOptions(approval_mode="wait", verbose=verbose), after=args.after)
        try:
            res = follower.run()
        except KeyboardInterrupt:
            res = record("result", ok=True, task_state="INTERRUPTED", task_id=args.task_id,
                         cursor=follower.state.last_seq, note="наблюдение прервано; задача не тронута",
                         exit_code=EXIT_INTERRUPTED)
        _print_rec(out, res)
        return int(res.get("exit_code", EXIT_FAIL))
    return _simple(args, run, what="events")


def _print_rec(out: Out, rec: dict) -> None:
    if out.machine:
        out.json(rec)
        return
    t = rec.get("type")
    body = {k: v for k, v in rec.items() if k not in ("v", "type", "ts")}
    out.say(f"{t}: " + json.dumps(body, ensure_ascii=False, default=str)[:400])


def cmd_approve(args) -> int:
    return _decide(args, approve=True)


def cmd_deny(args) -> int:
    return _decide(args, approve=False)


def _decide(args, *, approve: bool) -> int:
    def run(client: Client, out: Out) -> int:
        if approve and os.environ.get("BOSSMAN_TERMINAL_NO_APPROVE", "").strip() in ("1", "true", "yes"):
            raise UsageError("одобрение из этого окна запрещено (BOSSMAN_TERMINAL_NO_APPROVE=1): "
                             "решение принимает владелец в вебе, Telegram или своём терминале")
        rows = client.get("/api/approvals", params={"status": "all"}) or []
        row = next((r for r in rows if r.get("id") == args.approval_id), None)
        if row is None:
            raise BossmanError(f"разрешение #{args.approval_id} не найдено", kind="not_found")
        if row.get("status") != "pending":
            raise BossmanError(f"разрешение #{args.approval_id} уже решено: {row.get('status')}",
                               kind="conflict")
        if approve and not getattr(args, "yes", False):
            if not (sys.stdin and sys.stdin.isatty() and sys.stderr and sys.stderr.isatty()):
                # stdin alone is not enough: on Windows a child inherits the console
                # stdin while its output is captured, and the prompt went unseen.
                raise UsageError("одобрение без терминала требует --yes: это решение владельца, "
                                 "а не автоматический шаг")
            sys.stderr.write(sanitize(row.get("preview") or "") + "\n")
            sys.stderr.write(f"Одобрить разрешение #{args.approval_id}? [y/N] ")
            sys.stderr.flush()
            if (sys.stdin.readline() or "").strip().lower() not in ("y", "yes", "д", "да"):
                out.say("не одобрено")
                return EXIT_CONFLICT
        decided = client.post(f"/api/approvals/{args.approval_id}",
                              {"approve": approve, "by": "owner:terminal"})
        status = (decided or {}).get("status")
        want = "approved" if approve else "rejected"
        rec = record("approval_decided", ok=status == want, approval_id=args.approval_id,
                     status=status, by="owner:terminal",
                     exit_code=EXIT_OK if status == want else EXIT_CONFLICT)
        if out.machine:
            out.json(rec)
        else:
            out.say(f"разрешение #{args.approval_id}: {status}")
        return rec["exit_code"]
    return _simple(args, run, what="approve" if approve else "deny")


def _task_action(args, action: str) -> int:
    def run(client: Client, out: Out) -> int:
        res = client.post(f"/api/tasks/{args.task_id}/{action}")
        rec = record("task_action", ok=bool((res or {}).get("ok", True)), action=action,
                     task_id=args.task_id, status=(res or {}).get("status"), exit_code=EXIT_OK)
        if out.machine:
            out.json(rec)
        else:
            out.say(f"задача {args.task_id}: {action} → {(res or {}).get('status')}")
        return EXIT_OK
    return _simple(args, run, what=action)


def cmd_pause(args) -> int:
    return _task_action(args, "pause")


def cmd_continue(args) -> int:
    return _task_action(args, "resume")


def cmd_stop(args) -> int:
    if args.all:
        return _simple(args, lambda c, o: global_stop(c, o), what="stop-all")
    if args.task_id is None:
        return fail(Out(args.output_format), UsageError("нужен id задачи или --all"), what="stop")
    return _task_action(args, "stop")


def global_stop(client: Client, out: Out) -> int:
    """Global STOP: every active task, the computer-control STOP flag, running
    coding tasks. Each result is the backend's answer, reported one by one."""
    stopped, errors = [], []
    tasks = client.get("/api/tasks", params={"status": "queued,running,waiting_approval,paused",
                                             "limit": 500}) or []
    for t in tasks:
        try:
            client.post(f"/api/tasks/{t['id']}/stop")
            stopped.append(t["id"])
        except BossmanError as exc:
            errors.append({"task_id": t["id"], "error": exc.message})
    computer = None
    try:
        computer = client.post("/api/computer/stop")
    except BossmanError as exc:
        errors.append({"computer": exc.message})
    coding = []
    try:
        for item in (client.get("/api/coding-tasks") or {}).get("items", []):
            if item.get("status") == "running":
                client.post(f"/api/coding-tasks/{item['id']}/cancel")
                coding.append(item["id"])
    except BossmanError as exc:
        if exc.kind != "not_supported":
            errors.append({"coding": exc.message})
    rec = record("stop_all", ok=not errors, stopped_tasks=stopped, computer_stopped=bool(
        (computer or {}).get("stopped")), cancelled_coding_tasks=coding, errors=errors or None,
        exit_code=EXIT_OK if not errors else EXIT_FAIL)
    if out.machine:
        out.json(rec)
    else:
        out.say(f"STOP: задач {len(stopped)}, компьютер {'остановлен' if rec['computer_stopped'] else '—'},"
                f" coding {len(coding)}" + (f"; ошибки: {len(errors)}" if errors else ""))
    return rec["exit_code"]


def cmd_list(args) -> int:
    def run(client: Client, out: Out) -> int:
        items = list_items(client, args.what, limit=args.limit)
        if out.machine:
            out.json(record("list", ok=True, what=args.what, items=items, exit_code=EXIT_OK))
        else:
            for item in items:
                out.say("  " + " · ".join(f"{sanitize(v)}" for v in item.values() if v not in (None, "", [])))
            if not items:
                out.say("  (пусто)")
        return EXIT_OK
    return _simple(args, run, what="list")


cmd_tasks = cmd_list
cmd_approvals = cmd_list


def list_items(client: Client, what: str, *, limit: int = 20) -> list[dict]:
    if what == "models":
        cat = Catalog.load(client)
        from .ops import locality
        from .records import model_kind
        return [{"id": m.get("id"), "alias": sanitize(m.get("alias")), "name": sanitize(m.get("name")),
                 "locality": locality(cat.provider(m.get("provider_id")), m),
                 "context_window": m.get("context_window"), "model_kind": model_kind(m.get("name"))}
                for m in cat.models]
    if what == "agents":
        cat = Catalog.load(client)
        return [{"id": a.get("id"), "name": sanitize(a.get("name")), "enabled": a.get("enabled"),
                 "model": (cat.describe_agent(a).get("model") or {}).get("alias"),
                 "tools": [sanitize(t) for t in (a.get("tools") or []) if isinstance(t, str)]}
                for a in cat.agents]
    if what == "skills":
        items = []
        try:
            for s in client.get("/api/skills") or []:
                items.append({"id": sanitize(s.get("id")), "title": sanitize(s.get("title") or s.get("name")),
                              "status": s.get("status"), "source": "library"})
        except BossmanError:
            pass
        try:
            for s in client.get("/api/skill-catalog") or []:
                items.append({"id": sanitize(s.get("id")), "title": sanitize(s.get("title")),
                              "status": s.get("status"), "source": sanitize(s.get("source")) or "catalog"})
        except BossmanError:
            pass
        return items
    if what == "tools":
        caps = client.get("/api/capabilities") or {}
        return [{"tool": sanitize(c.get("tool")), "effect": (c.get("grant") or {}).get("effect")
                 or c.get("default_effect"), "granted": (c.get("grant") or {}).get("granted"),
                 "reason": sanitize((c.get("grant") or {}).get("reason"))[:120] or None}
                for c in caps.get("capabilities") or []]
    if what == "tasks":
        rows = client.get("/api/tasks", params={"limit": limit}) or []
        return [{"id": t.get("id"), "status": t.get("status"), "task_state": state_of(t.get("status")),
                 "title": sanitize(t.get("title"))[:100]} for t in rows]
    if what == "approvals":
        rows = client.get("/api/approvals", params={"status": "pending"}) or []
        return [{"id": a.get("id"), "task_id": a.get("task_id"), "kind": a.get("kind"),
                 "preview": sanitize(a.get("preview"), keep_newlines=False)[:160]} for a in rows]
    raise UsageError(f"неизвестный список: {what}")


# ----------------------------------------------------------------- code / evolution


def cmd_code(args) -> int:
    def run(client: Client, out: Out) -> int:
        if args.instruction == "apply" and args.task_id:
            from .screens import apply_coding_task
            return apply_coding_task(client, out, args.task_id, args.approval_id)
        if args.task_id:
            raise UsageError("лишний аргумент: инструкцию берите в кавычки; применить — `code apply <id>`")
        from .screens import run_coding_task
        return run_coding_task(client, out, instruction=args.instruction, allow=args.allow,
                               protect=args.protect, verify=args.verify,
                               repo=os.path.abspath(args.repo or os.getcwd()), agent=args.agent,
                               timeout=args.timeout, use_memory=not args.no_memory)
    return _simple(args, run, what="code")


def cmd_evolution(args) -> int:
    def run(client: Client, out: Out) -> int:
        from .screens import evolution_call
        return evolution_call(client, out, args.action)
    return _simple(args, run, what="evolution")


def cmd_repair(args) -> int:
    def run(client: Client, out: Out) -> int:
        from .screens import run_repair
        return run_repair(client, out, model=args.model, max_seconds=args.max_seconds,
                          plain=args.plain)
    return _simple(args, run, what="repair")


def cmd_run(args) -> int:
    def run(client: Client, out: Out) -> int:
        from .screens import run_automation
        return run_automation(client, out, args)
    return _simple(args, run, what="run")


def cmd_evolve(args) -> int:
    def run(client: Client, out: Out) -> int:
        from .screens import run_lab
        return run_lab(client, out, args)
    return _simple(args, run, what="evolve")


def cmd_keys(args) -> int:
    from .keys import run_keys
    out = Out(args.output_format)
    if args.forbidden_value:
        # The key must never travel in argv (shell history, process list).
        return fail(out, UsageError("ключ в командной строке не принимаю: он остаётся в истории "
                                    "оболочки и списке процессов. Используйте `bossman keys set "
                                    "<vendor>` (скрытый ввод) или --stdin"), what="keys")
    try:
        client = connect(args)
    except BossmanError as exc:
        return fail(out, exc, what="connect")
    with client:
        try:
            return run_keys(client, out, args)
        except (BossmanError, UsageError) as exc:
            return fail(out, exc, what="keys")


def cmd_resume(args) -> int:
    if args.task is not None:
        args.task_id = args.task
        return _task_action(args, "resume")
    if not args.session_id:
        return fail(Out(args.output_format), UsageError("нужен id сессии (или --task <id>)"), what="resume")
    from .chat import run_chat
    args.session = args.session_id
    args.agent = None
    args.cwd = None
    args.verbose = False
    args.no_history = False
    return run_chat(args)


def cmd_start(args) -> int:
    out = Out(args.output_format)
    from .launch import start_backend
    try:
        info = start_backend(url=args.url, data_dir=args.data_dir, port=args.port)
    except BossmanError as exc:
        return fail(out, exc, what="start")
    rec = record("started", ok=True, **info, exit_code=EXIT_OK)
    if out.machine:
        out.json(rec)
    else:
        out.say(("Bossman уже работает: " if info.get("already_running") else "Bossman запущен: ")
                + str(info.get("url")))
    return EXIT_OK


__all__ = ["main", "build_parser", "TERMINAL_COMMANDS", "load_exec_input", "run_headless"]
