"""FastAPI app, API v1. Roles: viewer < analyst < compliance < owner. Every write is audited; risky ones go through the approval ledger.

Never implemented on purpose: bid submission, counterparty contact, purchase of controlled goods (see /actions/*: always 403)."""
from __future__ import annotations

import json
import os
import secrets
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import API_VERSION, VERSION, ai as ai_mod, finance, learning, rules, scoring, sources
from .connectors import demo as demo_conn, demo_conflicts, gdelt, http as http_mod, sam, ted, usaspending
from .store import Store, now

ROLE_RANK = {"viewer": 0, "analyst": 1, "compliance": 2, "owner": 3}
PROFILE_KEYS = {"name", "country", "registered", "vat_id", "sam_uei", "cpv_capabilities", "certificates", "min_prep_days",
                "capital_limit_usd", "base_currency", "past_performance"}
P = f"/api/{API_VERSION}"
UI_DIR = Path(__file__).parent / "ui"


def jd(obj):
    """JSON-safe: Decimal -> str (exact money), recursively."""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: jd(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jd(v) for v in obj]
    return obj


def _load_tokens(data_dir: Path) -> dict[str, str]:
    path = data_dir / "tokens.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    tokens = {r: secrets.token_urlsafe(24) for r in ROLE_RANK}
    path.write_text(json.dumps(tokens), encoding="utf-8")
    return tokens


class IngestIn(BaseModel):
    source: str = Field(pattern=r"^(ted|sam|usaspending|gdelt|demo)$")
    days: int = Field(default=30, ge=1, le=365)
    cpv_prefixes: list[str] | None = None
    country: str | None = "CZE"
    limit: int = Field(default=50, ge=1, le=200)
    keyword: str | None = None


class AssessIn(BaseModel):
    tender_key: str
    dry_run: bool = True
    use_ai: bool = False
    back_to_back: bool = False


class ReviewIn(BaseModel):
    gate_id: str
    decision: str = Field(pattern=r"^(cleared|rejected)$")
    note: str = Field(min_length=5, max_length=1000)
    legal_reference: str | None = None


class ProfilePatch(BaseModel):
    changes: dict


class DecideIn(BaseModel):
    approve: bool
    note: str = Field(default="", max_length=500)


class FinanceIn(BaseModel):
    currency: str = "USD"
    fx: dict[str, float] = {}
    purchase: float
    freight: float = 0
    insurance: float = 0
    duties_pct: float = 0
    certification: float = 0
    financing: float = 0
    risk_reserve_pct: float = 0
    overhead: float = 0
    bid_price: float
    supplier_prepay_pct: float = 30
    delivery_day: int = 30
    acceptance_day: int = 40
    customer_pay_delay_days: int = 30
    vat_import: float = 0
    vat_recovery_days: int = 60
    logistics_cost: float = 0
    buyer_prepay_pct: float = 0
    annual_rate: float = 0.12
    capital_limit_usd: float = 50000
    order_confirmed: bool = False
    supplier_contract_signed: bool = False


class SupplierIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    country: str = Field(min_length=2, max_length=3)
    cpv_prefixes: list[str] = []
    confirmed: bool = False
    source_url: str | None = None
    notes: str = ""


class DealIn(BaseModel):
    title: str
    tender_key: str | None = None
    buyer_country: str
    goods: str
    cpv: list[str] = []
    order_value: float | None = None
    currency: str | None = None
    order_confirmed: bool = False
    supplier: SupplierIn
    supplier_contract_signed: bool = False


class FxIn(BaseModel):
    rates: dict[str, float]
    as_of: str
    source: str = Field(min_length=3)


class SanctionsIn(BaseModel):
    entries: list[str] = Field(min_length=1, max_length=200000)
    source: str = Field(min_length=3)
    list_version: str
    as_of: str


class ConflictIn(BaseModel):
    name: str
    country: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    severity: str = Field(pattern=r"^(low|medium|high)$")
    since: str | None = None
    source_id: str = Field(min_length=2)
    source_url: str = Field(pattern=r"^https?://")
    licence: str = Field(min_length=2)


class ConflictImport(BaseModel):
    items: list[ConflictIn] = Field(min_length=1, max_length=5000)


class DecisionIn(BaseModel):
    tender_key: str
    decision: str = Field(pattern=r"^(go|no_go|watch)$")
    reasons: str = Field(min_length=3, max_length=1000)


class OutcomeIn(BaseModel):
    tender_key: str
    result: str = Field(pattern=r"^(won|lost|withdrawn|not_submitted)$")
    actual_margin_pct: float | None = None
    note: str = ""


def create_app(data_dir: str | Path | None = None, *, live: bool | None = None, tokens: dict | None = None, post=None, get=None,
               chat=None, today: date | None = None) -> FastAPI:
    data_dir = Path(data_dir or os.environ.get("WAR_TERMINAL_DATA") or Path.home() / ".war-terminal")
    data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(data_dir / "war.db")
    live = live if live is not None else os.environ.get("WAR_TERMINAL_LIVE", "").lower() in ("1", "true", "yes")
    tokens = tokens or _load_tokens(data_dir)
    post, get = post or http_mod.post_json, get or http_mod.get_json
    if chat is None and os.environ.get("WAR_TERMINAL_AI_URL") and os.environ.get("WAR_TERMINAL_AI_MODEL"):
        chat = ai_mod.make_chat(os.environ["WAR_TERMINAL_AI_URL"], os.environ["WAR_TERMINAL_AI_MODEL"], os.environ.get("WAR_TERMINAL_AI_KEY"))
    sam_key = os.environ.get("SAM_API_KEY", "")

    app = FastAPI(title="Bossman War & Procurement Terminal", version=VERSION, description="API v1. DEMO data is always marked demo=true.")
    app.state.store, app.state.live, app.state.tokens = store, live, tokens

    def _day() -> date:
        return today or datetime.now(timezone.utc).date()

    def role_of(request: Request) -> str:
        h = request.headers.get("authorization", "")
        tok = h[7:] if h.lower().startswith("bearer ") else request.headers.get("x-war-token", "")
        for role, t in tokens.items():
            if tok and secrets.compare_digest(tok, t):
                return role
        raise HTTPException(401, {"code": "TOKEN_REQUIRED", "message": "нужен токен роли (Authorization: Bearer ...)"})

    def need(min_role: str):
        def dep(request: Request) -> str:
            role = role_of(request)
            if ROLE_RANK[role] < ROLE_RANK[min_role]:
                raise HTTPException(403, {"code": "ROLE_TOO_LOW", "message": f"роль {role} ниже требуемой {min_role}"})
            return role
        return dep

    viewer, analyst, compliance, owner = need("viewer"), need("analyst"), need("compliance"), need("owner")

    # ------------------------------------------------------------------ assessment core
    def assess_tender(t: dict, back_to_back: bool = False) -> dict:
        profile = store.kv_get("profile", {})
        sanctions = (store.kv_get("sanctions") or {}).get("entries")
        gates = rules.evaluate_gates(t, profile, store.reviews(t["key"]), sanctions, _day())
        gate_overall = rules.overall(gates)
        fx = (store.kv_get("fx") or {}).get("rates", {})
        usd = None
        if t.get("estimated_value") is not None and t.get("currency"):
            try:
                usd = finance.to_base(t["estimated_value"], t["currency"], "USD", fx)
            except ValueError:
                usd = None
        deals = store.docs("deals")
        b2b = back_to_back or any(d.get("matched") and d.get("tender_key") == t["key"] for d in deals)
        fit = scoring.fit(t, profile, store.kv_get("suppliers", []), store.docs("outcomes"),
                          capital_limit_usd=Decimal(str(profile.get("capital_limit_usd", 50000))), back_to_back=b2b, usd_value=usd,
                          today=_day(), weights=store.kv_get("weights"))
        return {"tender_key": t["key"], "tender_version": t["version"], "demo": bool(t.get("demo")), "compliance_status": gate_overall,
                "gates": gates, "fit": fit, **scoring.classify(gate_overall, fit), "usd_value": str(usd) if usd is not None else None,
                "back_to_back": b2b, "assessed_at": now()}

    def brief(a: dict) -> dict:
        return {"compliance_status": a["compliance_status"], "complexity": a["complexity"], "tier_if_cleared": a["tier_if_cleared"],
                "low_confidence": a["low_confidence"], "fit_score": a["fit"]["fit_score"], "confidence": a["fit"]["confidence"], "unknowns": a["fit"]["unknowns"],
                "open_gates": [g["id"] for g in a["gates"] if g["status"] != "PASS"]}

    def source_rows() -> list[dict]:
        rows = []
        for e in sources.REGISTRY:
            rows.append(sources.status_for(e, live_enabled=live, last=store.kv_get(f"last:{e['id']}"), has_key=bool(sam_key)))
        demo_n = sum(1 for t in store.latest_tenders() if t.get("demo"))
        rows.append(sources.status_for(dict(id="demo", name="DEMO fixtures", kind="demo", url="", auth="none", adapter=True, note="synthetic"),
                                       live_enabled=live, last=None) | {"count": demo_n})
        return rows

    def data_mode() -> dict:
        ted_ok = bool(store.kv_get("last:ted")) and live
        demo_n = sum(1 for t in store.latest_tenders() if t.get("demo"))
        return {"mode": "LIVE" if ted_ok else "DEMO", "live_enabled": live, "demo_tenders": demo_n,
                "watermark": None if ted_ok and not demo_n else "DEMO / NOT LIVE"}

    def audit(role: str, event: str, payload: dict | None = None):
        store.audit(role, event, payload)

    # ------------------------------------------------------------------ basics
    @app.get(f"{P}/health")
    def health():
        a = store.verify_audit()
        return {"status": "ok", "version": VERSION, "api": API_VERSION, **data_mode(), "audit_chain_ok": a["ok"], "ai_configured": chat is not None}

    @app.get(f"{P}/capabilities")
    def capabilities(_=Depends(viewer)):
        return {"roles": list(ROLE_RANK), "approval_required_actions": ["company.profile.update", "learning.weights.apply", "deal.exceed_capital"],
                "never_implemented": ["submit-bid", "contact-counterparty", "purchase-controlled-goods"], "bossman_calls":
                ["analyze_tender", "summarize_conflict", "check_compliance", "estimate_cashflow", "draft_bid_checklist"]}

    @app.get(f"{P}/sources/status")
    def sources_status(_=Depends(viewer)):
        return {"data": data_mode(), "sources": source_rows()}

    # ------------------------------------------------------------------ ingest
    @app.post(f"{P}/ingest")
    def ingest(body: IngestIn, role=Depends(analyst)):
        t0 = now()
        if body.source == "demo":
            docs = demo_conn.tenders(t0, _day())
            new = sum(store.upsert_tender(d)[2] for d in docs)
            store.kv_set("conflicts_demo", demo_conflicts.conflicts(t0))
            audit(role, "ingest.demo", {"tenders": len(docs)})
            return {"source": "demo", "fetched": len(docs), "new": new, "demo": True}
        if not live:
            raise HTTPException(403, {"code": "LIVE_DISABLED", "message": "WAR_TERMINAL_LIVE=false: внешние источники отключены"})
        if body.source == "ted":
            req, raw = ted.fetch(post, cpv_prefixes=tuple(body.cpv_prefixes or ted.DEFAULT_CPV), country=body.country, days=body.days, limit=body.limit, today=_day())
            sid, digest, fresh = store.save_snapshot("ted", ted.URL + "?" + req["query"], raw)
            docs = ted.normalize_all(raw, t0, digest, _day())
            new = upd = 0
            for d in docs:
                _, version, changed = store.upsert_tender(d)
                new += changed and version == 1
                upd += changed and version > 1
            store.kv_set("last:ted", {"at": t0, "count": len(docs)})
            audit(role, "ingest.ted", {"fetched": len(docs), "new": new, "updated": upd, "snapshot": digest, "query": req["query"]})
            return {"source": "ted", "fetched": len(docs), "new": new, "updated": upd, "duplicates": len(docs) - new - upd,
                    "snapshot_sha256": digest, "snapshot_new": fresh, "total_available": raw.get("totalNoticeCount")}
        if body.source == "sam":
            if not sam_key:
                raise HTTPException(409, {"code": "NOT_CONNECTED", "message": "нет SAM_API_KEY (бесплатный ключ на sam.gov)"})
            req, raw = sam.fetch(get, sam_key, days=body.days, limit=body.limit, today=_day())
            sid, digest, _ = store.save_snapshot("sam", sam.URL, raw)
            docs = sam.normalize_all(raw, t0, digest)
            new = sum(store.upsert_tender(d)[2] for d in docs)
            store.kv_set("last:sam", {"at": t0, "count": len(docs)})
            audit(role, "ingest.sam", {"fetched": len(docs), "snapshot": digest})
            return {"source": "sam", "fetched": len(docs), "new": new, "snapshot_sha256": digest}
        if body.source == "usaspending":
            if not body.keyword:
                raise HTTPException(422, {"message": "для usaspending нужен keyword"})
            req, raw = usaspending.fetch(post, body.keyword, days=body.days, limit=body.limit, today=_day())
            sid, digest, _ = store.save_snapshot("usaspending", usaspending.URL, raw)
            awards = usaspending.normalize_all(raw, t0)
            store.kv_set(f"awards:{body.keyword}", awards)
            store.kv_set("last:usaspending", {"at": t0, "count": len(awards)})
            audit(role, "ingest.usaspending", {"keyword": body.keyword, "fetched": len(awards), "snapshot": digest})
            return {"source": "usaspending", "fetched": len(awards), "snapshot_sha256": digest}
        if not body.keyword:
            raise HTTPException(422, {"message": "для gdelt нужен keyword (запрос)"})
        req, raw = gdelt.fetch(get, body.keyword, limit=body.limit)
        sid, digest, _ = store.save_snapshot("gdelt", gdelt.URL, raw)
        items = gdelt.normalize_all(raw, t0)
        store.kv_set(f"news:{body.keyword}", items)
        store.kv_set("last:gdelt", {"at": t0, "count": len(items)})
        audit(role, "ingest.gdelt", {"query": body.keyword, "fetched": len(items), "snapshot": digest})
        return {"source": "gdelt", "fetched": len(items), "snapshot_sha256": digest}

    # ------------------------------------------------------------------ conflicts
    @app.get(f"{P}/conflicts")
    def conflicts(region: str | None = None, severity: str | None = None, since: str | None = None, include_demo: bool = True, _=Depends(viewer)):
        real = store.kv_get("conflicts", [])
        items = list(real) + (store.kv_get("conflicts_demo", []) if include_demo else [])
        if severity:
            items = [c for c in items if c.get("severity") == severity]
        if region:
            items = [c for c in items if region.lower() in (c.get("country", "") + c.get("name", "")).lower()]
        if since:
            items = [c for c in items if not c.get("since") or c["since"] >= since]
        return {"items": items, "real_count": len(real), "status": "OK" if real else "NOT_CONNECTED",
                "message": None if real else "реальных данных о конфликтах нет: импортируйте UCDP/ACLED (POST /conflicts/import) с источником и лицензией",
                "safety": "карта показывает регионы для коммерческого контекста; тактический анализ и целеуказание не поддерживаются"}

    @app.post(f"{P}/conflicts/import")
    def conflicts_import(body: ConflictImport, role=Depends(compliance)):
        items = [dict(c.model_dump(), id=f"{c.source_id}:{c.name}", retrieved_at=now(), demo=False) for c in body.items]
        store.kv_set("conflicts", items)
        audit(role, "conflicts.import", {"count": len(items), "sources": sorted({c["source_id"] for c in items})})
        return {"imported": len(items)}

    @app.get(f"{P}/conflicts/news")
    def conflict_news(query: str, _=Depends(viewer)):
        items = store.kv_get(f"news:{query}")
        if items is None:
            return {"status": "NOT_RUN", "items": [], "message": "новости не загружены: POST /ingest {source:gdelt, keyword}"}
        return {"status": "OK", "items": items, "disclaimer": "упоминания в СМИ, не подтверждённые события"}

    # ------------------------------------------------------------------ tenders
    @app.get(f"{P}/tenders")
    def tenders(status: str | None = None, complexity: str | None = None, tier: str | None = None, jurisdiction: str | None = None,
                category: str | None = None, country: str | None = None, q: str | None = None, include_demo: bool = True,
                limit: int = 200, offset: int = 0, _=Depends(viewer)):
        out = []
        for t in store.latest_tenders():
            if t.get("demo") and not include_demo:
                continue
            if status and t.get("status") != status or jurisdiction and t.get("jurisdiction") != jurisdiction or category and t.get("category") != category:
                continue
            if country and rules.country2(t.get("country")) != rules.country2(country):
                continue
            if q and q.lower() not in f"{t.get('title', '')} {t.get('buyer', '')}".lower():
                continue
            a = brief(assess_tender(t))
            if complexity and a["complexity"] != complexity or tier and a["tier_if_cleared"] != tier:
                continue
            out.append({**{k: t.get(k) for k in ("key", "version", "id", "official_notice_id", "title", "jurisdiction", "country", "category", "status",
                                                  "deadline", "estimated_value", "currency", "source_url", "source_id", "retrieved_at", "published_at",
                                                  "data_quality", "buyer", "cpv", "demo")}, "assessment": a})
        out.sort(key=lambda r: (r["demo"], -r["assessment"]["fit_score"]))
        return {"items": out[offset:offset + limit], "total": len(out), "data": data_mode()}

    @app.get(P + "/tenders/{key:path}/checklist")
    def checklist(key: str, _=Depends(viewer)):
        t = store.get_tender(key)
        if not t:
            raise HTTPException(404, {"message": "тендер не найден"})
        a = assess_tender(t)
        items = [{"step": f"Снять проверку: {g['id']}", "why": g["reason"], "evidence": g["evidence_needed"], "status": g["status"]} for g in a["gates"] if g["status"] != "PASS"]
        items += [{"step": "Прочитать документацию тендера целиком и выписать требования", "status": "TODO"},
                  {"step": "Получить котировки поставщиков и подтвердить срок/цену письменно", "status": "TODO"},
                  {"step": "Посчитать landed cost, cashflow и стресс задержки 30/60/90 дней (POST /finance/estimate)", "status": "TODO"},
                  {"step": "Юридическое заключение и подтверждение владельца до любых действий с заказчиком", "status": "REQUIRED"}]
        return {"tender_key": key, "draft": True, "note": "черновик чек-листа; подача заявки этим приложением невозможна", "items": items}

    @app.get(P + "/tenders/{key:path}")
    def tender_detail(key: str, _=Depends(viewer)):
        t = store.get_tender(key)
        if not t:
            raise HTTPException(404, {"message": "тендер не найден"})
        return {"tender": t, "versions": [v["version"] for v in store.tender_versions(key)], "assessment": assess_tender(t),
                "reviews": list(store.reviews(key).values())}

    # ------------------------------------------------------------------ assessments + review
    @app.post(f"{P}/assessments")
    def create_assessment(body: AssessIn, role=Depends(analyst)):
        t = store.get_tender(body.tender_key)
        if not t:
            raise HTTPException(404, {"message": "тендер не найден"})
        a = assess_tender(t, body.back_to_back)
        if body.use_ai:
            ai_res = ai_mod.classify(t, chat, store)
            a["ai"] = ai_res
            a["ai_disagreement"] = ai_res.get("status") == "OK" and ai_mod.disagreement(ai_res.get("tier"), a["tier_if_cleared"])
        a["dry_run"] = body.dry_run
        if not body.dry_run:
            a["id"] = store.add_doc("assessments", t["key"], a, dry_run=False)
            audit(role, "assessment.created", {"id": a["id"], "tender": t["key"], "complexity": a["complexity"], "fit": a["fit"]["fit_score"]})
        return jd(a)

    @app.get(P + "/assessments/{aid}")
    def get_assessment(aid: int, _=Depends(viewer)):
        rows = [d for d in store.docs("assessments", "WHERE id=?", (aid,))]
        if not rows:
            raise HTTPException(404, {"message": "оценка не найдена"})
        return jd(rows[0])

    @app.post(P + "/assessments/{aid}/review")
    def review(aid: int, body: ReviewIn, role=Depends(compliance)):
        rows = store.docs("assessments", "WHERE id=?", (aid,))
        if not rows:
            raise HTTPException(404, {"message": "оценка не найдена"})
        key = rows[0]["tender_key"]
        t = store.get_tender(key)
        if body.gate_id not in {g["id"] for g in rows[0]["gates"]}:
            raise HTTPException(422, {"message": f"нет такой проверки: {body.gate_id}"})
        if body.gate_id == "controlled_goods" and body.decision == "cleared" and rules.is_controlled(t) and not body.legal_reference:
            raise HTTPException(422, {"code": "LEGAL_REFERENCE_REQUIRED", "message": "для военной номенклатуры нужна ссылка на юридическое заключение (legal_reference)"})
        rid = store.add_review(key, body.gate_id, body.decision, role, body.note)
        audit(role, "review.signed", {"review": rid, "tender": key, "gate": body.gate_id, "decision": body.decision, "legal_reference": body.legal_reference})
        return {"review_id": rid, "note": "BLOCKED-проверки (эмбарго, срок, отмена) этим решением не снимаются"}

    # ------------------------------------------------------------------ company / approvals
    @app.get(f"{P}/company/profile")
    def get_profile(_=Depends(viewer)):
        return {"profile": store.kv_get("profile", {}), "pending": [a for a in store.list_approvals("pending") if a["action"] == "company.profile.update"]}

    @app.patch(f"{P}/company/profile")
    def patch_profile(body: ProfilePatch, role=Depends(analyst)):
        bad = set(body.changes) - PROFILE_KEYS
        if bad:
            raise HTTPException(422, {"message": f"недопустимые поля профиля: {sorted(bad)}"})
        aid = store.request_approval("company.profile.update", body.changes, role)
        audit(role, "approval.requested", {"id": aid, "action": "company.profile.update"})
        return {"status": "approval_required", "approval_id": aid}

    def apply_approved(ap: dict) -> dict:
        if ap["action"] == "company.profile.update":
            prof = store.kv_get("profile", {})
            prof.update(ap["payload"])
            store.kv_set("profile", prof)
        elif ap["action"] == "learning.weights.apply":
            store.kv_set("weights", ap["payload"]["weights"])
        elif ap["action"] == "deal.exceed_capital":
            lst = store.kv_get("deal_approvals", [])
            lst.append(ap["payload"]["deal_id"])
            store.kv_set("deal_approvals", lst)
        return {"applied": ap["action"]}

    @app.get(f"{P}/approvals")
    def approvals(status: str | None = None, _=Depends(viewer)):
        return {"items": store.list_approvals(status)}

    @app.post(P + "/approvals/{aid}/decide")
    def decide(aid: int, body: DecideIn, role=Depends(owner)):
        ap = store.decide_approval(aid, body.approve, role, body.note)
        if ap is None:
            raise HTTPException(404, {"message": "ожидающего запроса нет"})
        res = apply_approved(ap) if body.approve else {"applied": None}
        audit(role, "approval.decided", {"id": aid, "action": ap["action"], "approve": body.approve, "note": body.note})
        return {"approval": ap, **res}

    # ------------------------------------------------------------------ finance / deals / suppliers / fx / sanctions
    @app.post(f"{P}/finance/estimate")
    def finance_estimate(b: FinanceIn, _=Depends(analyst)):
        D = Decimal
        try:
            lc = finance.landed_cost(purchase=D(str(b.purchase)), freight=D(str(b.freight)), insurance=D(str(b.insurance)), duties_pct=D(str(b.duties_pct)),
                                     certification=D(str(b.certification)), financing=D(str(b.financing)), risk_reserve_pct=D(str(b.risk_reserve_pct)),
                                     overhead=D(str(b.overhead)))
            metrics = finance.price_metrics(D(str(b.bid_price)), lc["landed_cost"])

            def events(extra: int):
                return finance.build_events(supplier_prepay_pct=D(str(b.supplier_prepay_pct)), supplier_total=D(str(b.purchase)), delivery_day=b.delivery_day,
                                            acceptance_day=b.acceptance_day, bid_price=D(str(b.bid_price)), customer_pay_delay_days=b.customer_pay_delay_days + extra,
                                            vat_import=D(str(b.vat_import)), vat_recovery_days=b.vat_recovery_days, logistics_cost=D(str(b.freight + b.logistics_cost)),
                                            buyer_prepay_pct=D(str(b.buyer_prepay_pct)))
            cf = finance.cashflow(events(0))
            st = finance.stress(events, annual_rate=D(str(b.annual_rate)), bid_price=D(str(b.bid_price)), landed=lc["landed_cost"])
            peak_usd = finance.to_base(cf["peak_funding_need"], b.currency, "USD", {k: v for k, v in b.fx.items()})
        except ValueError as exc:
            raise HTTPException(422, {"code": "FX_OR_INPUT", "message": str(exc)}) from exc
        cap = finance.capital_check(peak_usd, limit_usd=D(str(b.capital_limit_usd)), order_confirmed=b.order_confirmed, supplier_contract_signed=b.supplier_contract_signed)
        return jd({"currency": b.currency, "landed": lc, "metrics": metrics, "cashflow": cf, "stress": st, "peak_funding_need_usd": peak_usd.quantize(Decimal("0.01")),
                   "capital": cap, "disclaimer": "все входные числа задал человек; рыночных допущений приложение не вводит"})

    @app.put(f"{P}/fx")
    def put_fx(body: FxIn, role=Depends(analyst)):
        if any(v <= 0 for v in body.rates.values()):
            raise HTTPException(422, {"message": "курсы должны быть > 0"})
        store.kv_set("fx", body.model_dump())
        audit(role, "fx.set", {"as_of": body.as_of, "source": body.source, "pairs": sorted(body.rates)})
        return {"stored": len(body.rates)}

    @app.get(f"{P}/fx")
    def get_fx(_=Depends(viewer)):
        return store.kv_get("fx") or {"rates": {}, "as_of": None, "source": None}

    @app.get(f"{P}/suppliers")
    def list_suppliers(_=Depends(viewer)):
        return {"items": store.kv_get("suppliers", [])}

    def _check_supplier(s: SupplierIn):
        c = rules.country2(s.country)
        if c in rules.RESTRICTED_SEED:
            raise HTTPException(422, {"code": "SUPPLIER_JURISDICTION_BLOCKED", "message": f"поставщик из {c}: ограничения по seed-списку"})
        sanc = (store.kv_get("sanctions") or {}).get("entries")
        hit = rules.screen_party(s.name, sanc) if sanc else None
        return {"screening": "NOT_CONNECTED" if not sanc else ("POSSIBLE_MATCH" if hit else "CLEAR"), "match": hit}

    @app.post(f"{P}/suppliers")
    def add_supplier(body: SupplierIn, role=Depends(analyst)):
        scr = _check_supplier(body)
        lst = store.kv_get("suppliers", [])
        lst.append(body.model_dump() | {"added_at": now(), "screening": scr["screening"]})
        store.kv_set("suppliers", lst)
        audit(role, "supplier.added", {"name": body.name, "country": body.country, "screening": scr["screening"]})
        return {"stored": True, **scr}

    @app.post(f"{P}/deals")
    def add_deal(body: DealIn, role=Depends(analyst)):
        scr = _check_supplier(body.supplier)
        matched = bool(body.order_confirmed and body.supplier_contract_signed and body.supplier.confirmed and scr["screening"] == "CLEAR")
        doc = body.model_dump() | {"matched": matched, "screening": scr, "owner_approved": False,
                                   "note": "заказ + поставщик состыкованы только при подтверждённом заказе, подписанном контракте и чистой санкционной проверке"}
        did = store.add_doc("deals", body.tender_key, doc)
        audit(role, "deal.created", {"id": did, "matched": matched, "screening": scr["screening"]})
        return {"id": did, "matched": matched, "screening": scr, "next": "для превышения лимита капитала запросите одобрение владельца" if matched else "условия состыковки не выполнены"}

    @app.get(f"{P}/deals")
    def list_deals(_=Depends(viewer)):
        approved = set(store.kv_get("deal_approvals", []))
        return {"items": [d | {"owner_approved": d["id"] in approved} for d in store.docs("deals")]}

    @app.post(P + "/deals/{did}/request-approval")
    def deal_approval(did: int, role=Depends(analyst)):
        deals = store.docs("deals", "WHERE id=?", (did,))
        if not deals or not deals[0].get("matched"):
            raise HTTPException(409, {"code": "NOT_MATCHED", "message": "сделка не состыкована: одобрять нечего"})
        aid = store.request_approval("deal.exceed_capital", {"deal_id": did}, role)
        audit(role, "approval.requested", {"id": aid, "action": "deal.exceed_capital", "deal": did})
        return {"status": "approval_required", "approval_id": aid}

    @app.post(f"{P}/sanctions/import")
    def sanctions_import(body: SanctionsIn, role=Depends(compliance)):
        store.kv_set("sanctions", body.model_dump())
        audit(role, "sanctions.import", {"count": len(body.entries), "source": body.source, "version": body.list_version, "as_of": body.as_of})
        return {"stored": len(body.entries)}

    # ------------------------------------------------------------------ learning
    @app.post(f"{P}/learning/decisions")
    def learn_decision(body: DecisionIn, role=Depends(owner)):
        t = store.get_tender(body.tender_key)
        if not t:
            raise HTTPException(404, {"message": "тендер не найден"})
        a = assess_tender(t)
        snap = {"tier_if_cleared": a["tier_if_cleared"], "fit_score": a["fit"]["fit_score"], "components": a["fit"]["components"],
                "gates": {g["id"]: g["status"] for g in a["gates"]}}
        did = learning.record_decision(store, body.tender_key, body.decision, body.reasons, snap)
        audit(role, "learning.decision", {"id": did, "tender": body.tender_key, "decision": body.decision})
        return {"id": did}

    @app.post(f"{P}/learning/outcomes")
    def learn_outcome(body: OutcomeIn, role=Depends(owner)):
        t = store.get_tender(body.tender_key)
        if not t:
            raise HTTPException(404, {"message": "тендер не найден"})
        if t.get("demo"):
            raise HTTPException(422, {"code": "DEMO_NOT_LEARNABLE", "message": "результаты по DEMO-данным в обучение не принимаются"})
        oid = learning.record_outcome(store, body.tender_key, body.result, body.actual_margin_pct, t.get("cpv", []), body.note)
        audit(role, "learning.outcome", {"id": oid, "tender": body.tender_key, "result": body.result})
        return {"id": oid}

    @app.get(f"{P}/learning/calibration")
    def learn_cal(_=Depends(viewer)):
        return learning.calibration(store)

    @app.get(f"{P}/learning/proposal")
    def learn_prop(_=Depends(viewer)):
        return learning.propose_weights(store)

    @app.post(f"{P}/learning/proposal/apply")
    def learn_apply(role=Depends(owner)):
        prop = learning.propose_weights(store)
        if prop["status"] != "PROPOSAL":
            raise HTTPException(409, {"code": "INSUFFICIENT_DATA", "message": f"данных недостаточно: n={prop['n']}, нужно {prop['needed']}"})
        aid = store.request_approval("learning.weights.apply", {"weights": prop["weights"]}, role)
        audit(role, "approval.requested", {"id": aid, "action": "learning.weights.apply"})
        return {"status": "approval_required", "approval_id": aid, "proposal": prop}

    @app.get(f"{P}/learning/dataset", response_class=PlainTextResponse)
    def learn_dataset(_=Depends(owner)):
        return learning.export_dataset(store)

    # ------------------------------------------------------------------ audit + forbidden actions
    @app.get(f"{P}/audit/events")
    def audit_events(limit: int = 100, _=Depends(compliance)):
        return {"items": store.audit_events(min(limit, 500))}

    @app.get(f"{P}/audit/verify")
    def audit_verify(_=Depends(compliance)):
        return store.verify_audit()

    @app.post(P + "/actions/{name}")
    def forbidden(name: str, role=Depends(viewer)):
        audit(role, "action.refused", {"name": name})
        raise HTTPException(403, {"code": "NOT_IMPLEMENTED_REQUIRES_LEGAL_REVIEW",
                                  "message": "подача заявок, контакт с контрагентами и закупка контролируемых товаров в этой версии не реализованы и недоступны без отдельного решения владельца и юридической проверки"})

    # ------------------------------------------------------------------ UI
    if UI_DIR.exists():
        app.mount("/ui", StaticFiles(directory=str(UI_DIR), html=True), name="ui")

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/ui/index.html")

    return app
