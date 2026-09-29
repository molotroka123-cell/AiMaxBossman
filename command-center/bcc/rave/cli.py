"""`bossman rave …` — Agentic Rave in Bossman CMD (thin client of /api/rave).

    bossman rave "<prompt>" --agents mock:a,mock:b,local:qwen [--repo P] [--allow P]... [--test CMD] [--detach]
    bossman rave list | connectors
    bossman rave status <id> [--watch]
    bossman rave show <id> <agent> | diff <id> <agent> | conflicts <id> | log <id>
    bossman rave pause|resume|stop <id> [--agent X]      bossman rave stop --all
    bossman rave apply <id> <agent> [--approval-id N]

Same backend, same data root, same approvals as the web UI and Telegram; the
terminal keeps no state. `--json` prints machine records.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from typing import Any

from ..terminal_cli.api_client import BossmanError, Client
from ..terminal_cli.console import sanitize
from ..terminal_cli.records import (EXIT_CONFLICT, EXIT_FAIL, EXIT_INTERRUPTED, EXIT_OK, EXIT_USAGE)

ACTIONS = ("start", "list", "status", "show", "diff", "conflicts", "log", "pause", "resume", "stop",
           "apply", "connectors")
TERMINAL = ("done", "failed", "stopped", "blocked", "interrupted")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bossman rave", description="Agentic Rave: один prompt — несколько агентов, "
                                "каждый в своей изолированной копии проекта.")
    p.add_argument("action", nargs="?", default="start", help=" | ".join(ACTIONS) + " (или сразу prompt)")
    p.add_argument("args", nargs="*")
    p.add_argument("--agents", action="append", default=[], help="список: mock:a,mock:b,local:qwen,claude:c,codex:x")
    p.add_argument("--agent", help="один агент (pause/resume/stop) или ещё один агент для запуска")
    p.add_argument("--repo", help="git-проект в разрешённых корнях (без него — чистый scratch-проект)")
    p.add_argument("--allow", action="append", default=[], help="разрешённые пути для local-агента")
    p.add_argument("--test", help="команда проверки в каждой готовой копии (argv, без shell)")
    p.add_argument("--detach", action="store_true", help="запустить и сразу вернуть id")
    p.add_argument("--watch", action="store_true", help="status: следить до конца")
    p.add_argument("--all", action="store_true", help="stop: остановить все рейвы")
    p.add_argument("--approval-id", type=int)
    p.add_argument("--interval", type=float, default=1.0)
    p.add_argument("--json", action="store_true", help="машинный вывод")
    p.add_argument("--url")
    p.add_argument("--data-dir")
    return p


class Printer:
    def __init__(self, as_json: bool):
        self.as_json = as_json

    def say(self, text: str = "") -> None:
        stream = sys.stderr if self.as_json else sys.stdout
        stream.write(sanitize(text) + "\n")
        stream.flush()

    def record(self, obj: dict) -> None:
        if self.as_json:
            sys.stdout.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
            sys.stdout.flush()


def main(argv: list[str]) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    from ..terminal_cli.api_client import discover
    pr = Printer(args.json)
    try:
        client = Client(discover(args.url, args.data_dir))
    except BossmanError as exc:
        pr.say(f"bossman rave: {exc.message}" + (f"\n  подсказка: {exc.hint}" if exc.hint else ""))
        return 5
    with client:
        return run(client, args, pr)


def chat_command(client: Client, words: list[str], say) -> int:
    """`/rave …` inside `bossman chat` — the same commands on the chat's connection."""
    try:
        args = build_parser().parse_args(words)
    except SystemExit:
        return EXIT_USAGE
    pr = Printer(False)
    pr.say = lambda text="": say(sanitize(text))  # type: ignore[method-assign]
    if args.action in ("start",) or args.action not in ACTIONS:
        args.detach = True                        # chat stays interactive: watch with /rave status <id>
    return run(client, args, pr)


def run(client: Client, args, pr: Printer) -> int:
    action, rest = args.action, list(args.args)
    if action not in ACTIONS:                    # `bossman rave "<prompt>" …`
        rest.insert(0, action)
        action = "start"
    try:
        return DISPATCH[action](client, args, rest, pr)
    except BossmanError as exc:
        pr.record({"type": "error", "ok": False, "error": exc.message, "kind": exc.kind, "code": exc.code})
        pr.say(f"bossman rave: {exc.message}" + (f"\n  подсказка: {exc.hint}" if exc.hint else ""))
        return EXIT_CONFLICT if exc.kind == "conflict" else EXIT_FAIL
    except _Usage as exc:
        pr.say(f"bossman rave: {exc}")
        return EXIT_USAGE


class _Usage(Exception):
    pass


def _need(rest: list[str], n: int, what: str) -> list[str]:
    if len(rest) < n:
        raise _Usage(f"нужно: {what}")
    return rest[:n]


# ------------------------------------------------------------------ rendering


def _cut(text: Any, n: int) -> str:
    s = sanitize(str(text if text is not None else "")).replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _ts(value: float | None) -> str:
    return datetime.fromtimestamp(value).strftime("%H:%M:%S") if value else "--:--:--"


def render(rave: dict, pr: Printer) -> None:
    repo = "scratch-проект" if rave.get("scratch") else rave.get("repo")
    pr.say(f"Рейв {rave['id']} · {rave['status'].upper()} · base {str(rave.get('base_commit'))[:10]} · {repo}")
    pr.say(f"prompt: {_cut(rave.get('prompt'), 110)}")
    head = f"{'АГЕНТ':<8} {'ПРОВАЙДЕР / МОДЕЛЬ':<40} {'ВХОД':<27} {'СТАТУС':<11} {'ШАГ':<6} {'ФАЙЛЫ':<5} {'ТЕСТЫ':<6} ОТВЕТ / ОШИБКА"
    pr.say(head)
    pr.say("-" * len(head))
    for a in rave["agents"]:
        prov = f"{a.get('provider') or '-'} / {a.get('model') or '-'}"
        step = f"{a.get('step') or 0}/{a.get('steps_total') or '?'}"
        tests = a.get("tests") or {}
        t = "-" if not tests else ("PASS" if tests.get("passed") else "FAIL")
        status = a["status"] + ("*" if a.get("pause_reason") == "recovered_after_restart" else "")
        text = a.get("error") if a["status"] in ("failed", "blocked", "interrupted", "stopped") and a.get("error") \
            else (a.get("answer") or a.get("step_label") or "")
        pr.say(f"{a['name']:<8} {_cut(prov, 40):<40} {_cut(a.get('auth') or '-', 27):<27} {status:<11} {step:<6} "
               f"{len(a.get('changed_files') or []):<5} {t:<6} {_cut(text, 70)}")
        if a["status"] in ("blocked", "failed", "interrupted") and len(str(a.get("error") or "")) > 70:
            # what the owner has to do (login step, approval id) must not be cut off
            pr.say(f"         ↳ {_cut(a['error'], 600)}")
    if any(a.get("pause_reason") == "recovered_after_restart" for a in rave["agents"]):
        pr.say("* пауза после перезапуска Bossman: состояние восстановлено; продолжить — "
               f"bossman rave resume {rave['id']}")
    conflicts = [c for c in rave.get("conflicts") or [] if c.get("file")]
    if conflicts:
        pr.say(f"КОНФЛИКТЫ ({len(conflicts)}) — ничего не потеряно, все версии сохранены:")
        for c in conflicts:
            merge = "сливается автоматически" if c.get("auto_mergeable") else \
                f"нужно решение ({c.get('conflict_hunks')} участ.)"
            pr.say(f"  {c['file']}: {' × '.join(c.get('agents') or [])} · {c.get('kind')} · {merge}")
            pr.say(f"    версии: {c.get('artifacts')}")
    if rave.get("applied"):
        for name, info in rave["applied"].items():
            pr.say(f"применён в проект: {name} (разрешение #{info.get('approval_id')}) · "
                   f"{', '.join(info.get('files') or [])}")


def _event_line(ev: dict) -> str:
    kind = ev.get("kind")
    who = ev.get("agent") or "рейв"
    extra = {k: v for k, v in ev.items() if k not in ("seq", "ts", "kind", "agent")}
    if kind == "step_started":
        detail = f"шаг {extra.get('step')}/{extra.get('total')} · {extra.get('label')}"
    elif kind in ("agent_failed", "agent_blocked", "agent_stopped", "agent_interrupted"):
        detail = str(extra.get("error") or "")
    else:
        detail = ", ".join(f"{k}={v}" for k, v in extra.items() if v not in (None, "", [], {}))
    return f"[{_ts(ev.get('ts'))}] {who:<6} {kind:<18} {_cut(detail, 100)}"


def watch(client: Client, rid: str, pr: Printer, *, interval: float = 1.0, after: int = 0) -> int:
    cursor = after
    try:
        while True:
            page = client.get(f"/api/rave/{rid}/events", params={"after": cursor})
            for ev in page.get("events") or []:
                if ev.get("kind") in ("step_done",):
                    continue
                pr.record({"type": "rave_event", **ev})
                if not pr.as_json:
                    pr.say(_event_line(ev))
            cursor = page.get("cursor", cursor)
            rave = client.get(f"/api/rave/{rid}")
            if rave["status"] not in ("running",):
                break
            time.sleep(max(0.2, interval))
    except KeyboardInterrupt:
        pr.say(f"(наблюдение прервано; агенты продолжают работу — bossman rave status {rid})")
        return EXIT_INTERRUPTED
    pr.say("")
    return _final(rave, pr)


def _final(rave: dict, pr: Printer) -> int:
    pr.record({"type": "rave", **rave})
    if not pr.as_json:
        render(rave, pr)
    # FAIL only when nothing useful came out: a partial rave with at least one
    # finished agent is a result the owner can compare and apply.
    if rave["status"] == "partial" and not any(a["status"] == "done" for a in rave["agents"]):
        return EXIT_FAIL
    return EXIT_OK


# ------------------------------------------------------------------ actions


def a_start(client: Client, args, rest: list[str], pr: Printer) -> int:
    prompt = " ".join(rest).strip()
    if not prompt:
        raise _Usage('нужен prompt: bossman rave "<задача>" --agents mock:a,mock:b')
    agents = list(args.agents) + ([args.agent] if args.agent else [])
    if not agents:
        raise _Usage("нужны агенты: --agents mock:a,mock:b[,local:qwen,claude:c,codex:x]")
    body = {"prompt": prompt, "agents": agents, "repo": args.repo, "allow": args.allow, "test": args.test}
    rave = client.post("/api/rave", body, timeout=300)
    pr.record({"type": "rave_started", "id": rave["id"], "agents": [a["name"] for a in rave["agents"]]})
    pr.say(f"Рейв {rave['id']} запущен: {len(rave['agents'])} агент(ов), каждый в своей копии проекта "
           f"(base {rave['base_commit'][:10]}).")
    for a in rave["agents"]:
        pr.say(f"  {a['name']}: {a.get('provider')} / {a.get('model')} · вход: {a.get('auth')} · копия {a['workspace']}")
    if args.detach:
        pr.say(f"следить: bossman rave status {rave['id']} --watch")
        return EXIT_OK
    pr.say("(Ctrl+C — перестать следить; агенты продолжат работу)")
    return watch(client, rave["id"], pr, interval=args.interval)


def a_list(client: Client, args, rest, pr: Printer) -> int:
    items = client.get("/api/rave").get("items") or []
    pr.record({"type": "rave_list", "items": items})
    if not items:
        pr.say("рейвов нет")
    for it in items:
        agents = ", ".join(f"{k}:{v}" for k, v in it["agents"].items())
        pr.say(f"{it['id']} · {it['status']:<8} · {_ts(it['created_at'])} · {_cut(it['prompt'], 50)} · {agents}"
               + (f" · конфликтов {it['conflicts']}" if it.get("conflicts") else ""))
    return EXIT_OK


def a_status(client: Client, args, rest, pr: Printer) -> int:
    (rid,) = _need(rest, 1, "bossman rave status <id>")
    if args.watch:
        return watch(client, rid, pr, interval=args.interval,
                     after=int(client.get(f"/api/rave/{rid}/events").get("cursor") or 0))
    return _final(client.get(f"/api/rave/{rid}"), pr)


def a_show(client: Client, args, rest, pr: Printer) -> int:
    rid, name = _need(rest, 2, "bossman rave show <id> <agent>")
    rave = client.get(f"/api/rave/{rid}")
    a = next((x for x in rave["agents"] if x["name"] == name), None)
    if a is None:
        raise _Usage(f"в рейве {rid} нет агента {name}")
    pr.record({"type": "rave_agent", "rave": rid, **a})
    pr.say(f"Агент {name} · {a['status']} · {a.get('provider')} / {a.get('model')} · вход: {a.get('auth')}")
    pr.say(f"копия: {a['workspace']} · ветка {a['branch']} · результат {a.get('result_commit') or '-'}")
    pr.say(f"шаг {a.get('step')}/{a.get('steps_total')} · журнал {a.get('journal')}")
    if a.get("answer"):
        pr.say("ОТВЕТ:\n" + str(a["answer"]))
    if a.get("error"):
        pr.say("ОШИБКА: " + str(a["error"]))
    files = a.get("changed_files") or []
    pr.say(f"изменённые файлы ({len(files)}): " + (", ".join(f"{f['status']} {f['path']}" for f in files) or "-"))
    if a.get("diff_stat"):
        pr.say(a["diff_stat"])
    if a.get("tests"):
        t = a["tests"]
        pr.say(f"ТЕСТЫ `{t.get('command')}`: {'PASS' if t.get('passed') else 'FAIL'} (exit {t.get('exit_code')}"
               f"{', timeout' if t.get('timed_out') else ''})")
        pr.say(str(t.get("output_tail") or "")[-1500:])
    if a.get("meta"):
        pr.say("meta: " + json.dumps(a["meta"], ensure_ascii=False, default=str)[:600])
    return EXIT_OK


def a_diff(client: Client, args, rest, pr: Printer) -> int:
    rid, name = _need(rest, 2, "bossman rave diff <id> <agent>")
    d = client.get(f"/api/rave/{rid}/agents/{name}/diff")
    pr.record({"type": "rave_diff", "rave": rid, **d})
    if not pr.as_json:
        pr.say(f"diff агента {name} ({d.get('status')}) против base {str(d.get('base') or '')[:10]}:"
               + (f"  [{d['note']}]" if d.get("note") else ""))
        pr.say(d.get("stat") or "(нет изменений)")
        if d.get("patch"):
            pr.say(d["patch"].rstrip("\n"))
    return EXIT_OK


def a_conflicts(client: Client, args, rest, pr: Printer) -> int:
    (rid,) = _need(rest, 1, "bossman rave conflicts <id>")
    data = client.get(f"/api/rave/{rid}/conflicts")
    pr.record({"type": "rave_conflicts", **data})
    items = [c for c in data.get("conflicts") or [] if c.get("file")]
    if not items:
        pr.say("конфликтов нет")
    for c in items:
        pr.say(f"{c['file']}: {' × '.join(c['agents'])} · {c['kind']} · "
               + ("сливается автоматически" if c.get("auto_mergeable") else f"{c.get('conflict_hunks')} конфл. участок(ов)"))
        pr.say(f"  все версии (base / каждый агент / merged с маркерами): {c.get('artifacts')}")
    return EXIT_OK


def a_log(client: Client, args, rest, pr: Printer) -> int:
    (rid,) = _need(rest, 1, "bossman rave log <id>")
    page = client.get(f"/api/rave/{rid}/events")
    for ev in page.get("events") or []:
        pr.record({"type": "rave_event", **ev})
        if not pr.as_json:
            pr.say(_event_line(ev))
    return EXIT_OK


def _control(client: Client, args, rest, pr: Printer, what: str) -> int:
    if what == "stop" and args.all:
        res = client.post("/api/rave/stop-all")
        pr.record({"type": "rave_stop_all", **res})
        stopped = res.get("stopped") or {}
        pr.say("STOP всех рейвов: " + (", ".join(f"{k} ({', '.join(v)})" for k, v in stopped.items()) or
                                         "активных рейвов не было"))
        return EXIT_OK
    (rid,) = _need(rest, 1, f"bossman rave {what} <id> [--agent X]")
    res = client.post(f"/api/rave/{rid}/{what}", {"agent": args.agent})
    pr.record({"type": f"rave_{what}", "id": rid, "agent": args.agent, "changed": res.get("changed")})
    label = {"pause": "пауза", "resume": "продолжение", "stop": "STOP"}[what]
    who = f"агент {args.agent}" if args.agent else "все агенты"
    pr.say(f"{label}: {who} → затронуты: {', '.join(res.get('changed') or []) or 'никто (уже в конечном состоянии)'}")
    if not pr.as_json:
        render(res["rave"], pr)
    return EXIT_OK


def a_apply(client: Client, args, rest, pr: Printer) -> int:
    rid, name = _need(rest, 2, "bossman rave apply <id> <agent> [--approval-id N]")
    try:
        res = client.post(f"/api/rave/{rid}/agents/{name}/apply", {"approval_id": args.approval_id})
    except BossmanError as exc:
        if exc.kind == "conflict":
            pr.say(f"КОНФЛИКТ: {exc.message}")
            pr.say("проект не изменён; версии агентов — в их копиях и в `bossman rave conflicts " + rid + "`")
            return EXIT_CONFLICT
        raise
    pr.record({"type": "rave_apply", **res})
    if res.get("state") == "WAIT_APPROVAL":
        aid = res.get("approval_id")
        pr.say(f"Нужно решение владельца: разрешение #{aid}\n{res.get('preview')}")
        pr.say(f"одобрить: bossman approve {aid}   затем: bossman rave apply {rid} {name} --approval-id {aid}")
        return EXIT_OK
    pr.say(f"ПРИМЕНЕНО в проект (рабочее дерево, без commit): агент {name} · разрешение #{res.get('approval_id')} · "
           f"файлы: {', '.join(res.get('files') or [])}")
    return EXIT_OK


def a_connectors(client: Client, args, rest, pr: Printer) -> int:
    data = client.get("/api/rave/connectors", timeout=120)
    pr.record({"type": "rave_connectors", **data})
    for key in ("mock", "local", "claude", "codex"):
        c = data.get(key) or {}
        if key == "mock":
            pr.say("mock   · готов · вход: none (скриптовый агент, не модель)")
        elif key == "local":
            pr.say(f"local  · {c.get('endpoint')} · модель по умолчанию {c.get('default_model')} · вход: local")
        else:
            state = "готов" if c.get("logged_in") and c.get("subscription") else "НЕ ГОТОВ"
            pr.say(f"{key:<6} · {state} · вход: {c.get('auth')}"
                   + (f" · план {c.get('plan')}" if c.get("plan") else "")
                   + (" · opt-in владельца: есть" if c.get("optin") else " · opt-in владельца: нет (попросит при запуске)"))
            if not (c.get("logged_in") and c.get("subscription")):
                pr.say(f"         как войти: {c.get('login_step')}")
            pr.say(f"         источники: {', '.join(c.get('sources') or [])}")
    api = data.get("api_key_path") or {}
    pr.say(f"путь по API-ключу: {'ВКЛЮЧЁН' if api.get('enabled') else 'выключен'} ({api.get('how')})")
    return EXIT_OK


DISPATCH = {"start": a_start, "list": a_list, "status": a_status, "show": a_show, "diff": a_diff,
            "conflicts": a_conflicts, "log": a_log, "apply": a_apply, "connectors": a_connectors,
            "pause": lambda c, a, r, p: _control(c, a, r, p, "pause"),
            "resume": lambda c, a, r, p: _control(c, a, r, p, "resume"),
            "stop": lambda c, a, r, p: _control(c, a, r, p, "stop")}
