from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import APP_ID, __version__
from .collector import Collector
from .events import EventBus
from .grants import GrantBook, GrantError
from .local_runtime import LocalRuntime, limits_for
from .policy import PolicyDenied
from .prompt_cache import wrap_cloud
from .provenance import PassportError
from .sources import Fetcher
from .store import Store
from .ui import page


class GrantIn(BaseModel):
    author: str
    source_or_subject: str
    reason: str
    clause: str
    ttl_hours: int = Field(ge=1, le=2160)


class FactIn(BaseModel):
    subject: str
    predicate: str
    object: str | int | float | dict | list
    source: str
    url: str
    method: str
    license: str
    confidence: float = Field(ge=0, le=1)
    grant_clause: str | None = None


class FetchIn(BaseModel):
    url: str


class ExportIn(BaseModel):
    subject: str


class StubTransport:
    def get_text(self, url: str, headers: dict | None = None) -> str:
        raise HTTPException(501, "live HTTP is opt-in; inject a transport")


def build_app(store: Store | None = None, transport=None) -> FastAPI:
    api = FastAPI(title="OSIRIS", version=__version__)
    st = store or Store()
    bus = EventBus()
    bus.on_any(lambda topic, payload: st.journal("bus." + topic, payload.get("subject") if isinstance(payload, dict) else None, payload if isinstance(payload, dict) else {}))
    fetcher = Fetcher(st, transport or StubTransport())
    col = Collector(st, bus, fetcher)
    grants = GrantBook(st)
    local = LocalRuntime()

    @api.get("/")
    def home():
        return page()

    @api.get("/health")
    def health():
        return {
            "status": "healthy",
            "app": APP_ID,
            "version": __version__,
            "bind": "127.0.0.1",
            "level0": "sealed",
            "local_session_cap": limits_for("local").session_token_cap,
            "storage": "sqlite",
        }

    @api.get("/capabilities")
    def caps():
        return {
            "standalone": True,
            "imports_bossman": False,
            "level0_sealed": True,
            "grants": True,
            "provenance": True,
            "local_runtime_hook": True,
        }

    @api.get("/metrics")
    def metrics():
        return st.metrics()

    @api.get("/api/journal")
    def journal(limit: int = 100):
        return {"events": st.journal_list(limit)}

    @api.get("/api/grants")
    def grant_list():
        grants.expire_due()
        return {"grants": st.grant_list()}

    @api.post("/api/grants")
    def grant_issue(body: GrantIn):
        try:
            return grants.issue(
                author=body.author,
                source_or_subject=body.source_or_subject,
                reason=body.reason,
                clause=body.clause,
                ttl_hours=body.ttl_hours,
            )
        except PolicyDenied as e:
            raise HTTPException(403, str(e)) from e
        except GrantError as e:
            raise HTTPException(422, str(e)) from e

    @api.post("/api/grants/{gid}/revoke")
    def grant_revoke(gid: str, author: str = "owner"):
        try:
            return grants.revoke(gid, author=author)
        except KeyError:
            raise HTTPException(404, "grant not found")

    @api.post("/api/facts")
    def fact_add(body: FactIn):
        try:
            return col.record_org_fact(
                subject=body.subject,
                predicate=body.predicate,
                obj=body.object,
                source=body.source,
                url=body.url,
                method=body.method,
                license=body.license,
                confidence=body.confidence,
                grant_clause=body.grant_clause,
            )
        except (PolicyDenied, PassportError) as e:
            code = 403 if isinstance(e, PolicyDenied) else 422
            raise HTTPException(code, str(e)) from e

    @api.get("/api/facts")
    def fact_list(subject: str | None = None):
        return {"facts": st.fact_list(subject)}

    @api.get("/api/graph")
    def graph():
        return col.graph.snapshot()

    @api.post("/api/fetch")
    def fetch(body: FetchIn):
        try:
            return col.fetch_page(body.url)
        except PolicyDenied as e:
            raise HTTPException(403, str(e)) from e
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, str(e)) from e

    @api.post("/api/export")
    def export(body: ExportIn):
        try:
            return col.export_outbound(body.subject)
        except PolicyDenied as e:
            raise HTTPException(403, str(e)) from e

    @api.post("/api/local/complete")
    def local_complete(prompt: str):
        return local.complete(prompt)

    @api.post("/api/cloud/cache-wrap")
    def cloud_wrap(prompt: str):
        return wrap_cloud(prompt)

    return api


app = build_app()
