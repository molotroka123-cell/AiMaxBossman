"""String web-access MCP server (https://portal.usestring.ai/skill.md).

The repository holds only the endpoint TEMPLATE and the secret NAME. The key
lives in Bossman's encrypted vault (`settings_kv`, Fernet) and is substituted
into the URL in memory by the MCP runtime at connection time.

Every call goes through `WebAccess`: policy -> rate limit -> MCP call ->
size cap + untrusted fence -> one audit line (UTC time, tool, domain only).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import policy
from .policy import PolicyRefusal

SERVER_ID = "string"
SECRET_NAME = "string_web_access_key"
SETTING_KEY = "secret." + SECRET_NAME
ENDPOINT_TEMPLATE = "https://mcp.usestring.ai/{secret:" + SECRET_NAME + "}/v1/mcp"
TOOL_FETCH = "web_access_fetch"
TOOL_SEARCH = "web_access_search"
TOOL_SITEMAP = "web_access_sitemap"
ALLOWED_TOOLS = (TOOL_FETCH, TOOL_SEARCH, TOOL_SITEMAP)
CALL_TIMEOUT_S = 60.0
_QUOTA_MARKERS = ("429", "quota", "rate limit", "too many", "insufficient", "payment", "402", "credits")


@dataclass(slots=True)
class WebResult:
    ok: bool
    status: str                 # ok | refused | no_key | unavailable | over_quota | error
    tool: str
    domain: str = "-"
    text: str = ""              # fenced, capped, UNTRUSTED
    truncated: bool = False
    injection_suspected: bool = False
    latency_ms: int = 0
    detail: str = ""            # safe to show (redacted)
    raw: str = ""               # untrusted, capped; for in-process parsing only, never in as_dict()

    def as_dict(self) -> dict:
        return {"ok": self.ok, "status": self.status, "tool": self.tool, "domain": self.domain,
                "text": self.text, "truncated": self.truncated,
                "injection_suspected": self.injection_suspected,
                "latency_ms": self.latency_ms, "detail": self.detail}


class WebAccess:
    """Policy-enforcing front for the String MCP server."""

    def __init__(self, *, runtime: Any, key_getter: Callable[[], Awaitable[str | None]],
                 audit_path: Path | None = None, limiter: policy.RateLimiter | None = None,
                 timeout: float = CALL_TIMEOUT_S):
        from ..v2.mcp_hub import MCPServerSpec
        self.runtime = runtime
        self._key_getter = key_getter
        self.limiter = limiter or policy.RateLimiter()
        self.audit = policy.AuditWriter(audit_path)
        self.timeout = timeout
        self.spec = MCPServerSpec(id=SERVER_ID, name=SERVER_ID, transport="http",
                                  url=ENDPOINT_TEMPLATE, timeout_seconds=int(timeout))

    async def has_key(self) -> bool:
        return bool(await self._key_getter())

    async def _call(self, tool: str, args: dict, domain: str) -> WebResult:
        """Everything after argument validation: key -> limiter -> MCP -> fence -> audit."""
        if tool not in ALLOWED_TOOLS:
            raise PolicyRefusal(f"tool {tool} is not allowed")
        key = await self._key_getter()
        if not key:
            self.audit.line(tool, domain, "no_key")
            return WebResult(False, "no_key", tool, domain, detail="String key is not stored in the vault")
        policy.register_secret(key)
        self.limiter.acquire()                                    # PolicyRefusal when over the limit
        started = time.perf_counter()
        try:
            await self.runtime.ensure(self.spec)
            res = await self.runtime.call_tool(SERVER_ID, tool, args, timeout=self.timeout)
        except Exception as exc:                                  # MCPUnavailable / MCPCallError / SDK
            ms = int((time.perf_counter() - started) * 1000)
            detail = policy.redact(f"{type(exc).__name__}: {exc}")[:300]
            status = "over_quota" if any(m in detail.lower() for m in _QUOTA_MARKERS) else "unavailable"
            self.audit.line(tool, domain, status, ms=ms)
            return WebResult(False, status, tool, domain, latency_ms=ms, detail=detail)
        ms = int((time.perf_counter() - started) * 1000)
        raw = res.text or ""
        if res.is_error:
            detail = policy.redact(raw)[:300]
            status = "over_quota" if any(m in detail.lower() for m in _QUOTA_MARKERS) else "error"
            self.audit.line(tool, domain, status, ms=ms)
            return WebResult(False, status, tool, domain, latency_ms=ms, detail=detail)
        fenced, truncated, suspected = policy.fence_untrusted(policy.redact(raw), domain)
        self.audit.line(tool, domain, "ok", ms=ms, chars=len(raw), injection=suspected)
        return WebResult(True, "ok", tool, domain, fenced, truncated, suspected, ms,
                         raw=policy.redact(raw)[:200_000])

    def _refused(self, tool: str, domain: str, exc: PolicyRefusal) -> WebResult:
        self.audit.line(tool, domain, "refused")
        return WebResult(False, "refused", tool, domain, detail=str(exc))

    async def search(self, query: object) -> WebResult:
        try:
            q = policy.check_query(query)
            return await self._call(TOOL_SEARCH, {"query": q}, "-")
        except PolicyRefusal as exc:
            return self._refused(TOOL_SEARCH, "-", exc)

    async def fetch(self, url: object) -> WebResult:
        domain = policy.domain_of(str(url or ""))
        try:
            clean = policy.check_public_url(url)
            return await self._call(TOOL_FETCH, {"url": clean}, domain)
        except PolicyRefusal as exc:
            return self._refused(TOOL_FETCH, domain, exc)

    async def sitemap(self, url: object) -> WebResult:
        domain = policy.domain_of(str(url or ""))
        try:
            clean = policy.check_public_url(url)
            return await self._call(TOOL_SITEMAP, {"url": clean}, domain)
        except PolicyRefusal as exc:
            return self._refused(TOOL_SITEMAP, domain, exc)

    async def guarded_call(self, tool: str, arguments: dict) -> WebResult:
        """Generic entry used by the MCP `call` endpoint: same rules for any String tool."""
        args = dict(arguments or {})
        if tool == TOOL_SEARCH:
            return await self.search(args.get("query"))
        if tool == TOOL_FETCH:
            return await self.fetch(args.get("url"))
        if tool == TOOL_SITEMAP:
            return await self.sitemap(args.get("url"))
        self.audit.line(str(tool)[:60], "-", "refused")
        return WebResult(False, "refused", str(tool)[:60], detail="only web_access_fetch/search/sitemap are allowed")
