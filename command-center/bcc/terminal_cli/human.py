"""Human view: ONE scrollable column (Bossman 1.2 directive §4).

Compact header with the status cells of the "CLI Operator" reference, then the
conversation ("You ›" / "Bossman ›"), collapsed tool events, diffs and the
result, printed append-only — the terminal's scrollback keeps everything. The
only thing redrawn in place is a single transient status line (spinner, real
elapsed time, "ожидаю модель"), and the `View` is the one output coordinator:
every print goes through it, and it clears that line first.

Every value shown comes from a backend record; an unknown value is hidden or
shown as «—», never invented (no fake percentages, no fake $0).
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from rich.console import Console
from rich.text import Text

from . import theme
from .console import sanitize
from .theme import glyphs

LABEL_WIDTH = 11           # «Bossman › » column
THINK_LINES = 3            # collapsed thinking: first lines shown
RESULT_LINES = 4           # collapsed tool result preview


# ----------------------------------------------------------------- small utils

def human_tokens(n: int | None) -> str:
    if n is None:
        return "—"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}K"
    return str(n)


def human_duration(ms: int | float | None) -> str:
    if ms is None:
        return "—"
    s = float(ms) / 1000.0
    if s < 1:
        return f"{int(ms)} мс"
    if s < 60:
        return f"{s:.1f} с"
    m, s = divmod(int(s), 60)
    if m < 60:
        return f"{m}м{s:02d}с"
    h, m = divmod(m, 60)
    return f"{h}ч{m:02d}м"


def money(value: float | None) -> str:
    """Unknown cost is «—», never $0."""
    if value is None:
        return "—"
    return f"${value:.4f}" if value < 0.01 else f"${value:.2f}"


def args_preview(args: Any, limit: int = 80) -> str:
    """`path=calc.py, limit=3` — one line, bounded."""
    if not isinstance(args, dict) or not args:
        return ""
    parts = []
    for k, v in args.items():
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        parts.append(f"{sanitize(k)}={sanitize(v, keep_newlines=False)}")
    out = ", ".join(parts)
    return out if len(out) <= limit else out[: limit - 1] + "…"


# ----------------------------------------------------------------- status cells

@dataclass
class Cell:
    label: str
    value: str
    tone: str = "value"        # style name from theme.STYLES


def status_cells(*, mode: str, model: str | None, model_locality: str | None, model_kind: str | None,
                 approvals: str | None, context: tuple[int, int] | None,
                 computer: str | None) -> list[Cell]:
    """The five cells of the reference. A cell whose value is unknown is
    HIDDEN (Memory %), not filled with a plausible number."""
    cells = [Cell("Mode", mode, "value.on")]
    if model:
        suffix = []
        if model_locality:
            suffix.append(model_locality)
        if model_kind == "MOCK_MODEL":
            suffix.append("MOCK_MODEL")
        tone = "value.warn" if model_kind == "MOCK_MODEL" else "value"
        cells.append(Cell("Model", model + (f" ({', '.join(suffix)})" if suffix else ""), tone))
    else:
        cells.append(Cell("Model", "—", "value.warn"))
    if approvals:
        cells.append(Cell("Approvals", approvals, "value.on" if approvals.startswith("on-demand")
                          else "value.warn"))
    if context and context[1] > 0 and context[0] >= 0:
        used, limit = context
        pct = int(round(100.0 * used / limit))
        cells.append(Cell("Memory", f"{pct}% ({used}/{limit})", "value"))
    if computer:
        tone = {"enabled": "value.on", "stopped": "value.off"}.get(computer.split()[0], "value")
        cells.append(Cell("Computer Control", computer, tone))
    return cells


def render_status_bar(cells: list[Cell], width: int, *, plain: bool) -> list[Text]:
    """Boxed row of cells (fancy) or one «a | b | c» line (plain). Cells that
    do not fit the width wrap onto further rows — nothing is cut silently."""
    g = glyphs()
    pieces = []
    for c in cells:
        t = Text()
        t.append(f"{c.label}: ", style="label")
        t.append(sanitize(c.value, keep_newlines=False), style=c.tone)
        pieces.append(t)
    if plain:
        line = Text()
        for i, p in enumerate(pieces):
            if i:
                line.append(" | ")
            line.append_text(p)
        return [line]
    rows: list[list[Text]] = [[]]
    used = 1
    for p in pieces:
        need = p.cell_len + 3
        if rows[-1] and used + need > width:
            rows.append([])
            used = 1
        rows[-1].append(p)
        used += need
    hz = "─" if g.box == "square" else "-"
    vt = g.sep
    corners = ("┌", "┐", "└", "┘") if g.box == "square" else ("+", "+", "+", "+")
    out: list[Text] = []
    inner = max(10, width - 2)
    out.append(Text(corners[0] + hz * inner + corners[1], style="border"))
    for row in rows:
        line = Text(vt + " ", style="border")
        for i, p in enumerate(row):
            if i:
                line.append(f" {vt} ", style="border")
            line.append_text(p)
        pad = width - 1 - line.cell_len
        if pad > 0:
            line.append(" " * pad)
        line.append(vt, style="border")
        out.append(line)
    out.append(Text(corners[2] + hz * inner + corners[3], style="border"))
    return out


def render_title(version: str | None, build: str | None, width: int, *, screen: str,
                 plain: bool) -> Text:
    title = Text()
    name = f"Bossman {version}" if version else "Bossman"
    title.append(f"{name} — {screen}", style="title")
    if build:
        title.append(f"  · build {build}", style="muted")
    tagline = theme.TAGLINE
    gap = width - title.cell_len - len(tagline)
    if gap >= 2 and not plain:
        title.append(" " * gap)
        title.append(tagline, style="tagline")
    return title


# ----------------------------------------------------------------- text layout

_NUMBERED = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_BOLD_TITLE = re.compile(r"^\*\*(.+?)\*\*\s*(?:[-–—:]\s*)?(.*)$")
_NEXT = re.compile(r"^\s*(?:\*\*)?(Next|Дальше|Следующий шаг|Далее)(?:\*\*)?\s*:\s*(.+)$", re.I)


def format_answer(text: str) -> list[Text]:
    """Markdown-ish, but never interpreted as markup: numbered lists with bold
    titles (`1. **Title** — detail`), `Next:` lines after a thin rule. Anything
    else is plain text. Input is untrusted and already neutralised."""
    g = glyphs()
    lines: list[Text] = []
    for raw in sanitize(text).split("\n"):
        m = _NUMBERED.match(raw)
        nm = _NEXT.match(raw)
        if nm:
            lines.append(Text(g.rule * 40, style="border"))
            t = Text()
            t.append(f"{nm.group(1)}: ", style="label")
            t.append(nm.group(2), style="text")
            lines.append(t)
            continue
        if m:
            indent, num, rest = m.groups()
            t = Text(indent)
            t.append(f"{num}. ", style="plan.num")
            bm = _BOLD_TITLE.match(rest)
            if bm:
                t.append(bm.group(1), style="plan.title")
                if bm.group(2):
                    t.append(f" – {bm.group(2)}", style="text")
            else:
                t.append(rest, style="text")
            lines.append(t)
            continue
        t = Text()
        # **bold** inside a line: rendered bold, never parsed as rich markup
        pos = 0
        for bm in re.finditer(r"\*\*(.+?)\*\*", raw):
            t.append(raw[pos:bm.start()], style="text")
            t.append(bm.group(1), style="bold")
            pos = bm.end()
        t.append(raw[pos:], style="text")
        lines.append(t)
    return lines


# ----------------------------------------------------------------- the view

@dataclass
class ToolLine:
    name: str
    args: str
    started: float
    step: int | None = None
    done: bool = False


@dataclass
class ViewState:
    blocks: list[dict] = field(default_factory=list)     # for /expand and /history
    tools: dict[str, ToolLine] = field(default_factory=dict)
    said_bossman: bool = False
    last_status: str | None = None
    usage: dict = field(default_factory=dict)
    run_started: float | None = None
    step: int | None = None
    max_steps: int | None = None
    thinking_seen: bool = False
    task_id: int | None = None
    run_id: int | None = None
    model: str | None = None
    verdict: str | None = None
    pending_approval: int | None = None


class View:
    """Prints records as the conversation. The one output coordinator."""

    def __init__(self, console: Console, *, plain: bool, verbose: bool = False):
        self.console = console
        self.plain = plain
        self.verbose = verbose
        self.state = ViewState()
        self._lock = threading.RLock()
        self._status_shown = False
        self._spin = 0

    # -- output coordination ------------------------------------------------

    @property
    def width(self) -> int:
        return self.console.width or shutil.get_terminal_size((100, 30)).columns

    def _clear_status(self) -> None:
        if self._status_shown and not self.plain:
            self.console.file.write("\r\x1b[2K")
            self.console.file.flush()
        self._status_shown = False

    def print(self, *items: Any) -> None:
        with self._lock:
            self._clear_status()
            for item in items:
                self.console.print(item, overflow="fold", no_wrap=False, crop=False)

    def status_line(self, text: str) -> None:
        """The transient line: real elapsed time and what we wait for. TTY only."""
        if self.plain:
            return
        with self._lock:
            g = glyphs()
            self._spin = (self._spin + 1) % len(g.spinner)
            t = Text(f"{g.spinner[self._spin]} ", style="label")
            t.append(sanitize(text, keep_newlines=False)[: max(10, self.width - 4)], style="muted")
            self._clear_status()
            with self.console.capture() as cap:
                self.console.print(t, end="", no_wrap=True, crop=True)
            self.console.file.write(cap.get())
            self.console.file.flush()
            self._status_shown = True

    def tick(self) -> None:
        st = self.state
        if st.run_started is None or st.last_status in ("completed", "failed", "stopped", "blocked"):
            return
        elapsed = human_duration((time.monotonic() - st.run_started) * 1000)
        if st.last_status == "waiting_approval":
            what = "ждёт решения владельца (веб / Telegram / y·n здесь)"
        elif st.last_status == "paused":
            what = "пауза: ждёт владельца"
        elif any(not t.done for t in st.tools.values()):
            running = [t.name for t in st.tools.values() if not t.done]
            what = f"выполняется {running[-1]}"
        else:
            what = "думает…" if not st.thinking_seen else "ожидаю модель"
        step = f" · шаг {st.step}/{st.max_steps}" if st.step and st.max_steps else ""
        self.status_line(f"{what} · {elapsed}{step} · Ctrl+C — отменить задачу")

    # -- blocks -------------------------------------------------------------

    def _gutter(self, label: str | None, style: str, body: Iterable[Text]) -> list[Text]:
        """Label in a fixed-width left column, body wrapped in the right one."""
        out: list[Text] = []
        avail = max(20, self.width - LABEL_WIDTH - 1)
        first = True
        for line in body:
            wrapped = line.wrap(self.console, avail) if line.cell_len > avail else [line]
            for piece in wrapped:
                row = Text()
                if first and label:
                    row.append(label.ljust(LABEL_WIDTH), style=style)
                else:
                    row.append(" " * LABEL_WIDTH)
                row.append_text(piece)
                out.append(row)
                first = False
        if first and label:
            out.append(Text(label, style=style))
        return out

    def _bossman(self, body: list[Text]) -> None:
        label = None
        if not self.state.said_bossman:
            label = glyphs().bossman
            self.state.said_bossman = True
        self.print(*self._gutter(label, "bossman", body))

    def user(self, text: str) -> None:
        self.state.said_bossman = False
        self.print(Text(""), *self._gutter(glyphs().you, "you",
                                           [Text(line, style="text") for line in sanitize(text).split("\n")]))
        self.print(Text(""))

    def note(self, text: str, style: str = "muted") -> None:
        self.print(Text(sanitize(text), style=style))

    def error(self, text: str, hint: str | None = None) -> None:
        g = glyphs()
        t = Text(f"{g.fail} ", style="error")
        t.append(sanitize(text), style="error")
        self.print(t)
        if hint:
            self.print(Text(f"  {sanitize(hint)}", style="muted"))

    # -- records ------------------------------------------------------------

    def on_record(self, rec: dict) -> None:
        handler = getattr(self, "_r_" + str(rec.get("type")), None)
        if handler is not None:
            handler(rec)

    def _r_task(self, rec: dict) -> None:
        st = self.state
        status = rec.get("status")
        st.task_id = rec.get("task_id") or st.task_id
        st.run_id = rec.get("run_id") or st.run_id
        if status == "running" and st.run_started is None:
            st.run_started = time.monotonic()
        if status == st.last_status:
            return
        st.last_status = status
        g = glyphs()
        if status == "waiting_approval":
            self._bossman([Text(f"{g.approval} ждёт решения владельца", style="approval")])
        elif status == "paused":
            self._bossman([Text(f"{g.stop} пауза: задача ждёт владельца", style="value.warn")])
        elif status == "stopped":
            self._bossman([Text(f"{g.stop} остановлена (подтверждено Bossman)", style="value.off")])
        elif status == "blocked":
            reason = rec.get("reason") or rec.get("error") or ""
            self._bossman([Text(f"{g.fail} не принята к исполнению: {sanitize(reason)}", style="error")])
        elif status == "failed" and rec.get("error"):
            self._bossman([Text(f"{g.fail} {sanitize(rec['error'])}", style="error")])

    def _r_step(self, rec: dict) -> None:
        self.state.step = rec.get("step") or self.state.step
        self.state.max_steps = rec.get("max_steps") or self.state.max_steps
        self.state.model = rec.get("model") or self.state.model
        if self.state.run_started is None:
            self.state.run_started = time.monotonic()
        self.state.last_status = "running"

    def _r_thinking(self, rec: dict) -> None:
        g = glyphs()
        self.state.thinking_seen = True
        lines = sanitize(rec.get("delta")).strip().split("\n")
        self.state.blocks.append({"kind": "thinking", "text": "\n".join(lines)})
        shown = lines[:THINK_LINES]
        body = [Text(f"{g.thinking} " + shown[0], style="thinking")]
        body += [Text("  " + ln, style="thinking") for ln in shown[1:]]
        if len(lines) > THINK_LINES:
            body.append(Text(f"  {g.ellipsis} +{len(lines) - THINK_LINES} строк "
                             f"(/expand {len(self.state.blocks)})", style="muted"))
        self._bossman(body)

    def _r_assistant(self, rec: dict) -> None:
        self.state.blocks.append({"kind": "text", "text": rec.get("delta") or ""})
        self._bossman(format_answer(rec.get("delta") or ""))

    def _r_assistant_message(self, rec: dict) -> None:
        self.state.blocks.append({"kind": "answer", "text": rec.get("text") or ""})
        self._bossman(format_answer(rec.get("text") or ""))

    def _r_tool_use(self, rec: dict) -> None:
        g = glyphs()
        name = sanitize(rec.get("name"))
        args = args_preview(rec.get("input"), max(30, self.width - LABEL_WIDTH - len(name) - 8))
        self.state.tools[str(rec.get("id"))] = ToolLine(name=name, args=args, started=time.monotonic(),
                                                        step=rec.get("step"))
        t = Text(f"{g.tool} ", style="tool.pending")
        t.append(name, style="tool")
        t.append(f"({args})", style="tool.args")
        self._bossman([t])

    def _r_tool_result(self, rec: dict) -> None:
        g = glyphs()
        line = self.state.tools.get(str(rec.get("tool_use_id")))
        if line is not None:
            line.done = True
        err = bool(rec.get("is_error"))
        self.state.blocks.append({"kind": "tool", "name": rec.get("name"),
                                  "text": rec.get("content") or rec.get("summary") or ""})
        t = Text(f"  {g.result} ", style="muted")
        t.append(f"{g.fail if err else g.ok} ", style="tool.err" if err else "tool.ok")
        t.append(sanitize(rec.get("summary") or ("ошибка" if err else "готово"), keep_newlines=False),
                 style="tool.err" if err else "text")
        if rec.get("duration_ms") is not None:
            t.append(f"  ({human_duration(rec['duration_ms'])})", style="muted")
        body = [t]
        content = sanitize(rec.get("content") or "").strip("\n")
        if self.verbose and content:
            for ln in content.split("\n")[:RESULT_LINES]:
                body.append(Text("     " + ln, style="muted"))
            extra = len(content.split("\n")) - RESULT_LINES
            if extra > 0:
                body.append(Text(f"     {g.ellipsis} +{extra} строк (/expand {len(self.state.blocks)})",
                                 style="muted"))
        self._bossman(body)

    def _r_tool_denied(self, rec: dict) -> None:
        g = glyphs()
        t = Text(f"{g.fail} ", style="tool.err")
        t.append(sanitize(rec.get("name")), style="tool")
        t.append(f" отклонён политикой: {sanitize(rec.get('reason'))}", style="value.warn")
        self._bossman([t])

    def _r_approval_required(self, rec: dict) -> None:
        g = glyphs()
        self.state.pending_approval = rec.get("approval_id")
        self.state.blocks.append({"kind": "approval", "text": rec.get("preview") or ""})
        preview = sanitize(rec.get("preview")).strip().split("\n")
        body = [Text(f"{g.approval} требуется разрешение #{rec.get('approval_id')} "
                     f"({sanitize(rec.get('kind'))})", style="approval")]
        body += [Text("  " + ln, style="text") for ln in preview[:8]]
        if len(preview) > 8:
            body.append(Text(f"  {g.ellipsis} +{len(preview) - 8} строк — [d] подробнее", style="muted"))
        self._bossman(body)

    def _r_approval_decided(self, rec: dict) -> None:
        g = glyphs()
        ok = rec.get("status") == "approved"
        t = Text(f"{g.ok if ok else g.fail} разрешение #{rec.get('approval_id')}: "
                 f"{'одобрено' if ok else sanitize(rec.get('status'))}",
                 style="tool.ok" if ok else "value.warn")
        if rec.get("by"):
            t.append(f" ({sanitize(rec['by'])})", style="muted")
        self._bossman([t])
        if self.state.pending_approval == rec.get("approval_id"):
            self.state.pending_approval = None

    def _r_memory(self, rec: dict) -> None:
        g = glyphs()
        sub = rec.get("subtype")
        style = "note" if sub != "skipped" else "muted"
        self._bossman([Text(f"{g.note} {sanitize(rec.get('message'))}", style=style)])

    def _r_skill(self, rec: dict) -> None:
        g = glyphs()
        ids = ", ".join(sanitize(i) for i in rec.get("ids") or [])
        self._bossman([Text(f"{g.note} навыки: {ids}", style="note")])

    def _r_usage(self, rec: dict) -> None:
        self.state.usage = rec

    def _r_evaluation(self, rec: dict) -> None:
        g = glyphs()
        verdict = sanitize(rec.get("verdict"))
        self.state.verdict = verdict
        ok = verdict == "PASS"
        t = Text(f"{g.ok if ok else g.approval} проверка: {verdict}", style="tool.ok" if ok else "value.warn")
        if rec.get("reasons"):
            t.append(f" — {sanitize(rec['reasons'], keep_newlines=False)[:200]}", style="muted")
        self._bossman([t])

    def _r_fallback(self, rec: dict) -> None:
        self._bossman([Text(f"{glyphs().approval} запасной путь ({sanitize(rec.get('what'))}): "
                            f"{sanitize(rec.get('detail'), keep_newlines=False)}", style="value.warn")])

    def _r_log(self, rec: dict) -> None:
        level = rec.get("level")
        if level not in ("warn", "error") and not self.verbose:
            return
        g = glyphs()
        style = "error" if level == "error" else "value.warn" if level == "warn" else "muted"
        self._bossman([Text(f"{g.approval if level != 'info' else g.dot} "
                            f"{sanitize(rec.get('message'), keep_newlines=False)}", style=style)])

    def _r_stream(self, rec: dict) -> None:
        sub = rec.get("subtype")
        if sub == "reconnected":
            self.note(f"(поток событий восстановлен с курсора {rec.get('cursor')})")
        elif sub in ("disconnected", "error"):
            self.note("(поток событий недоступен — слежу за статусом задачи)", "value.warn")

    def _r_result(self, rec: dict) -> None:
        g = glyphs()
        state = rec.get("task_state")
        style = {"PASS": "ok", "FAIL": "error", "STOPPED": "value.off", "BLOCKED": "error",
                 "DISCONNECTED": "error"}.get(state, "value.warn")
        usage = rec.get("usage") or {}
        parts = [f"задача {rec.get('task_id')}"]
        if rec.get("run_id"):
            parts.append(f"run {rec['run_id']}")
        parts.append(human_duration(rec.get("duration_ms")))
        if usage.get("tokens_in") or usage.get("tokens_out"):
            parts.append(f"{human_tokens(usage.get('tokens_in'))}→{human_tokens(usage.get('tokens_out'))} tok")
        parts.append(money(usage.get("cost_usd")))
        if rec.get("model"):
            parts.append(sanitize(rec["model"]))
        if rec.get("model_kind") == "MOCK_MODEL":
            parts.append("MOCK_MODEL")
        if self.state.verdict:
            parts.append(f"verifier: {self.state.verdict}")
        label = f" {state} "
        info = " · ".join(parts)
        line = Text(g.rule * 2, style="border")
        line.append(label, style=style)
        line.append(g.rule * 1, style="border")
        line.append(f" {info} ", style="footer")
        pad = self.width - line.cell_len
        if pad > 0 and not self.plain:
            line.append(g.rule * pad, style="border")
        if state == "PASS" and rec.get("result") and not any(
                b.get("kind") == "answer" for b in self.state.blocks):
            self._bossman(format_answer(rec["result"]))
        if rec.get("error") and state != "PASS":
            self._bossman([Text(f"{g.fail} {sanitize(rec['error'])}", style="error")])
        if rec.get("note"):
            self.note(f"  {rec['note']}")
        self.print(line)

    # -- /expand ------------------------------------------------------------

    def expand(self, index: int | None) -> None:
        blocks = self.state.blocks
        if not blocks:
            self.note("разворачивать нечего")
            return
        i = (index or len(blocks)) - 1
        if not 0 <= i < len(blocks):
            self.note(f"нет блока {index}; всего {len(blocks)}")
            return
        b = blocks[i]
        self.print(Text(f"[{i + 1}] {b.get('kind')} {sanitize(b.get('name') or '')}", style="label"))
        for ln in sanitize(b.get("text")).split("\n"):
            self.print(Text("  " + ln, style="thinking" if b.get("kind") == "thinking" else "text"))


def print_json_line(obj: dict, stream=None) -> None:
    """Machine output: one JSON object per line, UTF-8, nothing else."""
    stream = stream or sys.stdout
    stream.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
    stream.flush()
