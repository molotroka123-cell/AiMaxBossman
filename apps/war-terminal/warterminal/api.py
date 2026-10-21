"""FastAPI app, API v1. Roles: viewer < analyst < compliance < owner. Every write is audited; risky ones go through the approval ledger.

Never implemented on purpose: bid submission, counterparty contact, purchase of controlled goods (see /actions/*: always 403)."""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from . import API_VERSION, VERSION, ai as ai_mod, contacts as contacts_mod, finance, firm as firm_mod, fx_tracker as fxt, geo, learning, rules, scoring, sources
from . import translate as tr_mod
from .connectors import cnb, cz, indicators, gdelt, http as http_mod, prozorro, sam, sanctions as sanc_conn, ted, ucdp, usaspending, worldbank
from .provenance import is_real
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
    source: str = Field(pattern=r"^(ted|sam|usaspending|gdelt|worldbank|cz|prozorro)$")
    days: int = Field(default=30, ge=1, le=365)
    cpv_prefixes: list[str] | None = None
    country: str | None = None              # None = all countries (owner: Czech company, whole world)
    limit: int = Field(default=50, ge=1, le=200)
    keyword: str | None = None
    reset_cursor: bool = False              # prozorro: forget the stored feed cursor and start `days` back


class BrowserIngestIn(BaseModel):
    """Records read from public portal pages by the headless-browser tool (tools/browser_contracts.py)."""
    portal: str = Field(min_length=3, max_length=60)
    tenders: list[dict] = Field(max_length=500)


class TranslateIn(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=tr_mod.MAX_PER_CALL)
    target: str = Field(pattern=r"^(ru|en)$")


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


class SanctionsFetchIn(BaseModel):
    sources: list[str] = ["ofac", "un"]


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


def _enum_rx(values) -> str:
    """Pattern built from the module's own enum tuple: the HTTP layer cannot drift from the domain values."""
    return "^(" + "|".join(re.escape(v) for v in values) + ")$"


class KeywordsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    en: list[str] = Field(default_factory=list, max_length=40)
    uk: list[str] = Field(default_factory=list, max_length=40)
    cs: list[str] = Field(default_factory=list, max_length=40)
    ru: list[str] = Field(default_factory=list, max_length=40)


class ContactIn(BaseModel):
    """A company met at an event. person_* only when the owner types them in; never audited, exported or sent to Bossman."""
    model_config = ConfigDict(extra="forbid")
    company: str = Field(min_length=2, max_length=200)
    country: str | None = Field(default=None, max_length=60)
    website: str | None = Field(default=None, max_length=300)
    public_email: str | None = Field(default=None, max_length=200)
    public_email_source: str | None = Field(default=None, max_length=200)
    person_name: str | None = Field(default=None, max_length=120)
    person_phone: str | None = Field(default=None, max_length=40)
    products: str = Field(default="", max_length=2000)
    cpv_prefixes: list[str] = Field(default_factory=list, max_length=40)
    keywords: KeywordsIn = Field(default_factory=KeywordsIn)
    event: str | None = Field(default=None, max_length=200)
    source: str | None = Field(default=None, max_length=300)
    notes: str = Field(default="", max_length=2000)
    status: str = Field(default="new", pattern=_enum_rx(contacts_mod.STATUSES))
    next_step: str | None = Field(default=None, max_length=300)
    next_step_date: str | None = Field(default=None, pattern=r"^(\d{4}-\d{2}-\d{2})?$")


class ContactPatch(BaseModel):
    """Only the fields sent are changed; an empty string or null clears an optional field (e.g. the person's phone)."""
    model_config = ConfigDict(extra="forbid")
    company: str | None = Field(default=None, min_length=2, max_length=200)
    country: str | None = Field(default=None, max_length=60)
    website: str | None = Field(default=None, max_length=300)
    public_email: str | None = Field(default=None, max_length=200)
    public_email_source: str | None = Field(default=None, max_length=200)
    person_name: str | None = Field(default=None, max_length=120)
    person_phone: str | None = Field(default=None, max_length=40)
    products: str | None = Field(default=None, max_length=2000)
    cpv_prefixes: list[str] | None = Field(default=None, max_length=40)
    keywords: KeywordsIn | None = None
    event: str | None = Field(default=None, max_length=200)
    source: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=2000)
    status: str | None = Field(default=None, pattern=_enum_rx(contacts_mod.STATUSES))
    next_step: str | None = Field(default=None, max_length=300)
    next_step_date: str | None = Field(default=None, pattern=r"^(\d{4}-\d{2}-\d{2})?$")


class MatchFeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tender_key: str = Field(min_length=3, max_length=300)
    contact_id: int = Field(ge=1)
    vote: str = Field(pattern=_enum_rx(contacts_mod.VOTES))
    reason: str | None = Field(default=None, max_length=500)


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def create_app(data_dir: str | Path | None = None, *, live: bool | None = None, tokens: dict | None = None, post=None, get=None,
               chat=None, today: date | None = None, download=None, bossman=None, sleep=None) -> FastAPI:
    data_dir = Path(data_dir or os.environ.get("WAR_TERMINAL_DATA") or Path.home() / ".war-terminal")
    data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(data_dir / "war.db")
    live = live if live is not None else os.environ.get("WAR_TERMINAL_LIVE", "true").lower() not in ("0", "false", "no", "off")   # live by default; no demo mode exists
    purge = store.archive_unverified(data_dir / "archive", _now_stamp())          # records without provenance leave every view; archived, not destroyed
    if purge["archived_total"]:
        store.audit("system", "provenance.archive", {k: v for k, v in purge.items() if k != "reasons"} | {"reasons": purge["reasons"]})
    tokens = tokens or _load_tokens(data_dir)
    post, get = post or http_mod.post_json, get or http_mod.get_json
    download = download or http_mod.get_bytes
    _sidx: dict = {"version": None, "index": None}

    def sanctions_index():
        """Sanctions list as an in-memory token index, rebuilt only when the stored list version changes."""
        ver = store.kv_get("sanctions_version")
        if ver is None:
            return None
        if _sidx["version"] != ver:
            entries = (store.kv_get("sanctions") or {}).get("entries") or []
            _sidx.update(version=ver, index=rules.SanctionsIndex(entries) if entries else None)
        return _sidx["index"]

    def store_sanctions(doc: dict) -> None:
        store.kv_set("sanctions", doc)
        store.kv_set("sanctions_version", f"{doc.get('list_version')}|{len(doc.get('entries', []))}|{now()}")
    if chat is None and os.environ.get("WAR_TERMINAL_AI_URL") and os.environ.get("WAR_TERMINAL_AI_MODEL"):
        chat = ai_mod.make_chat(os.environ["WAR_TERMINAL_AI_URL"], os.environ["WAR_TERMINAL_AI_MODEL"], os.environ.get("WAR_TERMINAL_AI_KEY"))
    sam_key = os.environ.get("SAM_API_KEY", "")
    gateway = None if bossman is False else (bossman or tr_mod.gateway_from_env(dict(os.environ)))     # False = off (tests)
    translator = tr_mod.Translator(store, gateway)

    app = FastAPI(title="Bossman War & Procurement Terminal", version=VERSION, description="API v1. Real data only: every record carries source, link and fetch time; records without provenance are archived and never shown.")
    app.state.store, app.state.live, app.state.tokens, app.state.purge_report = store, live, tokens, purge

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

    def firm_of(request: Request) -> dict | None:
        """Firm data from the popup, per request, in memory only (never stored, logged, translated or exported). None = view-only."""
        try:
            return firm_mod.decode_header(request.headers.get(firm_mod.HEADER))
        except firm_mod.FirmError as exc:
            raise HTTPException(422, {"code": "FIRM_INVALID", "fields": exc.fields, "message": "данные фирмы заполнены неверно: " + ", ".join(exc.fields)}) from exc

    from contextlib import contextmanager

    @contextmanager
    def guard(source_id: str):
        """A source that cannot be reached is reported per source (status strip) instead of silently showing old data."""
        try:
            yield
        except HTTPException:
            raise
        except Exception as exc:                                    # noqa: BLE001 - recorded and surfaced as 502, not swallowed
            msg = f"{type(exc).__name__}: {str(exc)[:200]}"
            store.kv_set(f"error:{source_id}", {"at": now(), "message": msg})
            raise HTTPException(502, {"code": "SOURCE_UNREACHABLE", "source": source_id, "message": f"источник {source_id} недоступен: {msg}"}) from exc
        else:
            if store.kv_get(f"error:{source_id}"):
                store.kv_set(f"error:{source_id}", None)

    # ------------------------------------------------------------------ assessment core
    def assess_tender(t: dict, back_to_back: bool = False, firm: dict | None = None) -> dict:
        profile = firm_mod.to_profile(firm) if firm else {}              # view-only (no firm entered): gates only, fit is not computed
        gates = rules.evaluate_gates(t, profile, store.reviews(t["key"]), sanctions_index(), _day())
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
        fit = (scoring.fit(t, profile, store.kv_get("suppliers", []), store.docs("outcomes"),
                           capital_limit_usd=Decimal("50000"), back_to_back=b2b, usd_value=usd,        # 50 000 USD: the owner's standing capital rule
                           today=_day(), weights=store.kv_get("weights")) if firm else scoring.no_firm())
        return {"tender_key": t["key"], "tender_version": t["version"], "compliance_status": gate_overall,
                "gates": gates, "fit": fit, **scoring.classify(gate_overall, fit), "usd_value": str(usd.quantize(Decimal("0.01"))) if usd is not None else None,
                "back_to_back": b2b, "assessed_at": now()}

    def firm_checks(t: dict, firm: dict | None) -> list[dict] | None:
        """Tender-vs-firm facts for the response only (attached AFTER anything is saved, no identifiers inside)."""
        if not firm:
            return None
        fx = (store.kv_get("fx") or {}).get("rates", {})
        eur = None
        if t.get("estimated_value") is not None and t.get("currency"):
            try:
                eur = finance.to_base(t["estimated_value"], t["currency"], "EUR", fx)
            except ValueError:
                eur = None
        return firm_mod.checks(t, firm, eur, rules.is_controlled(t))

    def brief(a: dict) -> dict:
        return {"compliance_status": a["compliance_status"], "complexity": a["complexity"], "tier_if_cleared": a["tier_if_cleared"],
                "low_confidence": a["low_confidence"], "fit_score": a["fit"]["fit_score"], "confidence": a["fit"]["confidence"], "unknowns": a["fit"]["unknowns"],
                "open_gates": [g["id"] for g in a["gates"] if g["status"] != "PASS"]}

    def source_rows() -> list[dict]:
        rows = []
        for e in sources.REGISTRY:
            rows.append(sources.status_for(e, live_enabled=live, last=store.kv_get(f"last:{e['id']}"), has_key=bool(sam_key), error=store.kv_get(f"error:{e['id']}")))
        return rows

    def data_mode() -> dict:
        """Real data only. `excluded_unverified` = records archived at startup because they had no provenance (0 in a clean store)."""
        return {"mode": "LIVE" if live else "OFFLINE", "live_enabled": live, "excluded_unverified": purge["archived_total"],
                "watermark": None if live else "OFFLINE / SOURCES OFF"}

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
        with guard({"cz": "cz_nen"}.get(body.source, body.source)):
            return _ingest(body, role)

    def _ingest(body: IngestIn, role: str):
        t0 = now()
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
        if body.source == "cz":
            # Owner decision 17.10.2026: enable the ISVZ open-data monthly export despite robots.txt "Disallow: /".
            # Only the documented ZIP is downloaded (no HTML pages), one file per month, identified User-Agent.
            req, raw = cz.fetch(download, days=body.days, limit=body.limit, today=_day())
            sid, digest, fresh = store.save_snapshot("cz_nen", cz.PAGE, raw)
            docs = cz.normalize_all(raw, t0, digest, _day())
            new = upd = 0
            for d in docs:
                _, version, changed = store.upsert_tender(d)
                new += changed and version == 1
                upd += changed and version > 1
            store.kv_set("last:cz_nen", {"at": t0, "count": len(docs)})
            audit(role, "ingest.cz", {"fetched": len(docs), "new": new, "updated": upd, "snapshot": digest, "owner_decision": "2026-10-17 enable despite robots.txt"})
            return {"source": "cz", "fetched": len(docs), "new": new, "updated": upd, "duplicates": len(docs) - new - upd, "snapshot_sha256": digest}
        if body.source == "worldbank":
            req, raw = worldbank.fetch(get, query=body.keyword, rows=body.limit, today=_day(),
                                       country=(rules.country2(body.country) if body.country else None))
            sid, digest, fresh = store.save_snapshot("worldbank", "https://search.worldbank.org/api/v2/procnotices", raw)
            docs = worldbank.normalize_all(raw, t0, digest, _day())
            new = upd = 0
            for d in docs:
                _, version, changed = store.upsert_tender(d)
                new += changed and version == 1
                upd += changed and version > 1
            store.kv_set("last:worldbank", {"at": t0, "count": len(docs)})
            audit(role, "ingest.worldbank", {"fetched": len(docs), "new": new, "updated": upd, "snapshot": digest})
            return {"source": "worldbank", "fetched": len(docs), "new": new, "updated": upd, "duplicates": len(docs) - new - upd, "snapshot_sha256": digest}
        if body.source == "prozorro":
            return _ingest_prozorro(body, role, t0)
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

    def _ingest_prozorro(body: IngestIn, role: str, t0: str):
        """Incremental Prozorro feed sync. The cursor is saved after each stored page: a failed run keeps what it stored
        and the next run resumes at the first unfinished page. `limit` = relevant tenders after which the run stops."""
        cursor = None if body.reset_cursor else store.kv_get("cursor:prozorro")
        days = body.days if "days" in body.model_fields_set else prozorro.DEFAULT_DAYS
        known = {t.get("prozorro_id") for t in store.latest_tenders() if t.get("source_id") == "prozorro" and t.get("prozorro_id")}
        pget = prozorro.polite(get, **({"sleep": sleep} if sleep else {}))
        tot = {"pages": 0, "scanned": 0, "details_fetched": 0, "relevant": 0, "new": 0, "updated": 0, "duplicates": 0}
        snapshots, start, nxt, caught_up = [], cursor, cursor, False
        for page in prozorro.iter_pages(pget, cursor=cursor, days=days, today=_day(), known=known):
            docs = []
            if page["details"]:
                sid, digest, fresh = store.save_snapshot("prozorro", f"{prozorro.URL}?offset={page['offset']}", page["details"])
                snapshots.append(digest)
                docs = prozorro.normalize_all(page["details"], t0, digest, _day())
            for d in docs:
                _, version, changed = store.upsert_tender(d)
                tot["new"] += changed and version == 1
                tot["updated"] += changed and version > 1
                tot["duplicates"] += not changed
            nxt, caught_up = page["next_offset"], page["caught_up"]
            store.kv_set("cursor:prozorro", nxt)                       # only after the page's tenders are stored
            tot["pages"] += 1
            tot["scanned"] += page["scanned"]
            tot["details_fetched"] = page["details_fetched"]
            tot["relevant"] += len(docs)
            if tot["relevant"] >= body.limit:
                break
        store.kv_set("last:prozorro", {"at": t0, "count": tot["relevant"]})
        audit(role, "ingest.prozorro", {**tot, "cursor_from": start, "cursor_to": nxt, "snapshots": snapshots, "caught_up": caught_up})
        return {"source": "prozorro", "fetched": tot["relevant"], **tot, "cursor": nxt, "caught_up": caught_up,
                "snapshot_sha256": snapshots[-1] if snapshots else None}

    # ------------------------------------------------------------------ conflicts
    @app.post(f"{P}/ingest/browser")
    def ingest_browser(body: BrowserIngestIn, role=Depends(analyst)):
        """Store tenders read by a browser (no outbound network here). Needs provenance; never overwrites a notice already known."""
        known = {t["key"] for t in store.latest_tenders()}
        new = dup = bad = 0
        for d in body.tenders:
            if (d.get("source") != "browser" or d.get("demo") or not str(d.get("source_url", "")).startswith("https://")
                    or not d.get("fetched_at") or not d.get("official_notice_id") or not d.get("title") or d.get("status") not in ("open", "closed", "cancelled", "unknown")):
                bad += 1
                continue
            key = f"{d.get('jurisdiction')}:{d['official_notice_id']}"
            if key in known:
                dup += 1
                continue
            store.upsert_tender(d)
            known.add(key)
            new += 1
        store.kv_set(f"last:{body.portal}", {"at": now(), "count": new + dup})
        audit(role, "ingest.browser", {"portal": body.portal, "received": len(body.tenders), "new": new, "duplicates": dup, "rejected": bad})
        return {"source": "browser", "portal": body.portal, "received": len(body.tenders), "new": new, "duplicates": dup, "rejected": bad}

    @app.get(f"{P}/conflicts")
    def conflicts(region: str | None = None, severity: str | None = None, since: str | None = None, _=Depends(viewer)):
        real = [c for c in store.kv_get("conflicts", []) if is_real(c)]
        items = list(real)
        if severity:
            items = [c for c in items if c.get("severity") == severity]
        if region:
            items = [c for c in items if region.lower() in (c.get("country", "") + c.get("name", "")).lower()]
        if since:
            items = [c for c in items if not c.get("since") or c["since"] >= since]
        return {"items": items, "real_count": len(real), "status": "OK" if real else "NOT_CONNECTED", "acd": store.kv_get("conflicts_acd"),
                "message": None if real else "реальных данных о конфликтах нет: импортируйте UCDP/ACLED (POST /conflicts/import) с источником и лицензией",
                "safety": "карта показывает регионы для коммерческого контекста; тактический анализ и целеуказание не поддерживаются"}

    @app.post(f"{P}/conflicts/import")
    def conflicts_import(body: ConflictImport, role=Depends(compliance)):
        items = [dict(c.model_dump(), id=f"{c.source_id}:{c.name}", retrieved_at=now(), demo=False) for c in body.items]
        if any(not is_real(c) for c in items):
            raise HTTPException(422, {"code": "UNVERIFIED_RECORD", "message": "у каждой записи нужны source_id и source_url (https); без источника данные не принимаются"})
        store.kv_set("conflicts", items)
        audit(role, "conflicts.import", {"count": len(items), "sources": sorted({c["source_id"] for c in items})})
        return {"imported": len(items)}

    @app.get(f"{P}/conflicts/news")
    def conflict_news(query: str, _=Depends(viewer)):
        items = store.kv_get(f"news:{query}")
        if items is None:
            return {"status": "NOT_RUN", "items": [], "message": "новости не загружены: POST /ingest {source:gdelt, keyword}"}
        return {"status": "OK", "items": items, "disclaimer": "упоминания в СМИ, не подтверждённые события"}

    # ------------------------------------------------------------------ map layers (country choropleth)
    @app.get(f"{P}/map/layers")
    def map_layers(_=Depends(viewer)):
        countries: dict[str, dict] = {}
        unmapped_conflicts, unmapped_tenders = [], 0

        def row(iso3):
            return countries.setdefault(iso3, {"open_tenders": 0, "tenders": 0, "conflict_deaths": 0, "conflict_events": 0, "severity": None, "conflict_year": None,
                                               "military_usd": None, "military_year": None, "refugees": None})
        for t in store.latest_tenders():
            iso3 = geo.to_iso3(t.get("country"))
            if not iso3:
                unmapped_tenders += 1
                continue
            r = row(iso3)
            r["tenders"] += 1
            r["open_tenders"] += t.get("status") == "open"
        for c in (c for c in store.kv_get("conflicts", []) if is_real(c)):
            iso3 = geo.to_iso3(c.get("country"))
            if not iso3:
                unmapped_conflicts.append(c.get("country"))
                continue
            r = row(iso3)
            r.update(conflict_deaths=r["conflict_deaths"] + int(c.get("deaths") or 0), conflict_events=r["conflict_events"] + int(c.get("events") or 0),
                     severity=c.get("severity"), conflict_year=c.get("since"))
        for iso3, m in ((store.kv_get("ind_military") or {}).get("items") or {}).items():
            if iso3 in geo.index()["iso3"]:
                row(iso3).update(military_usd=m["usd"], military_year=m["year"])
        for iso3, r in ((store.kv_get("ind_refugees") or {}).get("items") or {}).items():
            if iso3 in geo.index()["iso3"]:
                row(iso3)["refugees"] = r["refugees"]
        return {"countries": countries, "unmapped_conflicts": sorted(set(unmapped_conflicts)), "unmapped_tenders": unmapped_tenders,
                "geometry": "/ui/vendor/countries-110m.geojson", "geometry_source": geo.index()["source"],
                "conflict_source": (store.kv_get("conflicts") or [{}])[0].get("source_id"),
                "conflict_source_url": (store.kv_get("conflicts") or [{}])[0].get("source_url"),
                "conflict_fetched_at": (store.kv_get("conflicts") or [{}])[0].get("retrieved_at"), "note": "страны закрашиваются по данным; без данных — серым"}

    # ------------------------------------------------------------------ tenders
    @app.get(f"{P}/tenders")
    def tenders(status: str | None = None, complexity: str | None = None, tier: str | None = None, jurisdiction: str | None = None,
                category: str | None = None, country: str | None = None, q: str | None = None, source: str | None = None,
                limit: int = 200, offset: int = 0, firm=Depends(firm_of), _=Depends(viewer)):
        out = []
        for t in store.latest_tenders():
            if status and t.get("status") != status or jurisdiction and t.get("jurisdiction") != jurisdiction or category and t.get("category") != category:
                continue
            if source and t.get("source_id") != source:
                continue
            if country and (geo.to_iso3(t.get("country")) is None or geo.to_iso3(t.get("country")) != geo.to_iso3(country)):
                continue                                # region-only tenders (e.g. "Western and Central Africa") never match a country
            if q and q.lower() not in f"{t.get('title', '')} {t.get('buyer', '')}".lower():
                continue
            a = brief(assess_tender(t, firm=firm))
            if complexity and a["complexity"] != complexity or tier and a["tier_if_cleared"] != tier:
                continue
            out.append({**{k: t.get(k) for k in ("key", "version", "id", "official_notice_id", "title", "jurisdiction", "country", "category", "status",
                                                  "deadline", "estimated_value", "currency", "source_url", "source_id", "retrieved_at", "published_at",
                                                  "data_quality", "buyer", "cpv")}, "assessment": a})
        out.sort(key=lambda r: (r["status"] != "open", -(r["assessment"]["fit_score"] if r["assessment"]["fit_score"] is not None else -1)))   # open first
        return {"items": out[offset:offset + limit], "total": len(out), "data": data_mode(), "firm_entered": firm is not None}

    @app.get(P + "/tenders/{key:path}/checklist")
    def checklist(key: str, firm=Depends(firm_of), _=Depends(viewer)):
        t = store.get_tender(key)
        if not t:
            raise HTTPException(404, {"message": "тендер не найден"})
        a = assess_tender(t, firm=firm)
        items = [{"step": f"Снять проверку: {g['id']}", "why": g["reason"], "evidence": g["evidence_needed"], "status": g["status"]} for g in a["gates"] if g["status"] != "PASS"]
        items += [{"step": "Прочитать документацию тендера целиком и выписать требования", "status": "TODO"},
                  {"step": "Получить котировки поставщиков и подтвердить срок/цену письменно", "status": "TODO"},
                  {"step": "Посчитать landed cost, cashflow и стресс задержки 30/60/90 дней (POST /finance/estimate)", "status": "TODO"},
                  {"step": "Юридическое заключение и подтверждение владельца до любых действий с заказчиком", "status": "REQUIRED"}]
        return {"tender_key": key, "draft": True, "note": "черновик чек-листа; подача заявки этим приложением невозможна", "items": items}

    @app.get(P + "/tenders/{key:path}/matches")                 # «кому предложить»: before /tenders/{key:path}, which would swallow it
    def tender_matches(key: str, k: int = Query(default=10, ge=1, le=50), include_declined: bool = False, _=Depends(viewer)):
        t = store.get_tender(key)
        if not t:
            raise HTTPException(404, {"message": "тендер не найден"})
        return contacts_mod.rank_for_tender(store, t, sanctions_index(), k=k, include_declined=include_declined)

    @app.get(P + "/tenders/{key:path}")
    def tender_detail(key: str, firm=Depends(firm_of), _=Depends(viewer)):
        t = store.get_tender(key)
        if not t:
            raise HTTPException(404, {"message": "тендер не найден"})
        return {"tender": t, "versions": [v["version"] for v in store.tender_versions(key)], "assessment": assess_tender(t, firm=firm),
                "firm_checks": firm_checks(t, firm), "firm_entered": firm is not None, "reviews": list(store.reviews(key).values())}

    # ------------------------------------------------------------------ assessments + review
    @app.post(f"{P}/assessments")
    def create_assessment(body: AssessIn, firm=Depends(firm_of), role=Depends(analyst)):
        t = store.get_tender(body.tender_key)
        if not t:
            raise HTTPException(404, {"message": "тендер не найден"})
        a = assess_tender(t, body.back_to_back, firm)
        if body.use_ai:
            ai_res = ai_mod.classify(t, chat, store)
            a["ai"] = ai_res
            a["ai_disagreement"] = ai_res.get("status") == "OK" and ai_mod.disagreement(ai_res.get("tier"), a["tier_if_cleared"])
        a["dry_run"] = body.dry_run
        if not body.dry_run:
            a["id"] = store.add_doc("assessments", t["key"], a, dry_run=False)
            audit(role, "assessment.created", {"id": a["id"], "tender": t["key"], "complexity": a["complexity"], "fit": a["fit"]["fit_score"]})
        return jd(a | {"firm_checks": firm_checks(t, firm), "firm_entered": firm is not None})          # firm facts are attached after saving: never persisted

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
        return {"profile": {}, "pending": [], "stored": False,
                "note": "данные фирмы вводятся в окне при каждом открытии терминала и на сервере не хранятся"}

    @app.patch(f"{P}/company/profile")
    def patch_profile(body: ProfilePatch, role=Depends(analyst)):
        raise HTTPException(410, {"code": "FIRM_NOT_STORED", "message": "профиль фирмы на сервере не хранится: данные вводит владелец в окне при открытии терминала"})

    @app.post(f"{P}/firm/validate")
    def firm_validate(body: dict, _=Depends(viewer)):
        """Server-side check of the popup fields (same IČO mod-11 rule as the browser). Nothing is stored; only field names come back."""
        try:
            firm_mod.clean(body)
        except firm_mod.FirmError as exc:
            return {"valid": False, "fields": exc.fields}
        return {"valid": True, "fields": []}

    def apply_approved(ap: dict) -> dict:
        if ap["action"] == "learning.weights.apply":
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
    def finance_estimate(b: FinanceIn, firm=Depends(firm_of), _=Depends(analyst)):
        if firm is None:
            raise HTTPException(409, {"code": "FIRM_REQUIRED", "message": "калькулятор финансов отключён: данные фирмы не введены (режим «только просмотр»)"})
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
        ctx = {"bid_vs_turnover_pct": None, "bid_vs_guarantee_pct": None}
        try:
            bid_eur = finance.to_base(D(str(b.bid_price)), b.currency, "EUR", {k: v for k, v in b.fx.items()})
            if firm.get("turnover_eur"):
                ctx["bid_vs_turnover_pct"] = (bid_eur / D(str(firm["turnover_eur"])) * 100).quantize(D("0.1"))
            if firm.get("guarantee_eur"):
                ctx["bid_vs_guarantee_pct"] = (bid_eur / D(str(firm["guarantee_eur"])) * 100).quantize(D("0.1"))
        except ValueError:
            pass                                                     # no EUR rate: the figures stay "нет данных", nothing is assumed
        return jd({"currency": b.currency, "landed": lc, "metrics": metrics, "cashflow": cf, "stress": st, "peak_funding_need_usd": peak_usd.quantize(Decimal("0.01")),
                   "firm_context": ctx,
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

    # ---- daily FX tracker (CNB + ECB, append-only log in the fx dir; collection runs from Task Scheduler, not from here)
    @app.get(f"{P}/fx/track/report")
    def fx_track_report(amount: str = "1000", baseline: str | None = None, _=Depends(viewer)):
        if baseline is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", baseline):
            raise HTTPException(422, {"code": "BAD_DATE", "message": "baseline: YYYY-MM-DD"})
        if fxt._dec(amount) is None or len(amount) > 24:
            raise HTTPException(422, {"code": "BAD_AMOUNT", "message": "amount: положительное число"})
        st = fxt.open_store()
        try:
            rep = fxt.build_report(st.rows(), amount=amount, baseline=baseline)
            rep["runs"] = st.runs(6)
        finally:
            st.close()
        rep["xe"] = {"used": False, "reason": "terms of use forbid scraping"}
        return rep
    @app.get(f"{P}/suppliers")
    def list_suppliers(_=Depends(viewer)):
        return {"items": store.kv_get("suppliers", [])}

    def _check_supplier(s: SupplierIn):
        c = rules.country2(s.country)
        if c in rules.RESTRICTED_SEED:
            raise HTTPException(422, {"code": "SUPPLIER_JURISDICTION_BLOCKED", "message": f"поставщик из {c}: ограничения по seed-списку"})
        sanc = sanctions_index()
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
        store_sanctions(body.model_dump())
        audit(role, "sanctions.import", {"count": len(body.entries), "source": body.source, "version": body.list_version, "as_of": body.as_of})
        return {"stored": len(body.entries)}

    # ------------------------------------------------------------------ official keyless live sources: sanctions, FX, conflicts
    def need_live():
        if not live:
            raise HTTPException(403, {"code": "LIVE_DISABLED", "message": "WAR_TERMINAL_LIVE=false: внешние источники отключены"})

    @app.post(f"{P}/sanctions/fetch")
    def sanctions_fetch(body: SanctionsFetchIn, role=Depends(compliance)):
        need_live()
        names, by, shas = set(), {}, {}
        for s in body.sources:
            if s == "ofac":
                url, parse = sanc_conn.OFAC_URL, sanc_conn.parse_ofac
            elif s == "un":
                url, parse = sanc_conn.UN_URL, sanc_conn.parse_un
            else:
                raise HTTPException(422, {"message": f"неизвестный список {s}"})
            with guard("ofac" if s == "ofac" else "un_sanctions"):
                data = download(url)
                recs = parse(data)
            if not recs:
                raise HTTPException(502, {"code": "EMPTY_LIST", "message": f"{s}: список пуст или формат изменился"})
            shas[s], by[s] = sanc_conn.digest(data), len(recs)
            names.update(r["name"] for r in recs)
            store.save_snapshot(f"sanctions_{s}", url, {"sha256": shas[s], "bytes": len(data), "records": len(recs)})
            store.kv_set(f"last:{'ofac' if s == 'ofac' else 'un_sanctions'}", {"at": now(), "count": len(recs)})
        version = ",".join(f"{k}:{v[:12]}" for k, v in sorted(shas.items()))
        store_sanctions({"entries": sorted(names), "source": "+".join(sorted(by)), "list_version": version, "as_of": _day().isoformat()})
        audit(role, "sanctions.fetch", {"by_source": by, "sha256": shas, "unique_names": len(names)})
        return {"stored": len(names), "by_source": by, "sha256": shas, "as_of": _day().isoformat(), "list_version": version}

    @app.get(f"{P}/sanctions/status")
    def sanctions_status(_=Depends(viewer)):
        doc = store.kv_get("sanctions") or {}
        return {"count": len(doc.get("entries", [])), "as_of": doc.get("as_of"), "source": doc.get("source"), "list_version": doc.get("list_version")}

    @app.post(f"{P}/fx/fetch")
    def fx_fetch(role=Depends(analyst)):
        need_live()
        with guard("cnb"):
            data = download(cnb.URL)
            fx = cnb.parse(data.decode("utf-8", "replace"))
        store.save_snapshot("cnb", cnb.URL, {"sha256": sanc_conn.digest(data), "as_of": fx["as_of"]})
        store.kv_set("fx", fx)
        store.kv_set("last:cnb", {"at": now(), "count": len(fx["rates"])})
        audit(role, "fx.fetch", {"as_of": fx["as_of"], "pairs": len(fx["rates"])})
        return {"as_of": fx["as_of"], "pairs": len(fx["rates"]), "source": fx["source"]}

    @app.post(f"{P}/conflicts/fetch")
    def conflicts_fetch(role=Depends(compliance)):
        need_live()
        t0 = now()
        with guard("ucdp"):
            ged_raw, acd_raw = download(ucdp.GED_URL), download(ucdp.ACD_URL)
            ged, acd = ucdp.parse_ged(ged_raw, t0), ucdp.parse_acd(acd_raw)
        store.save_snapshot("ucdp_ged", ucdp.GED_URL, {"sha256": sanc_conn.digest(ged_raw), "year": ged["year"], "countries": len(ged["items"])})
        store.save_snapshot("ucdp_acd", ucdp.ACD_URL, {"sha256": sanc_conn.digest(acd_raw), "year": acd["year"], "conflicts": len(acd["items"])})
        store.kv_set("conflicts", ged["items"])
        store.kv_set("conflicts_acd", acd)
        store.kv_set("last:ucdp", {"at": t0, "count": len(ged["items"])})
        audit(role, "conflicts.fetch", {"year": ged["year"], "countries": len(ged["items"]), "events": ged["events_total"], "acd": len(acd["items"])})
        return {"year": ged["year"], "countries": len(ged["items"]), "events": ged["events_total"], "acd_conflicts": len(acd["items"])}

    # ------------------------------------------------------------------ country context: 5 public APIs
    _geo_last = {"t": 0.0}

    @app.post(f"{P}/indicators/fetch")
    def indicators_fetch(role=Depends(analyst)):
        need_live()
        results, errors = {}, {}
        for key, kv_key, fn in (("worldbank_ind", "ind_military", lambda: indicators.military_expenditure(get)),
                                ("unhcr", "ind_refugees", lambda: indicators.refugees_by_origin(get, _day().year - 2)),   # UNHCR lags: last complete year
                                ("ecb", "fx_ecb", lambda: indicators.ecb_rates(get))):
            try:
                results[key] = fn()                     # one failing source must not hide the others
            except Exception as exc:                    # noqa: BLE001 - reported per source, not swallowed
                errors[key] = f"{type(exc).__name__}: {str(exc)[:200]}"
                store.kv_set(f"error:{key}", {"at": now(), "message": errors[key]})
                continue
            store.kv_set(f"error:{key}", None)
            store.kv_set(kv_key, results[key])
            n = len(results[key].get("items") or results[key].get("rates") or {})
            store.kv_set(f"last:{key}", {"at": now(), "count": n})
        mil, ref, ecb = results.get("worldbank_ind") or {}, results.get("unhcr") or {}, results.get("ecb") or {}
        out = {"military_countries": len(mil.get("items", {})), "military_last_updated": mil.get("last_updated"), "refugee_countries": len(ref.get("items", {})),
               "refugee_year": ref.get("year"), "ecb_as_of": ecb.get("as_of"), "ecb_pairs": len(ecb.get("rates", {})), "errors": errors}
        audit(role, "indicators.fetch", out)
        if not results:
            raise HTTPException(502, {"code": "ALL_SOURCES_FAILED", "errors": errors})
        return out

    @app.get(P + "/indicators/country/{iso3}")
    def indicators_country(iso3: str, _=Depends(viewer)):
        iso3 = iso3.upper()
        mil = (store.kv_get("ind_military") or {}).get("items", {}).get(iso3)
        ref = (store.kv_get("ind_refugees") or {}).get("items", {}).get(iso3)
        return {"iso3": iso3, "military_expenditure": mil, "refugees": ref,
                "sources": ["World Bank WDI MS.MIL.XPND.CD (SIPRI)", "UNHCR Refugee Population Statistics"]}

    @app.get(f"{P}/fx/crosscheck")
    def fx_crosscheck(_=Depends(viewer)):
        cnb_fx, ecb = store.kv_get("fx") or {}, store.kv_get("fx_ecb") or {}
        a, b = (cnb_fx.get("rates") or {}).get("EURCZK"), (ecb.get("rates") or {}).get("CZK")
        if not a or not b:
            return {"status": "NOT_RUN", "message": "нужны оба источника: POST /fx/fetch и POST /indicators/fetch"}
        diff = abs(a - b) / b * 100
        return {"status": "OK" if diff < 0.5 else "MISMATCH", "eur_czk_cnb": a, "eur_czk_ecb": b, "diff_pct": round(diff, 3),
                "cnb_as_of": cnb_fx.get("as_of"), "ecb_as_of": ecb.get("as_of")}

    @app.get(f"{P}/geocode")
    def geocode(q: str, role=Depends(analyst)):
        need_live()
        q = q.strip()[:200]
        cached = store.kv_get(f"geo:{q.lower()}")
        if cached is not None:
            return {"cached": True, "result": cached}
        wait = 1.1 - (time.time() - _geo_last["t"])                    # Nominatim policy: at most 1 request per second
        if wait > 0:
            time.sleep(wait)
        _geo_last["t"] = time.time()
        with guard("nominatim"):
            res = indicators.geocode(get, q)
        store.kv_set(f"geo:{q.lower()}", res)
        return {"cached": False, "result": res}

    @app.get(f"{P}/trade")
    def trade(reporter: int = 203, hs: str = "27", flow: str = "M", year: int = 2024, _=Depends(viewer)):
        if flow not in ("M", "X") or not hs.isdigit() or len(hs) > 6:
            raise HTTPException(422, {"message": "flow M|X, hs = 2-6 цифр"})
        key = f"trade:{reporter}:{hs}:{flow}:{year}"
        cached = store.kv_get(key)
        if cached is None:
            need_live()
            with guard("comtrade"):
                cached = indicators.trade_flows(get, reporter, hs, flow, year)
            store.kv_set(key, cached)
            store.kv_set("last:comtrade", {"at": now(), "count": len(cached["partners"])})
        return cached

    # ------------------------------------------------------------------ learning
    @app.post(f"{P}/learning/decisions")
    def learn_decision(body: DecisionIn, firm=Depends(firm_of), role=Depends(owner)):
        t = store.get_tender(body.tender_key)
        if not t:
            raise HTTPException(404, {"message": "тендер не найден"})
        a = assess_tender(t, firm=firm)
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

    # ------------------------------------------------------------------ contacts + tender <-> company matching (owner request 21.10)
    def sees_person(role: str) -> bool:
        return ROLE_RANK[role] >= ROLE_RANK["analyst"]

    def contact_or_404(cid: int) -> dict:
        c = store.get_contact(cid)
        if not c:
            raise HTTPException(404, {"message": "контакт не найден"})
        return c

    def cleaned(doc: dict) -> dict:
        try:
            return contacts_mod.clean(doc)
        except contacts_mod.ContactError as exc:
            raise HTTPException(422, {"code": "CONTACT_INVALID", "fields": exc.fields, "message": str(exc)}) from exc

    @app.get(f"{P}/contacts")
    def contacts_list(status: str | None = Query(default=None, pattern=_enum_rx(contacts_mod.STATUSES)), q: str | None = None, role=Depends(viewer)):
        items = store.list_contacts()
        if status:
            items = [c for c in items if c.get("status") == status]
        if q:
            needle = contacts_mod.flat(q)
            hay = lambda c: " ".join([str(c.get(f) or "") for f in ("company", "products", "event", "country")] + sum((c.get("keywords") or {}).values(), []))  # noqa: E731
            items = [c for c in items if needle in contacts_mod.flat(hay(c))]
        return {"items": [contacts_mod.public_view(c, sees_person(role)) for c in items], "total": len(items), "statuses": list(contacts_mod.STATUSES)}

    @app.post(f"{P}/contacts")
    def contacts_create(body: ContactIn, role=Depends(analyst)):
        doc = cleaned(body.model_dump())
        cid, _ = store.add_contact(doc)
        audit(role, "contact.created", {"contact_id": cid})                       # ids only: no company text, no person data
        return {"id": cid, "contact": contacts_mod.public_view(store.get_contact(cid), True)}

    @app.get(P + "/contacts/{cid}")
    def contact_get(cid: int, role=Depends(viewer)):
        return {"contact": contacts_mod.public_view(contact_or_404(cid), sees_person(role))}

    @app.patch(P + "/contacts/{cid}")
    def contact_patch(cid: int, body: ContactPatch, role=Depends(analyst)):
        old = contact_or_404(cid)
        patch = body.model_dump(exclude_unset=True)
        merged = {f: old.get(f) for f in contacts_mod.FIELDS}
        for f, v in patch.items():
            if v is None and f in ("company", "status", "cpv_prefixes", "keywords"):
                continue                                                         # null never resets a required field
            merged[f] = {**(old.get("keywords") or {}), **v} if f == "keywords" else v   # keyword languages sent replace, others stay
        doc = cleaned(merged)
        changed = sorted(f for f in contacts_mod.FIELDS if doc.get(f) != old.get(f))
        if changed:
            store.update_contact(cid, doc)
            audit(role, "contact.updated", {"contact_id": cid, "fields": changed})  # field names only, never values
        return {"id": cid, "changed": changed, "contact": contacts_mod.public_view(store.get_contact(cid), True)}

    @app.get(P + "/contacts/{cid}/matches")
    def contact_matches(cid: int, k: int = Query(default=10, ge=1, le=50), status: str = Query(default="open", pattern="^(open|all)$"), _=Depends(viewer)):
        return contacts_mod.rank_for_contact(store, contact_or_404(cid), sanctions_index(), status=status, k=k)

    @app.get(P + "/contacts/{cid}/pitch")
    def contact_pitch(cid: int, _=Depends(viewer)):
        """Markdown sales draft with the top-5 open matching tenders (the app has no PDF renderer: markdown only)."""
        c = contact_or_404(cid)
        top = contacts_mod.rank_for_contact(store, c, sanctions_index(), status="open", k=5)["items"]
        md = contacts_mod.pitch_markdown(c, top, _day())
        return PlainTextResponse(md, media_type="text/markdown", headers={"Content-Disposition": f'attachment; filename="pitch-contact-{cid}.md"'})

    @app.post(P + "/contacts/{cid}/lesson")
    def contact_lesson(cid: int, role=Depends(analyst)):
        """Explicit owner/analyst click only: one lesson note into Bossman memory. No person data, e-mail, notes or vote reasons."""
        c = contact_or_404(cid)
        writer = getattr(gateway, "memory_write", None)
        if writer is None:
            audit(role, "contact.lesson", {"contact_id": cid, "status": "NOT_CONNECTED"})
            raise HTTPException(409, {"code": "NOT_CONNECTED", "message": "шлюз Bossman не настроен (WAR_TERMINAL_BOSSMAN_URL / токен)"})
        rows = store.match_feedback()
        labels = [r for (_, c_id), r in contacts_mod.effective_labels(rows).items() if c_id == cid]
        top = contacts_mod.rank_for_contact(store, c, sanctions_index(), status="open", k=5)["items"]
        title, content = contacts_mod.lesson(c, labels, contacts_mod.current_weights(store), top, contacts_mod.precision_report(rows))
        try:
            res = writer(title, content, "lesson", ["war-terminal", "tender-matching"])
        except tr_mod.RouteError as exc:
            code = "ALREADY_EXISTS" if exc.status == 409 else "BOSSMAN_ERROR"
            audit(role, "contact.lesson", {"contact_id": cid, "status": code})
            raise HTTPException(409 if exc.status == 409 else 502, {"code": code, "message": f"Bossman не сохранил урок: {exc}"}) from exc
        audit(role, "contact.lesson", {"contact_id": cid, "status": "SAVED"})
        return {"status": "SAVED", "path": (res or {}).get("path"), "title": title, "content": content}

    @app.post(f"{P}/matches/feedback")
    def match_feedback(body: MatchFeedbackIn, role=Depends(analyst)):
        t = store.get_tender(body.tender_key)
        if not t:
            raise HTTPException(404, {"message": "тендер не найден"})
        c = contact_or_404(body.contact_id)
        res = contacts_mod.record_vote(store, t, c, body.vote, (body.reason or "").strip() or None, role)
        audit(role, "match.feedback", {"feedback_id": res["id"], "contact_id": c["id"], "tender": t["key"], "vote": body.vote})   # the reason text is not audited
        return res

    @app.get(f"{P}/matches/metrics")
    def match_metrics(_=Depends(viewer)):
        return contacts_mod.precision_report(store.match_feedback())

    @app.get(f"{P}/matches/weights")
    def match_weights(_=Depends(viewer)):
        w = contacts_mod.current_weights(store)
        return {"weights": w, "features": len(w), "formula": "w = ln((up+1)/(down+1)); score = base + 10 x sum(w)"}

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

    # ------------------------------------------------------------------ translation (original and translation are separate records)
    @app.get(f"{P}/translate/status")
    def translate_status(_=Depends(viewer)):
        return translator.status()

    @app.post(f"{P}/translate/lookup")
    def translate_lookup(body: TranslateIn, _=Depends(viewer)):
        """Language tags and already-cached translations. No model call, no spend."""
        return {"items": translator.lookup(body.texts, body.target), **translator.status()}

    @app.post(f"{P}/translate")
    def translate_run(body: TranslateIn, role=Depends(analyst)):
        """Owner click: machine-translate the texts that have no cached translation, through free Bossman routes only."""
        out = translator.translate(body.texts, body.target)
        audit(role, "translate.run", {"target": body.target, "texts": len(body.texts), "fresh": out["fresh"], "models": sorted(set(out["models"])),
                                      "statuses": sorted({r["status"] for r in out["items"]})})
        return out

    # ------------------------------------------------------------------ UI
    if UI_DIR.exists():
        app.mount("/ui", StaticFiles(directory=str(UI_DIR), html=True), name="ui")

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/ui/index.html")

    from .tgapp import mount as tg_mount; tg_mount(app, store)   # noqa: E702 - owner-only Telegram Mini App at /tg/ (see tgapp.py)
    return app
