"""Separate open-news skill tools; no tick, cron, installer or direct execution API."""
from __future__ import annotations

import json

from fastapi import APIRouter

from ..open_news_skill import (UPSTREAM_SHA, NewsFetchError, NewsInputError,
                               network_blocked, process_articles, search_news, search_parameters)
from ..tools import REGISTRY, ToolContext, ToolResult, ToolSpec
from . import Feature

router = APIRouter()


def _result(data: dict) -> ToolResult:
    return ToolResult(content=json.dumps(data, ensure_ascii=False), data=data,
                      one_line=f"open-news: {data['status']}", external=True,
                      truncated=data.get("truncated", False),
                      more="Повторите с меньшей подборкой или более узким запросом.")


async def _process(args: dict, ctx: ToolContext) -> ToolResult:
    try:
        return _result(process_articles(args))
    except NewsInputError as exc:
        return ToolResult(content=str(exc), error=True, one_line="open-news: invalid input")


async def _search(args: dict, ctx: ToolContext) -> ToolResult:
    try:
        return _result(await search_news(args))
    except (NewsInputError, NewsFetchError) as exc:
        return ToolResult(content=str(exc), error=True, one_line="open-news: search not completed")


def _search_approval(args: dict) -> tuple[str, str]:
    # No model-provided approved/allow_network bit. The engine stores and binds
    # the owner's approval to these exact arguments and tool implementation.
    return "ask", "Поисковый текст будет отправлен news.google.com; подтвердите конкретный запрос."


async def _deny_offline(args: dict, ctx: ToolContext) -> str | None:
    if network_blocked():
        return "offline_mode_blocks_news_search"
    meta = ctx.task.get("meta") if isinstance(ctx.task, dict) else None
    if isinstance(meta, dict) and meta.get("skill") == "open-news":
        inputs = meta.get("skill_input")
        if not isinstance(inputs, dict) or inputs.get("mode") != "search":
            return "supplied_news_skill_cannot_acquire_external_data"
        expected = {k: v for k, v in inputs.items() if k not in {"mode", "articles"}}
        try:
            if (search_parameters(args) != search_parameters(expected)
                    or args.get("limit", 10) != expected.get("limit", 10)):
                return "search_arguments_differ_from_skill_request"
        except NewsInputError:
            return "invalid_search_skill_request"
    return None


@router.get("/open-news/status")
async def status():
    return {"skill_id": "open-news", "upstream_sha": UPSTREAM_SHA,
            "integration": "reviewed_subset", "tools": ["open_news.process", "open_news.search"],
            "offline_processing": True, "search_requires_approval": True,
            "network_blocked": network_blocked(), "background_running": False,
            "live_verified": False,
            "not_integrated": ["ddgs", "crawler", "article_download", "rss_url_discovery",
                               "google_redirect_resolution", "javascript", "streaming", "tui"]}


async def setup(svc) -> None:
    REGISTRY.register(ToolSpec(
        name="open_news.process", description="Локально отфильтровать, убрать точные URL-дубли и сократить переданные новости; сеть не вызывается.",
        handler=_process, category="read", default_effect="auto", timeout_seconds=10,
        source="builtin", external_output=True, required=["articles"],
        input_schema={
            "articles": {"type": "array", "maxItems": 40, "items": {"type": "object",
                "required": ["url"], "additionalProperties": False,
                "properties": {k: {"type": "string", "maxLength": n} for k, n in {
                    "url": 2048, "title": 500, "description": 4000, "text": 8000,
                    "source": 200, "published_at": 100}.items()} }},
            "query": {"type": "string", "maxLength": 240},
            "query_mode": {"type": "string", "enum": ["any", "all", "exact_phrase"]},
            "exclude_terms": {"type": "array", "maxItems": 10,
                              "items": {"type": "string", "maxLength": 80}},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            "sentence_count": {"type": "integer", "minimum": 1, "maximum": 5},
        }))
    REGISTRY.register(ToolSpec(
        name="open_news.search", description="Один подтверждаемый поиск Google News RSS. Возвращает заголовки/сниппеты и ссылки, не скачивает статьи; никаких фоновых подписок.",
        handler=_search, category="read", permission="browser.read", default_effect="ask",
        timeout_seconds=25, source="builtin", external_output=True,
        effect_hook=_search_approval, hook_is_floor=True, context_deny=_deny_offline,
        required=["query"], input_schema={
            "query": {"type": "string", "minLength": 1, "maxLength": 240},
            "query_mode": {"type": "string", "enum": ["any", "all", "exact_phrase"]},
            "language": {"type": "string", "pattern": "^[a-z]{2}$"},
            "country": {"type": "string", "pattern": "^[A-Z]{2}$"},
            "time_limit": {"type": "string", "enum": ["d", "w", "m"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        }))


FEATURE = Feature(name="open_news", router=router, setup=setup)
