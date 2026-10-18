"""War & Procurement Terminal inside Bossman: a thin loopback proxy plus read-only tools for the models.

The terminal is a separate app (apps/war-terminal). Bossman influences it ONLY through its versioned API, with the ANALYST role:
it can analyse a tender (dry-run), check compliance gates, estimate cashflow, draft a bid checklist and read conflict news.
It can NOT decide approvals, change the company profile, sign gate reviews, submit bids or contact counterparties: those stay with
the human owner in the terminal UI (owner token). The analyst token is read server-side and never returned or logged.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..tools import REGISTRY, ToolResult, ToolSpec
from . import Feature
from . import apps_control

router = APIRouter(prefix="/war-terminal", tags=["war-terminal"])
APP_ID = "war-terminal"
DEFAULT_PORT = 8830
TIMEOUT = httpx.Timeout(60.0, connect=2.0)
OUT_LIMIT = 6000


def _base() -> str:
    override = os.environ.get("BOSSMAN_WAR_TERMINAL_URL")           # tests; must still be loopback
    if override:
        if not override.startswith(("http://127.0.0.1:", "http://localhost:")):
            raise HTTPException(500, {"code": "WT_BAD_OVERRIDE", "message": "loopback only"})
        return override.rstrip("/")
    return f"http://127.0.0.1:{DEFAULT_PORT}"


def _token() -> str:
    tok = os.environ.get("BOSSMAN_WAR_TERMINAL_TOKEN")
    if tok:
        return tok
    data = Path(os.environ.get("WAR_TERMINAL_DATA") or Path.home() / ".war-terminal")
    try:
        return json.loads((data / "tokens.json").read_text(encoding="utf-8"))["analyst"]
    except (OSError, KeyError, ValueError) as exc:
        raise HTTPException(503, {"code": "WT_NO_TOKEN", "message": "нет токена аналитика War Terminal (tokens.json); запустите приложение один раз"}) from exc


async def _call(method: str, path: str, body: dict | None = None, params: dict | None = None):
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, trust_env=False) as c:
            r = await c.request(method, _base() + "/api/v1" + path, json=body, params=params, headers={"Authorization": f"Bearer {_token()}"})
    except httpx.HTTPError as exc:
        raise HTTPException(503, {"code": "WT_SERVICE_DOWN", "message": f"War Terminal не отвечает: {type(exc).__name__}",
                                  "hint": "запустите приложение (кнопка «Запустить сервис» или scripts/Start-WarTerminal.ps1)"}) from exc
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text[:200]
        raise HTTPException(r.status_code, detail)
    return r.json()


@router.get("/status")
async def status(request: Request):
    svc = request.app.state.svc
    try:
        health = await _call("GET", "/health")
        up = True
    except HTTPException:
        health, up = None, False
    return {"service_up": up, "health": health, "app_id": APP_ID, "process": apps_control.process_info(APP_ID, svc.settings.data_dir),
            "bossman_role": "analyst", "owner_only": ["approvals.decide", "company.profile", "gate reviews", "learning decisions"]}


@router.post("/service/start")
async def service_start(request: Request):
    svc = request.app.state.svc
    pol = await apps_control.policy(svc)
    if not pol["enabled"]:
        raise HTTPException(409, {"code": "APPS_CONTROL_OFF", "message": "управление приложениями выключено политикой владельца", "hint": pol.get("hint")})
    return await apps_control.start_app(APP_ID, svc.settings.data_dir, svc=svc)


@router.get("/tenders")
async def tenders(request: Request):
    allowed = {"status", "complexity", "tier", "jurisdiction", "category", "country", "q", "include_demo", "limit", "offset"}
    return await _call("GET", "/tenders", params={k: v for k, v in request.query_params.items() if k in allowed})


@router.get("/tenders/{key:path}/checklist")
async def checklist(key: str):
    return await _call("GET", f"/tenders/{key}/checklist")


@router.get("/tenders/{key:path}")
async def tender(key: str):
    return await _call("GET", f"/tenders/{key}")


class AnalyzeBody(BaseModel):
    tender_key: str = Field(min_length=3, max_length=200)
    use_ai: bool = False


@router.post("/analyze")
async def analyze(body: AnalyzeBody):
    """Dry-run only: nothing is persisted in the terminal."""
    return await _call("POST", "/assessments", {"tender_key": body.tender_key, "dry_run": True, "use_ai": body.use_ai})


@router.post("/cashflow")
async def cashflow(body: dict):
    return await _call("POST", "/finance/estimate", body)


@router.get("/conflicts")
async def conflicts(include_demo: bool = True):
    return await _call("GET", "/conflicts", params={"include_demo": include_demo})


@router.get("/sources")
async def sources():
    return await _call("GET", "/sources/status")


@router.get("/approvals")
async def approvals():
    """Read-only. Deciding an approval is the owner's act in the terminal UI, never Bossman's."""
    return await _call("GET", "/approvals")


class RefreshBody(BaseModel):
    source: str = Field(default="ted", pattern=r"^(ted|usaspending|gdelt)$")
    country: str | None = "CZE"
    keyword: str | None = None
    days: int = Field(default=30, ge=1, le=365)
    confirm_live: bool = False


@router.post("/refresh")
async def refresh(body: RefreshBody):
    if not body.confirm_live:
        raise HTTPException(403, {"code": "LIVE_NEEDS_OWNER_CONFIRM", "message": "загрузка внешних источников — только с явным подтверждением владельца"})
    data = body.model_dump()
    data.pop("confirm_live")
    return await _call("POST", "/ingest", data)


# ------------------------------------------------------------------ free-only one-shot chat for the terminal's translation
class FreeChatBody(BaseModel):
    model_id: int
    messages: list[dict] = Field(min_length=1, max_length=4)
    max_tokens: int = Field(default=2000, ge=64, le=4000)


def free_chat_refusal(model: dict | None, provider: dict | None) -> str:
    """Why this model may NOT serve the terminal's translation ('' = allowed): free route by Bossman's own record only, never local/paid."""
    from ..provider_governance import is_local_url
    if model is None or provider is None:
        return "модель не найдена"
    caps = model.get("caps") or {}
    pi, po = model.get("price_in"), model.get("price_out")
    zero = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v == 0     # noqa: E731
    if not (zero(pi) and zero(po)) or not caps.get("free_tier") or caps.get("paid"):
        return "маршрут не бесплатный по записи Bossman (нужны price 0/0 и caps.free_tier)"
    if is_local_url(provider.get("base_url") or "") or (model.get("kind") or provider.get("kind")) == "local":
        return "локальные модели для перевода запрещены владельцем (GPU занят)"
    return ""


@router.post("/free-chat")
async def free_chat(body: FreeChatBody, request: Request):
    """One non-streaming completion on a FREE cloud route. The provider key stays inside Bossman; the reply carries the provider's
    own usage (incl. cost when the provider reports it) so the caller can drop a reply that cost money."""
    svc = request.app.state.svc
    if any(m.get("role") not in ("system", "user") or not isinstance(m.get("content"), str) for m in body.messages) \
            or sum(len(m["content"]) for m in body.messages) > 24000:
        raise HTTPException(422, {"code": "WT_BAD_MESSAGES", "message": "только system/user, до 24000 символов"})
    model = await svc.registry.get_model(body.model_id)
    provider = next((p for p in await svc.registry.list_providers() if model and p["id"] == model.get("provider_id")), None)
    why = free_chat_refusal(model, provider)
    if why:
        raise HTTPException(403, {"code": "WT_ROUTE_NOT_FREE", "message": why})
    adapter, row = await svc.registry.adapter_for(body.model_id)
    try:
        res = await adapter.chat(row["name"], body.messages, max_tokens=body.max_tokens, temperature=0.1)
    except Exception as exc:                                    # noqa: BLE001 - provider text may echo request data: classify only
        status = getattr(exc, "status_code", None)
        raise HTTPException(status if status in (429, 503) else 502, {"code": "WT_ROUTE_FAILED", "message": f"{type(exc).__name__}"}) from None
    usage = dict((res.provider_meta or {}).get("usage") or {})
    return {"text": res.text, "model": row.get("alias"), "usage": {"cost": usage.get("cost"), "tokens_in": res.tokens_in, "tokens_out": res.tokens_out},
            "finish": res.finish}


# ------------------------------------------------------------------ tools for Bossman's models (read-only)
def _result(name: str, data) -> ToolResult:
    text = json.dumps(data, ensure_ascii=False, default=str)
    cut = len(text) > OUT_LIMIT
    return ToolResult(content=text[:OUT_LIMIT], one_line=f"{name}: ок", truncated=cut, external=True, data={"result_keys": list(data)[:20] if isinstance(data, dict) else None})


def _fail(name: str, exc: HTTPException) -> ToolResult:
    return ToolResult(content=json.dumps(exc.detail, ensure_ascii=False, default=str)[:1500], one_line=f"{name}: ошибка {exc.status_code}", error=True)


def _tool(name: str, description: str, schema: dict, required: list[str], runner) -> ToolSpec:
    async def handler(args: dict, ctx) -> ToolResult:
        try:
            return _result(name, await runner(args))
        except HTTPException as exc:
            return _fail(name, exc)
    return ToolSpec(name=name, description=description, handler=handler, input_schema=schema, required=required, category="read", source="builtin",
                    default_effect="auto", timeout_seconds=90.0, idempotent=True, external_output=True)


def tool_specs() -> list[ToolSpec]:
    key = {"tender_key": {"type": "string", "description": "ключ тендера, например EU:658545-2026"}}

    async def analyze_tender(a):
        return await _call("POST", "/assessments", {"tender_key": a["tender_key"], "dry_run": True, "use_ai": False})

    async def check_compliance(a):
        r = await _call("POST", "/assessments", {"tender_key": a["tender_key"], "dry_run": True, "use_ai": False})
        return {"tender_key": r["tender_key"], "compliance_status": r["compliance_status"], "gates": r["gates"],
                "note": "это проверка по правилам; юридическое заключение даёт человек"}

    async def summarize_conflict(a):
        return await _call("GET", "/conflicts/news", params={"query": a["query"]})

    async def estimate_cashflow(a):
        return await _call("POST", "/finance/estimate", a["inputs"])

    async def draft_bid_checklist(a):
        return await _call("GET", f"/tenders/{a['tender_key']}/checklist")

    return [
        _tool("war_terminal.analyze_tender", "Оценить тендер (dry-run): проверки, объяснимый fit, класс. Это НЕ вероятность победы.", key, ["tender_key"], analyze_tender),
        _tool("war_terminal.check_compliance", "Юридические проверки тендера по правилам; неизвестное = REVIEW_REQUIRED.", key, ["tender_key"], check_compliance),
        _tool("war_terminal.summarize_conflict", "Загруженные новости по запросу (упоминания в СМИ, не события).", {"query": {"type": "string"}}, ["query"], summarize_conflict),
        _tool("war_terminal.estimate_cashflow", "Расчёт landed cost, маржи, cashflow и стресса задержки оплаты по явным входным данным.",
              {"inputs": {"type": "object", "description": "поля FinanceIn: purchase, bid_price, fx, ..."}}, ["inputs"], estimate_cashflow),
        _tool("war_terminal.draft_bid_checklist", "Черновик чек-листа по тендеру. Подача заявки приложением невозможна.", key, ["tender_key"], draft_bid_checklist),
    ]


async def _setup(svc) -> None:
    for spec in tool_specs():
        REGISTRY.register(spec)


FEATURE = Feature(name="war_terminal", router=router, setup=_setup)
