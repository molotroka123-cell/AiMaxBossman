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


async def _call_text(method: str, path: str, params: dict | None = None) -> str:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, trust_env=False) as c:
            r = await c.request(method, _base() + "/api/v1" + path, params=params, headers={"Authorization": f"Bearer {_token()}"})
    except httpx.HTTPError as exc:
        raise HTTPException(503, {"code": "WT_SERVICE_DOWN", "message": f"War Terminal не отвечает: {type(exc).__name__}"}) from exc
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text[:200]
        raise HTTPException(r.status_code, detail)
    return r.text


@router.get("/contacts")
async def contacts_list(request: Request):
    allowed = {"status", "q"}
    return await _call("GET", "/contacts", params={k: v for k, v in request.query_params.items() if k in allowed})


@router.get("/contacts/{cid}/matches")
async def contact_matches(cid: int, request: Request):
    allowed = {"k", "status"}
    return await _call("GET", f"/contacts/{cid}/matches", params={k: v for k, v in request.query_params.items() if k in allowed})


@router.get("/contacts/{cid}/pitch")
async def contact_pitch(cid: int, recommendations_only: bool = False):
    md = await _call_text("GET", f"/contacts/{cid}/pitch", params={"recommendations_only": str(recommendations_only).lower()})
    return {"contact_id": cid, "recommendations_only": recommendations_only, "markdown": md}


@router.get("/monetization/opportunities")
async def monetization_opportunities(request: Request):
    allowed = {"k_per_contact", "max_items", "fee_pct", "target_margin_pct"}
    return await _call("GET", "/monetization/opportunities", params={k: v for k, v in request.query_params.items() if k in allowed})


@router.get("/monetization/playbook")
async def monetization_playbook(request: Request):
    allowed = {"k_per_contact", "max_items", "fee_pct", "target_margin_pct"}
    md = await _call_text("GET", "/monetization/playbook", params={k: v for k, v in request.query_params.items() if k in allowed})
    return {"markdown": md}


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

    async def list_forum_contacts(a):
        params = {k: a[k] for k in ("status", "q") if a.get(k)}
        return await _call("GET", "/contacts", params=params)

    async def match_supplier_tenders(a):
        cid = int(a["contact_id"])
        params = {"k": int(a.get("k", 10)), "status": a.get("status", "open")}
        return await _call("GET", f"/contacts/{cid}/matches", params=params)

    async def draft_supplier_pitch(a):
        cid = int(a["contact_id"])
        rec_only = bool(a.get("recommendations_only", False))
        md = await _call_text("GET", f"/contacts/{cid}/pitch", params={"recommendations_only": str(rec_only).lower()})
        return {"contact_id": cid, "recommendations_only": rec_only, "markdown": md}

    async def list_tenders(a):
        params = {"status": a.get("status", "open"), "limit": max(1, min(int(a.get("limit", 10)), 50))}
        for k in ("q", "country", "jurisdiction", "category"):
            if a.get(k):
                params[k] = a[k]
        r = await _call("GET", "/tenders", params=params)
        rows = r.get("items", []) if isinstance(r, dict) else (r or [])
        keep = ("key", "official_notice_id", "title", "buyer", "country", "status", "deadline", "estimated_value", "currency", "source_id", "source_url")
        return {"total": r.get("total") if isinstance(r, dict) else len(rows), "items": [{k: t.get(k) for k in keep} for t in rows[:params["limit"]]]}

    async def monetization_radar(a):
        params = {
            "k_per_contact": int(a.get("k_per_contact", 5)),
            "max_items": int(a.get("max_items", 25)),
            "fee_pct": float(a.get("fee_pct", 3.0)),
            "target_margin_pct": float(a.get("target_margin_pct", 15.0)),
        }
        return await _call("GET", "/monetization/opportunities", params=params)

    return [
        _tool("war_terminal.analyze_tender", "Оценить тендер (dry-run): проверки, объяснимый fit, класс. Это НЕ вероятность победы.", key, ["tender_key"], analyze_tender),
        _tool("war_terminal.check_compliance", "Юридические проверки тендера по правилам; неизвестное = REVIEW_REQUIRED.", key, ["tender_key"], check_compliance),
        _tool("war_terminal.summarize_conflict", "Загруженные новости по запросу (упоминания в СМИ, не события).", {"query": {"type": "string"}}, ["query"], summarize_conflict),
        _tool("war_terminal.estimate_cashflow", "Расчёт landed cost, маржи, cashflow и стресса задержки оплаты по явным входным данным.",
              {"inputs": {"type": "object", "description": "поля FinanceIn: purchase, bid_price, fx, ..."}}, ["inputs"], estimate_cashflow),
        _tool("war_terminal.draft_bid_checklist", "Черновик чек-листа по тендеру. Подача заявки приложением невозможна.", key, ["tender_key"], draft_bid_checklist),
        _tool("war_terminal.list_forum_contacts", "Список поставщиков и контактов с выставок/форумов (Future Military Forum 2026 и др.) с CPV и продуктами.",
              {"q": {"type": "string", "description": "поиск по компании/продуктам/ключевым словам"}, "status": {"type": "string", "description": "new|contacted|meeting|partner|declined"}}, [], list_forum_contacts),
        _tool("war_terminal.match_supplier_tenders", "Подобрать открытые тендеры под конкретного поставщика из контактов (по CPV, ключевым словам и отзывам владельца).",
              {"contact_id": {"type": "integer", "description": "ID контакта поставщика"}, "k": {"type": "integer", "description": "макс. тендеров (1..50)"}, "status": {"type": "string", "description": "open|all"}}, ["contact_id"], match_supplier_tenders),
        _tool("war_terminal.draft_supplier_pitch", "Сформировать готовый Markdown-черновик коммерческого предложения (.md) для поставщика с топ-5 открытыми тендерами (без персональных данных).",
              {"contact_id": {"type": "integer", "description": "ID контакта поставщика"}, "recommendations_only": {"type": "boolean", "description": "только без REVIEW_REQUIRED"}}, ["contact_id"], draft_supplier_pitch),
        _tool("war_terminal.list_tenders", "Список тендеров War Terminal (по умолчанию открытые): ключ, название, заказчик, срок, статус, ссылка source_url на официальное уведомление (TED/Prozorro/NEN) — её открывать browser.open.",
              {"status": {"type": "string", "description": "open|closed|all"}, "limit": {"type": "integer", "description": "1..50"}, "q": {"type": "string"}, "country": {"type": "string"}, "jurisdiction": {"type": "string"}, "category": {"type": "string"}}, [], list_tenders),
        _tool("war_terminal.monetization_radar", "Радар заработка на военных/оборонных тендерах: ранжирует пары (поставщик форума <-> открытый тендер) и считает 3 модели дохода (агентская комиссия 2-5%, поставка back-to-back с маржой 15% и проверкой лимита $50k USD, локальный субподряд 5%).",
              {"k_per_contact": {"type": "integer"}, "max_items": {"type": "integer"}, "fee_pct": {"type": "number"}, "target_margin_pct": {"type": "number"}}, [], monetization_radar),
    ]


async def _setup(svc) -> None:
    for spec in tool_specs():
        REGISTRY.register(spec)


FEATURE = Feature(name="war_terminal", router=router, setup=_setup)

