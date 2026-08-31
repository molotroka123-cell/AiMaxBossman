"""V2.6 модуль I — тонкая обвязка research-движка под toolkit.

НЕ регистрирует ничего в REGISTRY и НЕ импортирует toolkit на import модуля:
production-wiring делается позже — make_research_tool принимает класс ToolDef
параметром (или отдаёт готовый spec-словарь). rights="read": результат research
пройдёт стандартную границу ingest (external-data header + ingest_guard в
runner) как любой другой внешний контент.
"""
from __future__ import annotations

from typing import Awaitable, Callable

from .engine import ResearchEngine, citations
from .models import DEEP, MODES, QUICK, ResearchReport, Source


def _parse_sources(raw: list) -> list[Source]:
    out: list[Source] = []
    for item in raw or []:
        if isinstance(item, Source):
            out.append(item)
        elif isinstance(item, dict):
            ref = str(item.get("ref") or item.get("url") or "").strip()
            if ref:
                out.append(Source(url_or_ref=ref,
                                  kind=str(item.get("kind", "web")),
                                  trust=float(item.get("trust", 0.5))))
        elif isinstance(item, str) and item.strip():
            out.append(Source(url_or_ref=item.strip()))
    return out


def render_report(report: ResearchReport) -> str:
    """Текстовый рендер отчёта с полной картой цитирования."""
    lines = [f"Вопрос: {report.question}",
             f"Режим: {report.mode.name}; раундов: {report.rounds_used}"]
    for n, entry in enumerate(citations(report), 1):
        refs = "; ".join(f"{ref} @ {ts:.0f}" for ref, ts in entry["sources"])
        lines.append(f"[{n}] {entry['claim']} (источники: {refs})")
    for c in report.contradictions:
        lines.append(f"! {c}")
    for q in report.unanswered:
        lines.append(f"? без ответа: {q}")
    for e in report.fetch_errors:
        lines.append(f"× fetch: {e}")
    return "\n".join(lines)


async def research_handler(args: dict, ctx=None, *,
                           fetcher: Callable[[Source], Awaitable[str]],
                           mode_default: str = "quick"):
    """Обработчик в форме toolkit-Handler(args, ctx). DEEP никогда не
    выбирается по умолчанию — только явный args["mode"] == "deep"."""
    question = str(args.get("question", "")).strip()
    sources = _parse_sources(args.get("sources") or [])
    requested = str(args.get("mode") or "").strip().lower()
    mode = MODES.get(requested) or MODES.get(mode_default.lower()) or QUICK
    if mode is DEEP and requested != "deep":
        mode = QUICK    # DEEP не бывает дефолтом даже через mode_default

    report = await ResearchEngine(fetcher).run(question, sources, mode)
    text = render_report(report)
    one_line = (f"research: {len(report.claims)} claim(ов), "
                f"{len(report.sources)} источник(ов), режим {mode.name}")
    try:    # ToolResult — лениво: toolkit не импортируется на import модуля
        from ..toolkit import ToolResult
        return ToolResult(content=text, one_line=one_line)
    except Exception:  # noqa: BLE001 — вне toolkit-окружения отдаём текст
        return text


def make_research_tool(fetcher: Callable[[Source], Awaitable[str]], *,
                       tool_def_cls=None, mode_default: str = "quick"):
    """Фабрика регистрации: spec словарём или готовый ToolDef, если передан
    класс. Никакой записи в REGISTRY здесь — wiring делает вызывающий."""

    async def handler(args: dict, ctx=None):
        return await research_handler(args, ctx, fetcher=fetcher,
                                      mode_default=mode_default)

    spec = {
        "name": "research.deep",
        "description": ("Deep research по вопросу над заданными источниками: "
                        "evidence с provenance, противоречия, citations. "
                        "Режимы quick/standard; deep — только явно."),
        "rights": "read",
        "handler": handler,
        "params": {
            "question": {"type": "string", "description": "исследуемый вопрос"},
            "sources": {"type": "array",
                        "description": "источники: строки-ref или {ref,kind,trust}"},
            "mode": {"type": "string", "enum": ["quick", "standard", "deep"]},
        },
        "required": ["question", "sources"],
    }
    return tool_def_cls(**spec) if tool_def_cls is not None else spec
