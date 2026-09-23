"""`bossman chat` — the owner's conversation with the SAME Bossman, in cmd.

One scrollable column (header with the status cells, "You ›" / "Bossman ›",
collapsed tool events, results), input with prompt_toolkit when available:
Enter sends, Tab completes commands and paths, history survives streamed
output, a multi-line paste is ONE message (never executed line by line).

Ctrl+C: at the prompt — clears the line (twice on an empty prompt — exit);
during a task — asks Bossman to cancel it and waits for Bossman's
confirmation; a second Ctrl+C detaches (the task continues or stops in the
backend; `bossman status` shows it). Leaving the chat detaches: tasks keep
running in Bossman — said before exit.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.text import Text

from .. import conversation_context
from . import CLIENT_VERSION, slash
from .api_client import BossmanError, Client, discover
from .console import color_allowed, make_console, sanitize, utf8_console
from .follow import Follower, FollowOptions
from .human import (View, human_duration, human_tokens, money, render_status_bar, render_title,
                    status_cells)
from .ops import MAX_PROMPT_CHARS, Catalog, create_draft, new_request_id, start_run
from .records import EXIT_DISCONNECTED, EXIT_OK, model_kind, state_of
from .theme import glyphs

CONTEXT_TURNS = 3
CONTEXT_CHARS = 1500


# ----------------------------------------------------------------- history

_LONG_TOKEN = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")


def filter_history_line(line: str) -> str:
    """What may be written to the input history: known secret shapes and any
    long high-entropy word are replaced. Applied before anything is stored."""
    from ..plugin_security import redact_text
    return _LONG_TOKEN.sub("***", redact_text(line))


def history_enabled(args) -> bool:
    if getattr(args, "no_history", False):
        return False
    return os.environ.get("BOSSMAN_TERMINAL_HISTORY", "1").strip() not in ("0", "false", "no")


def _make_history(path: Path, enabled: bool):
    try:
        from prompt_toolkit.history import FileHistory, InMemoryHistory
    except ImportError:
        return None
    if not enabled:
        return InMemoryHistory()

    class FilteredHistory(FileHistory):
        def store_string(self, string: str) -> None:        # noqa: D401 — prompt_toolkit API
            super().store_string(filter_history_line(string))

    path.parent.mkdir(parents=True, exist_ok=True)
    return FilteredHistory(str(path))


# ----------------------------------------------------------------- session


@dataclass
class Session:
    id: str
    path: Path
    turns: list[dict] = field(default_factory=list)
    #: /compact: summary of turns[:compacted_at_turn] written by a Bossman task
    summary: str = ""
    compacted_at_turn: int = 0
    compact_tasks: list[int] = field(default_factory=list)

    @classmethod
    def open(cls, data_dir: Path, session_id: str | None) -> "Session":
        root = Path(data_dir) / "terminal" / "sessions"
        root.mkdir(parents=True, exist_ok=True)
        if session_id:
            if not re.fullmatch(r"[A-Za-z0-9_-]{4,64}", session_id):
                raise BossmanError("неверный id сессии", kind="usage")
            path = root / f"{session_id}.json"
            if not path.is_file():
                raise BossmanError(f"сессия {session_id} не найдена", kind="not_found",
                                   hint=f"сессии: {root}")
            data = json.loads(path.read_text(encoding="utf-8"))
            turns = list(data.get("turns") or [])
            # Sessions written before /compact have no summary fields.
            at = data.get("compacted_at_turn")
            at = at if isinstance(at, int) and 0 <= at <= len(turns) else 0
            return cls(id=session_id, path=path, turns=turns, summary=str(data.get("summary") or ""),
                       compacted_at_turn=at,
                       compact_tasks=[t for t in data.get("compact_tasks") or [] if isinstance(t, int)])
        sid = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        return cls(id=sid, path=root / f"{sid}.json")

    def add(self, task_id: int, text: str) -> None:
        # Only a pointer to the backend task + the owner's (secret-filtered)
        # words: the answer itself is read back from Bossman, not stored here.
        self.turns.append({"task_id": task_id, "text": filter_history_line(text)[:4000],
                           "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        self.save()

    def live_turns(self) -> list[dict]:
        """Turns after the compaction point (all of them without /compact)."""
        return self.turns[self.compacted_at_turn:]

    def compacted(self, summary: str, task_id: int) -> None:
        self.summary = summary
        self.compacted_at_turn = len(self.turns)
        self.compact_tasks.append(task_id)
        self.save()

    def save(self) -> None:
        data: dict[str, Any] = {"id": self.id, "turns": self.turns}
        if self.summary or self.compact_tasks:
            data.update(summary=self.summary, compacted_at_turn=self.compacted_at_turn,
                        compact_tasks=self.compact_tasks)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)


SUMMARY_LABEL = "Краткое содержание беседы до этого места (/compact):\n"


def _turn_part(client: Client, turn: dict) -> str | None:
    try:
        data = client.get(f"/api/tasks/{turn['task_id']}")
    except BossmanError:
        return None
    answer = sanitize(data.get("result") or data.get("error") or "")[:CONTEXT_CHARS]
    return f"Владелец: {sanitize(turn.get('text'))[:CONTEXT_CHARS]}\nBossman: {answer or '—'}"


def context_parts(client: Client, session: Session) -> list[str]:
    """The /compact summary (if any), then the last turns after the compaction
    point, read back from Bossman (the truth)."""
    parts = [SUMMARY_LABEL + session.summary] if session.summary else []
    for turn in session.live_turns()[-CONTEXT_TURNS:]:
        part = _turn_part(client, turn)
        if part is not None:
            parts.append(part)
    return parts


def context_preamble(client: Client, session: Session) -> str:
    """What a follow-up message carries as its context. Empty for the first message."""
    return conversation_context.compose(context_parts(client, session))


def approx_tokens(chars: int) -> int:
    """A rough estimate (chars/4), always labelled as an estimate where shown."""
    return (chars + 3) // 4


COMPACT_REQUEST = ("Составь краткое резюме беседы выше, чтобы по нему можно было продолжить разговор: "
                   "цели владельца, ключевые факты, имена и числа, принятые решения, открытые вопросы. "
                   "Ничего не выполняй и не вызывай инструменты — ответь только текстом резюме, "
                   "не длиннее {limit} символов.")
COMPACT_SUMMARY_CHARS = 2000


def compact_prompt(client: Client, session: Session, instruction: str = "") -> str:
    """Prompt of the /compact task: the previous summary and EVERY turn since the
    compaction point as conversation context (same format as a chat turn, so the
    backend checks see only the fixed request), then the summarisation request.

    The owner's /compact instruction shapes the summary; it is not an action, so it
    rides in the context part: «/compact запомни главное» must not turn the summary
    task into a memory action with an approval."""
    parts = [SUMMARY_LABEL + session.summary] if session.summary else []
    for turn in session.live_turns():
        part = _turn_part(client, turn)
        if part is not None:
            parts.append(part)
    hint = ["Указание владельца к резюме (/compact): " + sanitize(instruction.strip())[:500]] \
        if instruction.strip() else []
    request = COMPACT_REQUEST.format(limit=COMPACT_SUMMARY_CHARS)
    budget = MAX_PROMPT_CHARS - len(request) - 1000
    while len(parts) > 1 and len(conversation_context.compose(parts + hint)) > budget:
        parts.pop(1 if session.summary else 0)        # the oldest turn goes first
    return conversation_context.compose(parts + hint) + request


# ----------------------------------------------------------------- policy view


def tool_effects(agent: dict | None, caps: list[dict]) -> list[tuple[str, str]]:
    """[(tool, never|on-demand|allowed)] for the agent's own tools, by the same
    rule the engine applies (default effect; a granted permission lifts ask)."""
    if not agent:
        return []
    by_tool = {c.get("tool"): c for c in caps}
    perms = agent.get("permissions") or {}
    granted = set(perms) if isinstance(perms, list) else {k for k, v in perms.items() if v}
    out = []
    for name in [str(t) for t in (agent.get("tools") or []) if isinstance(t, str)]:
        cap = by_tool.get(name) or {}
        effect = str(cap.get("approval_requirement") or "")
        perm = cap.get("permission")
        if effect != "deny" and perm and perm in granted:
            effect = "auto"
        human = {"deny": "never", "ask": "on-demand", "auto": "allowed"}.get(effect, "—")
        out.append((name, human))
    return out


def approvals_cell(effects: list[tuple[str, str]], pending: int) -> str:
    if not effects:
        value = "on-demand"            # политика та же; у агента просто нет инструментов
    elif any(e == "on-demand" for _, e in effects):
        value = "on-demand"
    elif all(e == "allowed" for _, e in effects):
        value = "allowed"
    else:
        value = "on-demand"
    return value + (f" · {pending} ждут" if pending else "")


# ----------------------------------------------------------------- the chat


class Chat:
    def __init__(self, args, client: Client, view: View):
        self.args = args
        self.client = client
        self.view = view
        self.cat = Catalog.load(client)
        self.cwd = os.path.abspath(getattr(args, "cwd", None) or os.getcwd())
        self.mode = "chat"
        self.agent = self._initial_agent()
        self.session = Session.open(client.target.data_dir, getattr(args, "session", None))
        self.last_task: dict | None = None
        self.last_usage: dict = {}
        self.session_cost: float | None = 0.0
        self.last_coding_id: str | None = None
        self.history_on = history_enabled(args)
        self.prompt_session = None
        self._caps: list[dict] | None = None

    # -- setup ------------------------------------------------------------

    def _initial_agent(self) -> dict | None:
        ref = getattr(self.args, "agent", None)
        if ref:
            agent = self.cat.find_agent(ref)
            if agent is None:
                raise BossmanError(f"агент «{sanitize(ref)}» не найден", kind="not_found")
            return agent
        try:
            pre = self.client.post("/api/tasks/preflight", {"prompt": "", "agent_id": None})
            if pre.get("ok"):
                return self.cat.find_agent(str(pre["agent"]["id"]))
        except BossmanError:
            pass
        return next((a for a in self.cat.agents if a.get("enabled")), None)

    def caps(self) -> list[dict]:
        if self._caps is None:
            try:
                self._caps = (self.client.get("/api/capabilities") or {}).get("capabilities") or []
            except BossmanError:
                self._caps = []
        return self._caps

    def computer_state(self) -> str | None:
        try:
            st = self.client.get("/api/computer/status")
        except BossmanError:
            return None
        if st.get("stopped"):
            return "stopped"
        return "enabled" if st.get("available") else "disabled"

    def pending_approvals(self) -> list[dict]:
        try:
            return self.client.get("/api/approvals", params={"status": "pending"}) or []
        except BossmanError:
            return []

    def header(self) -> None:
        ident = self.client.target.identity or {}
        desc = self.cat.describe_agent(self.agent).get("model") or {}
        width = self.view.width
        self.view.print(render_title(ident.get("version"), ident.get("build_sha_short")
                                     or ident.get("source_identity"), width, screen="CLI Operator",
                                     plain=self.view.plain))
        context = None
        usage = self.last_usage
        if usage.get("reported") and usage.get("context_window") and usage.get("step_tokens_in"):
            context = (int(usage["step_tokens_in"]), int(usage["context_window"]))
        cells = status_cells(mode=self.mode, model=desc.get("alias"), model_locality=desc.get("locality"),
                             model_kind=desc.get("model_kind"),
                             approvals=approvals_cell(tool_effects(self.agent, self.caps()),
                                                      len(self.pending_approvals())),
                             context=context, computer=self.computer_state())
        for line in render_status_bar(cells, width, plain=self.view.plain):
            self.view.print(line)
        g = glyphs()
        agent_name = sanitize((self.agent or {}).get("name")) or "—"
        self.view.print(Text(f"агент: {agent_name} · сессия {self.session.id} · данные: "
                             f"{self.client.target.data_dir}", style="muted"))
        self.view.print(Text(f"Enter — отправить · /help — команды · Ctrl+C — отмена · /exit — выход "
                             f"(задачи продолжают работу в Bossman) {g.dot} клиент {CLIENT_VERSION}",
                             style="muted"))

    # -- input --------------------------------------------------------------

    def _prompt_text(self) -> str:
        return f"{self.cwd}> "

    def read_line(self) -> str:
        if self.prompt_session is not None:
            return self.prompt_session.prompt(self._prompt_text())
        sys.stdout.write(self._prompt_text())
        sys.stdout.flush()
        line = sys.stdin.readline()
        if line == "":
            raise EOFError
        return line.rstrip("\n")

    def setup_prompt(self) -> None:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            return                                         # redirected: plain readline
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.completion import merge_completers, PathCompleter, WordCompleter
        except ImportError:
            self.view.note("(prompt_toolkit не установлен: простой ввод; многострочная вставка "
                           "может отправиться по строкам)", "value.warn")
            return
        history = _make_history(self.client.target.data_dir / "terminal" / "history.txt", self.history_on)
        completer = merge_completers([WordCompleter(slash.completions(), sentence=True),
                                      PathCompleter(expanduser=True)])
        self.prompt_session = PromptSession(history=history, completer=completer,
                                            complete_while_typing=False, enable_history_search=True)

    # -- main loop ------------------------------------------------------------

    def run(self) -> int:
        self.setup_prompt()
        self.header()
        self.offer_env_keys()
        if self.session.turns:
            self.replay_session()
        last_interrupt = 0.0
        while True:
            try:
                line = self.read_line()
            except KeyboardInterrupt:
                now = time.monotonic()
                if now - last_interrupt < 2.0:
                    return self.exit_chat()
                last_interrupt = now
                self.view.note("(Ctrl+C ещё раз или /exit — выход; задачи продолжают работу в Bossman)")
                continue
            except EOFError:
                return self.exit_chat()
            parsed = slash.parse(line)
            if parsed.kind == "empty":
                continue
            if parsed.kind == "unknown":
                self.view.error(parsed.error)
                continue
            if parsed.kind == "message":
                self.turn(parsed.text)
                continue
            if parsed.name == "exit":
                return self.exit_chat()
            try:
                getattr(self, "cmd_" + parsed.name)(parsed)
            except BossmanError as exc:
                self.view.error(exc.message, exc.hint)
            except KeyboardInterrupt:
                self.view.note("(прервано)")

    def exit_chat(self) -> int:
        active = [t for t in self._session_tasks() if t.get("status") in
                  ("queued", "running", "waiting_approval", "paused")]
        if active:
            self.view.note("Выход: " + ", ".join(f"задача {t['id']} ({t['status']})" for t in active)
                           + " продолжает работу в Bossman — `bossman status`, веб или Telegram.")
        self.view.note(f"Сессия {self.session.id}: `bossman resume {self.session.id}`")
        return EXIT_OK

    def _session_tasks(self) -> list[dict]:
        out = []
        for turn in self.session.turns[-5:]:
            try:
                out.append((self.client.get(f"/api/tasks/{turn['task_id']}") or {}).get("task") or {})
            except BossmanError:
                continue
        return [t for t in out if t]

    def replay_session(self) -> None:
        self.view.note(f"(продолжение сессии {self.session.id}: {len(self.session.turns)} ходов)")
        if self.session.summary:
            self.view.note(f"(беседа сжата /compact после хода {self.session.compacted_at_turn}: "
                           "дальше уходит резюме + новые ходы; /context — подробнее)")
        for turn in self.session.turns[-CONTEXT_TURNS:]:
            self.view.user(turn.get("text") or "")
            try:
                data = self.client.get(f"/api/tasks/{turn['task_id']}")
            except BossmanError:
                continue
            status = (data.get("task") or {}).get("status")
            text = data.get("result") or data.get("error") or f"({status})"
            self.view.state.said_bossman = False
            self.view.on_record({"type": "assistant_message", "text": text})

    # -- a turn -------------------------------------------------------------

    def turn(self, text: str) -> None:
        if self.agent is None:
            self.view.error("нет агента с моделью", "настройте агента в вебе («Агенты») или /agent <имя>")
            return
        self.view.user(text)
        preamble = context_preamble(self.client, self.session)
        prompt = preamble + text if preamble else text
        try:
            pre = self.client.post("/api/tasks/preflight", {"prompt": prompt, "agent_id": self.agent["id"]})
            if not pre.get("ok"):
                self.view.error(pre.get("reason") or "исполнитель недоступен", pre.get("hint"))
                return
            sub = create_draft(self.client, prompt=prompt, title=text[:120], agent=self.agent,
                               request_id=new_request_id())
        except BossmanError as exc:
            self.view.error(exc.message, exc.hint)
            return
        task_id = sub.task["id"]
        self.session.add(task_id, text)
        self.last_task = sub.task
        self.view.state = type(self.view.state)()
        self.view.state.task_id = task_id
        self.view.state.run_started = time.monotonic()
        follower = Follower(self.client, task_id, sink=self._sink,
                            options=FollowOptions(approval_mode="ask", verbose=self.view.verbose),
                            decide=self.ask_approval, on_tick=lambda _st: self.view.tick())
        follower.model_kind_hint = (self.cat.describe_agent(self.agent).get("model") or {}).get("model_kind")
        try:
            res = follower.run(before=lambda: start_run(self.client, sub))
        except KeyboardInterrupt:
            res = self._cancel(follower)
        except BossmanError as exc:
            self.view.error(exc.message, exc.hint)
            return
        self.view.on_record(res)
        cost = (res.get("usage") or {}).get("cost_usd")
        if cost is None:
            self.session_cost = None           # хоть один запуск без известной цены — сумма «—»
        elif self.session_cost is not None:
            self.session_cost += float(cost)

    def _sink(self, rec: dict) -> None:
        if rec.get("type") == "usage":
            self.last_usage = rec
        self.view.on_record(rec)

    def _cancel(self, follower: Follower) -> dict:
        g = glyphs()
        tid = follower.state.task_id
        self.view.note(f"{g.stop} запрошена отмена задачи {tid} — жду подтверждения Bossman… "
                       "(Ctrl+C ещё раз — отсоединиться)", "value.warn")
        follower.request_stop()
        try:
            follower._await_stop()
        except KeyboardInterrupt:
            self.view.note(f"отсоединился от задачи {tid}; её состояние: `bossman status` / "
                           f"`bossman events {tid}`", "value.warn")
            from .follow import interrupted_result
            return interrupted_result(follower.state, stop_confirmed=False)
        from .follow import interrupted_result
        confirmed = follower.state.status in ("stopped", "cancelled")
        return interrupted_result(follower.state, stop_confirmed=confirmed)

    # -- approvals ----------------------------------------------------------

    def ask_approval(self, rec: dict) -> str:
        """y / n / d(etails) / Enter (decide later elsewhere). If the owner
        decides in the web or Telegram meanwhile, the question closes itself."""
        approval_id = rec.get("approval_id")
        stop = threading.Event()
        decided_elsewhere: dict = {}

        def watch():
            while not stop.wait(2.0):
                try:
                    rows = self.client.get("/api/approvals", params={"status": "all"}) or []
                except BossmanError:
                    continue
                row = next((r for r in rows if r.get("id") == approval_id), None)
                if row is not None and row.get("status") != "pending":
                    decided_elsewhere["status"] = row.get("status")
                    self._abort_prompt()
                    return

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        try:
            while True:
                try:
                    answer = self._ask("[y] да  [n] нет  [d] подробнее  [Enter] решу в вебе/Telegram › ")
                except KeyboardInterrupt:
                    return "wait"
                if decided_elsewhere or answer is None:
                    return "wait"
                answer = answer.strip().lower()
                if answer in ("y", "yes", "д", "да"):
                    return "approve"
                if answer in ("n", "no", "н", "нет"):
                    return "deny"
                if answer in ("d", "details", "п"):
                    self._approval_details(approval_id)
                    continue
                return "wait"
        finally:
            stop.set()

    def _ask(self, text: str) -> str | None:
        if self.prompt_session is not None:
            from prompt_toolkit import PromptSession
            self._approval_prompt = PromptSession()
            try:
                return self._approval_prompt.prompt(text)
            finally:
                self._approval_prompt = None
        sys.stdout.write(text)
        sys.stdout.flush()
        line = sys.stdin.readline()
        return None if line == "" else line

    def _abort_prompt(self) -> None:
        session = getattr(self, "_approval_prompt", None)
        app = getattr(session, "app", None)
        loop = getattr(app, "loop", None)
        if app is not None and loop is not None:
            try:
                loop.call_soon_threadsafe(lambda: app.exit(result=None) if app.is_running else None)
            except RuntimeError:
                pass

    def _approval_details(self, approval_id: Any) -> None:
        rows = self.client.get("/api/approvals", params={"status": "all"}) or []
        row = next((r for r in rows if r.get("id") == approval_id), None)
        if row is None:
            self.view.note("разрешение не найдено")
            return
        self.view.print(Text(f"разрешение #{approval_id} · {sanitize(row.get('kind'))} · задача "
                             f"{row.get('task_id')}", style="label"))
        for ln in sanitize(row.get("preview")).split("\n"):
            self.view.print(Text("  " + ln, style="text"))

    # -- slash commands -------------------------------------------------------

    def cmd_help(self, _p) -> None:
        for name, (usage, text) in slash.COMMANDS.items():
            t = Text(usage.ljust(44), style="label")
            t.append(text, style="text")
            self.view.print(t)

    def cmd_status(self, _p) -> None:
        self.cat = Catalog.load(self.client)
        self._caps = None
        self.header()
        tasks = self.client.get("/api/tasks", params={"status": "queued,running,waiting_approval,paused",
                                                      "limit": 20}) or []
        self.view.note(f"активных задач: {len(tasks)}" + (": " + ", ".join(
            f"#{t['id']} {t['status']}" for t in tasks[:8]) if tasks else ""))
        usage = self.last_usage
        if usage:
            self.view.note(f"последний шаг: {human_tokens(usage.get('tokens_in'))}→"
                           f"{human_tokens(usage.get('tokens_out'))} tok · стоимость "
                           f"{money(usage.get('cost_usd'))} · лимит запуска "
                           f"{money(usage.get('max_cost_usd'))} / {human_tokens(usage.get('max_tokens_total'))} tok")

    def cmd_tasks(self, p) -> None:
        from .cli import list_items
        limit = int(p.args[0]) if p.args and p.args[0].isdigit() else 10
        for t in list_items(self.client, "tasks", limit=limit):
            self.view.print(Text(f"  #{t['id']:<5} {t['status']:<17} {t['title']}", style="text"))

    def cmd_models(self, p) -> None:
        if p.args and p.args[0] == "use":
            if len(p.args) < 2:
                self.view.error("/models use <id|alias>")
                return
            model = self.cat.find_model(" ".join(p.args[1:]))
            if model is None:
                self.view.error("модель не найдена", "/models — список")
                return
            if self.agent is None:
                self.view.error("нет выбранного агента", "/agent <имя>")
                return
            self.client.patch(f"/api/agents/{self.agent['id']}", {"model_id": model["id"]})
            self.cat = Catalog.load(self.client)
            self.agent = self.cat.find_agent(str(self.agent["id"]))
            self.view.note(f"агент «{sanitize(self.agent.get('name'))}» теперь использует "
                           f"{sanitize(model.get('alias') or model.get('name'))} (это постоянная настройка "
                           f"агента — видна в вебе)", "value.on")
            return
        current = (self.agent or {}).get("model_id")
        from .cli import list_items
        for m in list_items(self.client, "models"):
            mark = glyphs().done if m["id"] == current else " "
            badge = " MOCK_MODEL" if m.get("model_kind") == "MOCK_MODEL" else ""
            self.view.print(Text(f" {mark} #{m['id']:<4} {m['alias']:<32} {m.get('locality') or '—':<6}"
                                 f" ctx {m.get('context_window') or '—'}{badge}",
                                 style="value.on" if mark.strip() else "text"))

    def cmd_agent(self, p) -> None:
        if p.args:
            agent = self.cat.find_agent(" ".join(p.args))
            if agent is None:
                self.view.error("агент не найден", "/agent — список")
                return
            self.agent = agent
            self._caps = None
            self.view.note(f"агент: {sanitize(agent.get('name'))}", "value.on")
            return
        for a in self.cat.agents:
            mark = glyphs().done if self.agent and a.get("id") == self.agent.get("id") else " "
            model = (self.cat.describe_agent(a).get("model") or {}).get("alias") or "—"
            self.view.print(Text(f" {mark} #{a.get('id'):<4} {sanitize(a.get('name')):<28} {model}"
                                 + ("" if a.get("enabled") else "  (выключен)"), style="text"))

    def cmd_skills(self, p) -> None:
        if p.text:
            hits = self.client.get("/api/skill-catalog/select", params={"q": p.text}) or []
            if not hits:
                self.view.note("ни один навык не подошёл к этому тексту")
            for s in hits:
                self.view.print(Text(f"  {glyphs().note} {sanitize(s.get('id'))} — {sanitize(s.get('title'))}"
                                     f" (score {s.get('score')}, {sanitize(s.get('status'))})", style="note"))
            return
        from .cli import list_items
        items = list_items(self.client, "skills")
        by_status: dict[str, int] = {}
        for s in items:
            by_status[str(s.get("status") or "—")] = by_status.get(str(s.get("status") or "—"), 0) + 1
        self.view.note("навыки: " + ", ".join(f"{k}: {v}" for k, v in sorted(by_status.items())) if items
                       else "навыков нет")
        for s in items[:40]:
            self.view.print(Text(f"  {sanitize(s.get('id')):<40} {sanitize(s.get('status')) or '—':<12} "
                                 f"{sanitize(s.get('source'))}", style="text"))

    def cmd_tools(self, _p) -> None:
        effects = tool_effects(self.agent, self.caps())
        if not effects:
            self.view.note("у агента нет инструментов: только ответ модели")
            return
        for name, effect in effects:
            style = {"allowed": "value.on", "on-demand": "value.warn", "never": "value.off"}.get(effect, "muted")
            self.view.print(Text(f"  {name:<36} {effect}", style=style))

    def cmd_memory(self, p) -> None:
        if not p.text:
            self.view.error("/memory <запрос>")
            return
        try:
            res = self.client.post("/api/memory/search", {"query": p.text})
            for it in res.get("items") or []:
                self.view.print(Text(f"  {glyphs().note} {sanitize(it.get('source'))} "
                                     f"{sanitize(it.get('heading'))} (score {it.get('score')})", style="note"))
                self.view.print(Text("    " + sanitize(it.get("content"), keep_newlines=False)[:200],
                                     style="muted"))
            if not res.get("items"):
                self.view.note("в заметках ничего не найдено")
        except BossmanError as exc:
            self.view.note(f"заметки: {exc.message}")
        facts = self.client.get("/api/memory/facts", params={"query": p.text, "limit": 10}) or {}
        for f in facts.get("items") or []:
            self.view.print(Text(f"  факт #{f.get('id')}: {sanitize(f.get('statement') or '')[:200]}",
                                 style="text"))

    def cmd_diff(self, p) -> None:
        ctid = p.args[0] if p.args else self.last_coding_id
        if not ctid:
            items = (self.client.get("/api/coding-tasks") or {}).get("items") or []
            ctid = items[0]["id"] if items else None
        if not ctid:
            self.view.note("coding-задач нет")
            return
        from .screens import render_diff
        rec = self.client.get(f"/api/coding-tasks/{ctid}")
        if not rec.get("diff"):
            self.view.note(f"у coding task {ctid} нет diff ({rec.get('status')})")
            return
        self.view.print(*render_diff(rec["diff"]))

    def cmd_code(self, p) -> None:
        import argparse
        ap = argparse.ArgumentParser(prog="/code", add_help=False, exit_on_error=False)
        ap.add_argument("--allow", action="append", default=[])
        ap.add_argument("--protect", action="append", default=[])
        ap.add_argument("--verify", action="append", default=[])
        ap.add_argument("--repo")
        try:
            ns, rest = ap.parse_known_args(p.args)
        except (argparse.ArgumentError, SystemExit):
            self.view.error(slash.COMMANDS["code"][0])
            return
        instruction = " ".join(rest).strip()
        if not instruction or not ns.allow:
            self.view.error(slash.COMMANDS["code"][0], "область правок (--allow) задаётся явно")
            return
        from .cli import Out
        from .screens import run_coding_task
        self.mode = "code"
        self.view.user(f"/code {instruction}")
        out = Out("text")
        try:
            run_coding_task(self.client, out, instruction=instruction, allow=ns.allow, protect=ns.protect,
                            verify=ns.verify, repo=os.path.abspath(ns.repo or self.cwd),
                            agent=str(self.agent["id"]) if self.agent else None, timeout=900,
                            use_memory=True)
        finally:
            self.mode = "chat"
            items = (self.client.get("/api/coding-tasks") or {}).get("items") or []
            self.last_coding_id = items[0]["id"] if items else self.last_coding_id

    def _decide_cmd(self, p, approve: bool) -> None:
        aid = int(p.args[0]) if p.args and p.args[0].isdigit() else None
        if aid is None:
            pending = self.pending_approvals()
            if self.last_task:
                pending = [a for a in pending if a.get("task_id") == self.last_task.get("id")] or pending
            if len(pending) != 1:
                self.view.error("укажите id: /approve <id>" if pending else "ожидающих разрешений нет")
                return
            aid = pending[0]["id"]
        row = self.client.post(f"/api/approvals/{aid}", {"approve": approve, "by": "owner:terminal"})
        self.view.note(f"разрешение #{aid}: {row.get('status')}", "value.on" if approve else "value.warn")

    def cmd_approve(self, p) -> None:
        self._decide_cmd(p, True)

    def cmd_deny(self, p) -> None:
        self._decide_cmd(p, False)

    def cmd_approvals(self, _p) -> None:
        rows = self.pending_approvals()
        if not rows:
            self.view.note("ожидающих разрешений нет")
        for a in rows:
            self.view.print(Text(f"  #{a.get('id')} задача {a.get('task_id')} · {sanitize(a.get('kind'))}: "
                                 f"{sanitize(a.get('preview'), keep_newlines=False)[:120]}", style="approval"))

    def _task_ref(self, p) -> int | None:
        if p.args and p.args[0].isdigit():
            return int(p.args[0])
        return (self.last_task or {}).get("id")

    def _task_action(self, p, action: str) -> None:
        tid = self._task_ref(p)
        if tid is None:
            self.view.error(f"/{action} <task>")
            return
        res = self.client.post(f"/api/tasks/{tid}/{action}")
        self.view.note(f"задача {tid}: {action} → {(res or {}).get('status')}")

    def cmd_pause(self, p) -> None:
        self._task_action(p, "pause")

    def cmd_resume(self, p) -> None:
        self._task_action(p, "resume")

    def cmd_stop(self, p) -> None:
        if p.args and p.args[0] == "all":
            from .cli import Out, global_stop
            global_stop(self.client, Out("text"))
            return
        self._task_action(p, "stop")

    def cmd_computer(self, p) -> None:
        action = p.args[0] if p.args else "status"
        if action == "stop":
            res = self.client.post("/api/computer/stop")
            self.view.note(f"управление компьютером: STOP ({'сохранён' if res.get('persisted') else 'в памяти'})",
                           "value.off")
            return
        if action == "resume":
            self.client.post("/api/computer/resume")
            self.view.note("управление компьютером: продолжено (прежние наблюдения недействительны)", "value.on")
            return
        st = self.client.get("/api/computer/status")
        self.view.note(f"доступно: {st.get('available')} · STOP: {st.get('stopped')} · занят: {st.get('busy')}"
                       f" · {sanitize(st.get('detail'))}")

    def cmd_evolve(self, p) -> None:
        from .cli import Out
        from .screens import evolution_call
        evolution_call(self.client, Out("text"), p.args[0] if p.args else "status")

    def cmd_keys(self, p) -> None:
        from types import SimpleNamespace
        from .cli import Out
        from .keys import run_keys
        action = p.args[0] if p.args else "list"
        if action not in ("list", "set", "remove", "import-env"):
            self.view.error(slash.COMMANDS["keys"][0])
            return
        vendor = p.args[1] if len(p.args) > 1 else None
        if len(p.args) > 2:
            self.view.error("ключ в строке команды не принимаю — введите его в скрытом запросе")
            return
        args = SimpleNamespace(action=action, vendor=vendor, stdin=False, base_url=None, yes=False,
                               no_models=False)
        run_keys(self.client, Out("text"), args)
        self.cat = Catalog.load(self.client)

    def cmd_panel(self, _p) -> None:
        """The optional context panel of the reference, printed on demand."""
        g = glyphs()
        rows: list[tuple[str, list[str]]] = [("Workspace", [self.cwd])]
        task = self.last_task
        if task:
            try:
                task = (self.client.get(f"/api/tasks/{task['id']}") or {}).get("task") or task
            except BossmanError:
                pass
            rows.append(("Active Task", [f"#{task.get('id')} {sanitize(task.get('title'))[:60]} "
                                         f"({task.get('status')})"]))
        else:
            rows.append(("Active Task", ["—"]))
        effects = tool_effects(self.agent, self.caps())
        rows.append(("Tools Enabled", [", ".join(n for n, _ in effects) or "нет (только ответ модели)"]))
        memory_line = []
        try:
            stats = (self.client.get("/api/memory/stats") or {}).get("stats") or {}
            memory_line.append(f"{stats.get('files', '—')} заметок {g.dot} {stats.get('chunks', '—')} фрагментов")
        except BossmanError:
            memory_line.append("заметки не подключены")
        try:
            facts = self.client.get("/api/memory/facts", params={"limit": 200}) or {}
            total = facts.get("total")
            memory_line.append(f"{total}{'+' if total == 200 else ''} фактов")
        except BossmanError:
            pass
        rows.append(("Memory (summary)", [f" {g.dot} ".join(memory_line)]))
        usage = self.last_usage
        limit = usage.get("max_cost_usd") if usage else None
        spent = self.session_cost
        if spent is not None and limit:
            budget = f"{money(spent)} / {money(limit)} ({int(100 * spent / limit)}%) — лимит одного запуска"
        else:
            budget = f"{money(spent)}" + (f" / лимит запуска {money(limit)}" if limit else "")
        rows.append(("Budget (session)", [budget]))
        desc = self.cat.describe_agent(self.agent).get("model") or {}
        rows.append(("Model", [f"{desc.get('alias') or '—'} ({desc.get('locality') or '—'})"
                               f"{' MOCK_MODEL' if desc.get('model_kind') == 'MOCK_MODEL' else ''}",
                               f"Context: {desc.get('context_window') or '—'}"]))
        for title, lines in rows:
            self.view.print(Text(f"{g.section} {title}", style="label"))
            for ln in lines:
                self.view.print(Text(f"  {sanitize(ln)}", style="text"))

    # -- Claude-Code-style session commands ------------------------------------

    def _run_quiet_task(self, prompt: str, title: str) -> dict:
        """One ordinary Bossman task for the current agent, followed to a verdict
        without printing its stream (used by /compact)."""
        pre = self.client.post("/api/tasks/preflight", {"prompt": prompt, "agent_id": self.agent["id"]})
        if not pre.get("ok"):
            raise BossmanError(pre.get("reason") or "исполнитель недоступен", kind="blocked",
                               hint=pre.get("hint"))
        sub = create_draft(self.client, prompt=prompt, title=title, agent=self.agent,
                           request_id=new_request_id())
        self.view.note(f"сжимаю беседу задачей Bossman #{sub.task['id']}… (Ctrl+C — отменить)")
        self.view.state = type(self.view.state)()
        self.view.state.task_id = sub.task["id"]
        self.view.state.run_started = time.monotonic()
        follower = Follower(self.client, sub.task["id"], sink=lambda _rec: None,
                            options=FollowOptions(approval_mode="fail", max_seconds=900),
                            on_tick=lambda _st: self.view.tick())
        try:
            return follower.run(before=lambda: start_run(self.client, sub))
        except KeyboardInterrupt:
            return self._cancel(follower)

    def _preamble_size(self) -> tuple[int, int]:
        chars = len(context_preamble(self.client, self.session))
        return chars, approx_tokens(chars)

    def cmd_compact(self, p) -> None:
        if self.agent is None:
            self.view.error("нет агента с моделью", "/agent <имя>")
            return
        if not self.session.live_turns():
            self.view.note("сжимать нечего: после последнего /compact новых ходов нет")
            return
        before = self._preamble_size()
        prompt = compact_prompt(self.client, self.session, p.text)
        res = self._run_quiet_task(prompt, "/compact: резюме беседы терминала " + self.session.id)
        summary = sanitize(res.get("result") or "").strip()[:2 * COMPACT_SUMMARY_CHARS]
        tid = res.get("task_id")
        if res.get("task_state") != "PASS" or not summary:
            if res.get("task_state") in ("WAIT_APPROVAL", "TIMEOUT") and tid is not None:
                try:
                    self.client.post(f"/api/tasks/{tid}/stop")   # no parked /compact task left behind
                except BossmanError:
                    pass
            reason = res.get("error") or res.get("note") or ("пустой ответ" if res.get("task_state") == "PASS"
                                                             else res.get("status"))
            self.view.error(f"/compact не удался (задача {tid}: {res.get('task_state')}; {sanitize(reason)}) — "
                            "сессия не изменена")
            return
        self.session.compacted(summary, int(tid))
        after = self._preamble_size()
        self.view.note(f"беседа сжата задачей #{tid}: контекст следующего сообщения {before[0]} → {after[0]} "
                       f"символов (≈{before[1]} → ≈{after[1]} токенов, оценка символы/4)", "value.on")

    def cmd_context(self, _p) -> None:
        parts = context_parts(self.client, self.session)
        chars = len(conversation_context.compose(parts))
        turns = len(parts) - (1 if self.session.summary else 0)
        desc = self.cat.describe_agent(self.agent).get("model") or {}
        window = self.last_usage.get("context_window") or desc.get("context_window")
        tokens = approx_tokens(chars)
        share = f" ({100 * tokens // int(window)}% окна)" if window and chars else ""
        compacted = (f"да (после хода {self.session.compacted_at_turn}, {len(self.session.summary)} символов)"
                     if self.session.summary else "нет")
        for label, value in (
                ("резюме /compact", compacted),
                ("ходов в контексте", f"{turns} (последние до {CONTEXT_TURNS} после сжатия; "
                                      f"всего в сессии {len(self.session.turns)})"),
                ("размер", f"{chars} символов · ≈{tokens} токенов (оценка: символы/4){share}"),
                ("агент", sanitize((self.agent or {}).get("name")) or "—"),
                ("модель", f"{desc.get('alias') or '—'} ({desc.get('locality') or '—'})"),
                ("окно контекста модели", str(window) if window else "— (backend не сообщил)")):
            t = Text(f"  {label:<22} ", style="label")
            t.append(sanitize(value), style="text")
            self.view.print(t)

    def _session_task_ids(self) -> list[int]:
        return [t["task_id"] for t in self.session.turns if isinstance(t.get("task_id"), int)] \
            + list(self.session.compact_tasks)

    def _task_usage(self, data: dict) -> tuple[int, int, float | None]:
        """tokens in/out and cost of all runs of a task; cost is None unless the
        model's pricing is known (the backend stores 0.0 for unknown prices)."""
        runs = data.get("runs") or []
        tin = sum(int(r.get("tokens_in") or 0) for r in runs)
        tout = sum(int(r.get("tokens_out") or 0) for r in runs)
        cost = 0.0
        for r in runs:
            model = self.cat.find_model(str(r.get("model_alias") or "")) if r.get("model_alias") else None
            if not (model and model.get("pricing_known")):
                return tin, tout, None
            cost += float(r.get("cost_usd") or 0.0)
        return tin, tout, cost

    def cmd_cost(self, _p) -> None:
        ids = self._session_task_ids()
        if not ids:
            self.view.note("в этой сессии задач ещё нет")
            return
        total_in = total_out = 0
        total_cost: float | None = 0.0
        unknown = 0
        for tid in ids:
            try:
                data = self.client.get(f"/api/tasks/{tid}") or {}
            except BossmanError as exc:
                self.view.print(Text(f"  #{tid:<5} нет данных ({sanitize(exc.message)})", style="muted"))
                unknown += 1
                total_cost = None
                continue
            tin, tout, cost = self._task_usage(data)
            total_in += tin
            total_out += tout
            if cost is None:
                unknown += 1
                total_cost = None
            elif total_cost is not None:
                total_cost += cost
            task = data.get("task") or {}
            mark = " (/compact)" if tid in self.session.compact_tasks else ""
            self.view.print(Text(f"  #{tid:<5} {str(task.get('status') or '—'):<17} {human_tokens(tin)}→"
                                 f"{human_tokens(tout)} tok  {money(cost):>8}  "
                                 f"{sanitize(task.get('title'), keep_newlines=False)[:60]}{mark}", style="text"))
        note = f" (цена неизвестна для {unknown} из {len(ids)})" if unknown else ""
        self.view.note(f"итого за сессию: {len(ids)} задач · {human_tokens(total_in)}→{human_tokens(total_out)} "
                       f"tok · {money(total_cost)}{note}", "value.on")

    def _export_markdown(self) -> str:
        ident = self.client.target.identity or {}
        lines = [f"# Bossman — сессия терминала {self.session.id}", "",
                 f"- экспорт: {time.strftime('%Y-%m-%dT%H:%M:%S')}",
                 f"- Bossman: {sanitize(ident.get('version')) or '—'} · сборка "
                 f"{sanitize(ident.get('build_sha_short') or ident.get('source_identity')) or '—'} · "
                 f"{self.client.target.url}",
                 f"- данные: {self.client.target.data_dir}", ""]
        if self.session.summary:
            lines += [f"## Резюме /compact (после хода {self.session.compacted_at_turn}, задача "
                      f"#{self.session.compact_tasks[-1] if self.session.compact_tasks else '—'})", "",
                      sanitize(self.session.summary), ""]
        for n, turn in enumerate(self.session.turns, 1):
            tid = turn.get("task_id")
            try:
                data = self.client.get(f"/api/tasks/{tid}") or {}
            except BossmanError as exc:
                data = {"error": f"(ответ недоступен: {exc.message})"}
            status = (data.get("task") or {}).get("status") or "—"
            answer = data.get("result") or data.get("error") or f"({status})"
            lines += [f"## Ход {n} · задача #{tid} · {sanitize(turn.get('at')) or '—'}", "",
                      "**Владелец:**", "", sanitize(turn.get("text")), "",
                      f"**Bossman** ({sanitize(status)}):", "", sanitize(answer), ""]
        return "\n".join(lines)

    def cmd_export(self, p) -> None:
        force = "--force" in p.args
        rest = [a for a in p.args if a != "--force"]
        if len(rest) > 1:
            self.view.error(slash.COMMANDS["export"][0], "путь с пробелами — в кавычках")
            return
        if rest:
            path = Path(os.path.expanduser(rest[0]))
            path = path if path.is_absolute() else Path(self.cwd) / path
        else:
            path = Path(self.client.target.data_dir) / "terminal" / "exports" / f"{self.session.id}.md"
        if path.exists() and not force:
            self.view.error(f"файл уже есть: {path}", "другой путь или /export <путь> --force")
            return
        text = self._export_markdown()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        self.view.note(f"беседа сохранена: {path} ({len(self.session.turns)} ходов)", "value.on")

    def _probe(self, path: str) -> tuple[dict, str]:
        """(answer, "") or ({}, why there is no answer) — /doctor never crashes."""
        try:
            data = self.client.get(path)
        except BossmanError as exc:
            return {}, ("эта сборка не сообщает" if exc.kind in ("not_supported", "not_found")
                        else f"ошибка: {exc.message}")
        return (data, "") if isinstance(data, dict) else ({}, "нет данных")

    def cmd_doctor(self, _p) -> None:
        ident = self.client.target.identity or {}
        desc = self.cat.describe_agent(self.agent).get("model") or {}
        rows = [("Bossman", f"{ident.get('version') or '—'} · сборка "
                            f"{ident.get('build_sha_short') or ident.get('source_identity') or '—'} · "
                            f"{self.client.target.url}"),
                ("данные", str(self.client.target.data_dir)),
                ("агент / модель", f"{(self.agent or {}).get('name') or '—'} / {desc.get('alias') or '—'} "
                                   f"({desc.get('locality') or '—'})"
                                   f"{' MOCK_MODEL' if desc.get('model_kind') == 'MOCK_MODEL' else ''}")]
        ready, err = self._probe("/api/coding-tasks/readiness")
        rows.append(("coding path", err or ("готов" if ready.get("available") else
                                            f"не готов: {ready.get('reason') or '—'}")))
        mem, err = self._probe("/api/memory/config")
        rows.append(("память (заметки)", err or (f"подключена ({mem.get('backend_class') or mem.get('backend')})"
                                                 if mem.get("configured") else "не настроена")))
        comp, err = self._probe("/api/computer/status")
        rows.append(("управление компьютером", err or ("STOP" if comp.get("stopped") else
                                                       "доступно" if comp.get("available") else "недоступно")))
        try:
            pending = str(len(self.client.get("/api/approvals", params={"status": "pending"}) or []))
        except BossmanError as exc:
            pending = f"— ({exc.message})"
        rows.append(("ждут разрешения", pending))
        for label, value in rows:
            t = Text(f"  {label:<24} ", style="label")
            t.append(sanitize(value, keep_newlines=False), style="text")
            self.view.print(t)

    def cmd_expand(self, p) -> None:
        index = int(p.args[0]) if p.args and p.args[0].isdigit() else None
        self.view.expand(index)

    def cmd_history(self, p) -> None:
        if p.args and p.args[0] in ("on", "off"):
            self.history_on = p.args[0] == "on"
            if self.prompt_session is not None:
                self.prompt_session.history = _make_history(
                    self.client.target.data_dir / "terminal" / "history.txt", self.history_on)
            self.view.note(f"история ввода: {'сохраняется (секреты вырезаются)' if self.history_on else 'не сохраняется'}")
            return
        self.view.note(f"история ввода: {'включена' if self.history_on else 'выключена'} · файл "
                       f"{self.client.target.data_dir / 'terminal' / 'history.txt'}")

    def cmd_clear(self, _p) -> None:
        self.view.console.clear()
        self.header()

    # -- env keys -------------------------------------------------------------

    def offer_env_keys(self) -> None:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return
        from .keys import ask_import, env_candidates, store_key, _say_stored
        from .cli import Out
        try:
            found = [f for f in env_candidates(self.client) if not f["already_stored"]]
        except BossmanError:
            return
        for f in found:
            if ask_import(Out("text"), f, self.client.target.data_dir):
                try:
                    res = store_key(self.client, f["vendor"], f["key"])
                    _say_stored(Out("text"), f["vendor"], res)
                    self.cat = Catalog.load(self.client)
                except BossmanError as exc:
                    self.view.error(exc.message, exc.hint)


def run_chat(args) -> int:
    utf8_console()
    plain = bool(getattr(args, "plain", False)) or not color_allowed()
    console = make_console(plain=plain)
    view = View(console, plain=plain, verbose=bool(getattr(args, "verbose", False)))
    try:
        target = discover(getattr(args, "url", None), getattr(args, "data_dir", None))
    except BossmanError as exc:
        view.error(exc.message, exc.hint)
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return EXIT_DISCONNECTED
        sys.stdout.write("Запустить Bossman сейчас? [Y/n] ")
        sys.stdout.flush()
        if (sys.stdin.readline() or "").strip().lower() not in ("", "y", "yes", "д", "да"):
            return EXIT_DISCONNECTED
        from .launch import start_backend
        try:
            info = start_backend(url=getattr(args, "url", None), data_dir=getattr(args, "data_dir", None))
            view.note(f"Bossman запущен: {info['url']} (журнал: {info.get('log')})", "value.on")
            target = discover(info["url"], getattr(args, "data_dir", None))
        except BossmanError as exc2:
            view.error(exc2.message, exc2.hint)
            return EXIT_DISCONNECTED
    try:
        client = Client(target)
    except BossmanError as exc:
        view.error(exc.message, exc.hint)
        return EXIT_DISCONNECTED
    with client:
        try:
            return Chat(args, client, view).run()
        except BossmanError as exc:
            view.error(exc.message, exc.hint)
            return EXIT_DISCONNECTED if exc.kind in ("disconnected", "auth") else 1
