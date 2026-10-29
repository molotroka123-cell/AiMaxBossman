"""Studio orders API (authenticated like every feature router, under /api): Direct-Generator jobs sold as a service.

  GET  /studio/price-list                 owner: the effective price list (defaults + <data>/direct-gen/studio/price_list.json)
  POST /studio/clients                    owner: issue (or rotate) a client's token; the raw token is returned ONCE
  GET  /studio/clients                    owner: issued tokens (no hashes) + clients with orders and no token
  POST /studio/clients/{key}/revoke       owner: the client's token stops working at once
  POST /studio/orders                     owner: create an order of the client's jobs -> quoted (or draft)
  GET  /studio/orders, /orders/{id}       owner: full view (receipt with cost/margin, licence warnings, brand flags);
                                          a client: the client view only (price, status, deliverables, disclosure)
  GET  /studio/orders/{id}/events         owner: the append-only event log of the order
  POST /studio/orders/{id}/quote          owner: price a draft / re-price a quoted order from the current price list
  POST /studio/orders/{id}/accept         owner or the client: quoted -> accepted (price frozen)
  POST /studio/orders/{id}/start          owner: accepted -> preflight -> generating (preflight: jobs, consent, brands);
                                          rework / qc_failed / reconcile_required -> generating (new attempt)
  POST /studio/orders/{id}/qc             owner: {passed, note}: -> qc_passed -> owner_review, or -> qc_failed
  POST /studio/orders/{id}/approve        owner: {answer: "yes"}: owner_review -> approved, bound to the file hashes
  POST /studio/orders/{id}/rework         owner: qc_failed | owner_review | approved -> rework
  POST /studio/orders/{id}/reconcile      owner: generating -> reconcile_required (unknown outcome mid-render)
  POST /studio/orders/{id}/disclose       owner: record «AI-generated · Bossman Motion Studio» on the finished files
  POST /studio/orders/{id}/watermark      owner: record the visible watermark on the finished files
  POST /studio/orders/{id}/brands         owner: record that the client owns the listed brands (clears the brand gate)
  POST /studio/orders/{id}/deliver        owner: approved -> delivered; refused (409 + problems) unless every job is
                                          completed, disclosed and watermarked, every face job has consent, no
                                          uncleared third-party brand is named and the owner's «yes» covers the
                                          current files. Model licence = owner warning only.
  POST /studio/orders/{id}/close          owner: delivered -> closed
  POST /studio/orders/{id}/cancel         owner or the client: any state before delivered -> cancelled
Every POST step takes an optional `Idempotency-Key` header: a retry with the same key returns the stored state
(`replayed: true`), the same key for another request is 409.

Who is who (F7, TZ 2.5 M9): a CLIENT is identified only by its server-issued token in `X-Studio-Client-Token`; the
token resolves to the client's participant key. An `X-Participant` header naming a client without a token is 403
(`client_token_required`); a wrong, revoked or expired token is 401. The OWNER is the app's owner credential
(`request.state.principal.role == "owner"`, set by the shared token/session auth) with no client token; it addresses a
client's orders with `?client=<64-hex key>`. An agent-scoped token never gets the owner view. Another client's order
id is 404. Every call reads/writes JSON files, so it runs in a worker thread (asyncio.to_thread): sync reads on the
loop froze the whole backend once (24.10).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..direct_gen.orders import DISCLOSURE, PRESETS, WATERMARK, OrderBook, OrderError
from ..direct_gen.store import JobStore, valid_participant
from . import Feature
from .direct_gen import _who

router = APIRouter(prefix="/studio", tags=["studio"])
CLIENT_TOKEN_HEADER = "x-studio-client-token"


class ItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(max_length=16)
    job_ids: list[Annotated[str, Field(max_length=64)]] = Field(min_length=1, max_length=20)
    # optional per-line price (string or number, > 0); otherwise the price list's price of `kind`
    price: str | float | int | None = None
    face: bool = False
    consent_confirmed: bool = False
    consent_note: str = Field(default="", max_length=200)
    source_rights_required: bool = False
    source_rights_confirmed: bool | None = None
    source_rights_note: str = Field(default="", max_length=200)


class OrderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ItemIn] = Field(min_length=1, max_length=50)
    brief: str = Field(default="", max_length=2000)
    note: str = Field(default="", max_length=500)
    draft: bool = False
    preset: str | None = Field(default=None, max_length=64)
    source_rights_required: bool = False
    source_rights_confirmed: bool | None = None
    source_rights_note: str = Field(default="", max_length=200)
    client_source_hashes: list[Annotated[str, Field(max_length=128)]] = Field(default_factory=list, max_length=50)


class PresetOrderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preset: str = Field(default="product_spin", max_length=64)
    brief: str = Field(default="", max_length=2000)
    note: str = Field(default="", max_length=500)
    price: str | float | int | None = None
    face: bool = False
    consent_confirmed: bool = True
    consent_note: str = Field(default="", max_length=200)
    source_rights_confirmed: bool = True
    source_rights_note: str = Field(default="client owns product source assets", max_length=200)
    seed: int = Field(default=42, ge=0)


class SourceRightsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: bool = True
    note: str = Field(default="", max_length=200)


class DeliveryLinkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ttl_hours: int | float = Field(default=72, gt=0, le=8760)


class RenderPresetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    simulate_kill_mid_render: bool = False


class DiscloseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_ids: list[Annotated[str, Field(max_length=64)]] | None = Field(default=None, max_length=1000)
    caption: str = Field(default=DISCLOSURE, max_length=200)


class WatermarkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_ids: list[Annotated[str, Field(max_length=64)]] | None = Field(default=None, max_length=1000)
    mark: str = Field(default=WATERMARK, max_length=120)


class BrandsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brands: list[Annotated[str, Field(max_length=80)]] = Field(min_length=1, max_length=20)
    note: str = Field(default="", max_length=200)


class ReasonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="", max_length=200)


CancelIn = ReasonIn


class QcIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passed: bool
    note: str = Field(default="", max_length=200)


class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(max_length=16)


class ClientIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client: str | None = Field(default=None, max_length=64)
    label: str = Field(default="", max_length=80)
    ttl_days: int | None = Field(default=None, ge=1, le=365)


def _book(request: Request) -> OrderBook:
    svc = request.app.state.svc
    book = getattr(svc, "studio_orders", None)
    if book is None:
        dg = getattr(svc, "direct_gen", None)
        store = getattr(dg, "store", None) or JobStore(Path(svc.settings.data_dir))
        book = svc.studio_orders = OrderBook(Path(svc.settings.data_dir), store)
    return book


def _owner_credential(request: Request) -> None:
    """The owner view needs the owner's own credential; an agent-scoped token (or no principal at all) never has it."""
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(401, {"code": "owner_credential_required", "message": "owner authentication required"})
    if getattr(principal, "role", None) != "owner":
        raise HTTPException(403, {"code": "owner_credential_required",
                                  "message": "an agent-scoped token cannot act as the studio owner"})


async def _ctx(x_participant: str | None, request: Request, client: str | None) -> tuple[str, str]:
    """(actor, namespace): the caller and whose orders it addresses (F7: a client only by its server-side token)."""
    token = request.headers.get(CLIENT_TOKEN_HEADER)
    if token is not None:
        key = await _run(_book(request).tokens.resolve, token.strip())     # clients.json: off the loop
        if key is None:
            raise HTTPException(401, {"code": "invalid_client_token", "message": "unknown, revoked or expired token"})
        claimed = _who(x_participant, request) if (x_participant is not None
                                                   or request.headers.get("x-bcc-participant") is not None) else key
        if claimed != key:
            raise HTTPException(403, {"code": "participant_mismatch",
                                      "message": "the participant header does not match the client token"})
        if client is not None:
            raise HTTPException(403, {"code": "owner_only", "message": "only the owner may address another client"})
        return key, key
    if _who(x_participant, request) != "owner":
        raise HTTPException(403, {"code": "client_token_required",
                                  "message": f"a client is identified by its {CLIENT_TOKEN_HEADER.title()} only"})
    _owner_credential(request)
    if client is None:
        return "owner", "owner"
    try:
        return "owner", valid_participant(client)
    except ValueError as exc:
        raise HTTPException(422, {"code": "invalid_participant", "message": str(exc)}) from None


def _owner(actor: str) -> None:
    if actor != "owner":
        raise HTTPException(403, {"code": "owner_only", "message": "this step is the studio owner's"})


def _key(request: Request) -> str | None:
    return request.headers.get("idempotency-key")


async def _run(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except OrderError as exc:
        raise HTTPException(exc.status, exc.detail) from None


async def _client_safe(book: OrderBook, caller: str, ns: str, fn, *args, **kwargs):
    """A client caller gets the client view of the result, never the owner's (cost, margin, licence warnings)."""
    out = await _run(fn, ns, *args, **kwargs)
    if caller == "owner":
        return out
    view = await _run(book.get_for_client, ns, out["order_id"])
    return view | ({"replayed": True} if out.get("replayed") else {})


async def _owner_ctx(x_participant: str | None, request: Request) -> None:
    actor, _ = await _ctx(x_participant, request, None)
    _owner(actor)


@router.get("/price-list")
async def price_list(request: Request, x_participant: str | None = Header(default=None)):
    await _owner_ctx(x_participant, request)
    pl = await _run(_book(request).price_list)
    return {**pl, "prices": {k: str(v) for k, v in pl["prices"].items()},
            "power_watts": str(pl["power_watts"]), "kwh_price": str(pl["kwh_price"])}


@router.post("/clients", status_code=201)
async def issue_client(body: ClientIn, request: Request, x_participant: str | None = Header(default=None)):
    await _owner_ctx(x_participant, request)
    return await _run(_book(request).tokens.issue, body.client, body.label, body.ttl_days)


@router.get("/clients")
async def clients(request: Request, x_participant: str | None = Header(default=None)):
    await _owner_ctx(x_participant, request)
    return await _run(_book(request).client_tokens)


@router.post("/clients/{key}/revoke")
async def revoke_client(key: str, request: Request, x_participant: str | None = Header(default=None)):
    await _owner_ctx(x_participant, request)
    return await _run(_book(request).tokens.revoke, key)


@router.post("/orders", status_code=201)
async def create(body: OrderIn, request: Request, client: str | None = None,
                 x_participant: str | None = Header(default=None)):
    actor, ns = await _ctx(x_participant, request, client)
    _owner(actor)
    return await _run(_book(request).create, ns, body.model_dump())


@router.get("/orders")
async def orders(request: Request, client: str | None = None, x_participant: str | None = Header(default=None)):
    actor, ns = await _ctx(x_participant, request, client)
    book = _book(request)
    return {"orders": await _run(book.list if actor == "owner" else book.list_for_client, ns)}


@router.get("/orders/{order_id}")
async def order(order_id: str, request: Request, client: str | None = None,
                x_participant: str | None = Header(default=None)):
    actor, ns = await _ctx(x_participant, request, client)
    book = _book(request)
    return await _run(book.get if actor == "owner" else book.get_for_client, ns, order_id)


@router.get("/orders/{order_id}/events")
async def events(order_id: str, request: Request, client: str | None = None,
                 x_participant: str | None = Header(default=None)):
    actor, ns = await _ctx(x_participant, request, client)
    _owner(actor)
    return {"events": await _run(_book(request).events, ns, order_id)}


@router.post("/orders/{order_id}/quote")
async def quote(order_id: str, request: Request, client: str | None = None,
                x_participant: str | None = Header(default=None)):
    actor, ns = await _ctx(x_participant, request, client)
    _owner(actor)
    return await _run(_book(request).quote, ns, order_id, key=_key(request))


@router.post("/orders/{order_id}/accept")
async def accept(order_id: str, request: Request, client: str | None = None,
                 x_participant: str | None = Header(default=None)):
    actor, ns = await _ctx(x_participant, request, client)
    book = _book(request)
    return await _client_safe(book, actor, ns, book.accept, order_id, key=_key(request),
                              actor="owner" if actor == "owner" else "client")


@router.post("/orders/{order_id}/start")
async def start(order_id: str, request: Request, client: str | None = None,
                x_participant: str | None = Header(default=None)):
    actor, ns = await _ctx(x_participant, request, client)
    _owner(actor)
    return await _run(_book(request).start, ns, order_id, key=_key(request))


@router.post("/orders/{order_id}/qc")
async def qc(order_id: str, body: QcIn, request: Request, client: str | None = None,
             x_participant: str | None = Header(default=None)):
    actor, ns = await _ctx(x_participant, request, client)
    _owner(actor)
    return await _run(_book(request).qc, ns, order_id, body.passed, body.note, key=_key(request))


@router.post("/orders/{order_id}/approve")
async def approve(order_id: str, body: ApproveIn, request: Request, client: str | None = None,
                  x_participant: str | None = Header(default=None)):
    actor, ns = await _ctx(x_participant, request, client)
    _owner(actor)
    return await _run(_book(request).approve, actor, ns, order_id, body.answer, key=_key(request))


@router.post("/orders/{order_id}/rework")
async def rework(order_id: str, request: Request, body: ReasonIn | None = None, client: str | None = None,
                 x_participant: str | None = Header(default=None)):
    actor, ns = await _ctx(x_participant, request, client)
    _owner(actor)
    return await _run(_book(request).rework, ns, order_id, (body or ReasonIn()).reason, key=_key(request))


@router.post("/orders/{order_id}/reconcile")
async def reconcile(order_id: str, request: Request, body: ReasonIn | None = None, client: str | None = None,
                    x_participant: str | None = Header(default=None)):
    actor, ns = await _ctx(x_participant, request, client)
    _owner(actor)
    return await _run(_book(request).reconcile, ns, order_id, (body or ReasonIn()).reason, key=_key(request))


@router.post("/orders/{order_id}/disclose")
async def disclose(order_id: str, request: Request, body: DiscloseIn | None = None, client: str | None = None,
                   x_participant: str | None = Header(default=None)):
    actor, ns = await _ctx(x_participant, request, client)
    _owner(actor)
    body = body or DiscloseIn()
    return await _run(_book(request).disclose, ns, order_id, body.job_ids, body.caption, key=_key(request))


@router.post("/orders/{order_id}/watermark")
async def watermark(order_id: str, request: Request, body: WatermarkIn | None = None, client: str | None = None,
                    x_participant: str | None = Header(default=None)):
    actor, ns = await _ctx(x_participant, request, client)
    _owner(actor)
    body = body or WatermarkIn()
    return await _run(_book(request).watermark, ns, order_id, body.job_ids, body.mark, key=_key(request))


@router.post("/orders/{order_id}/brands")
async def brands(order_id: str, body: BrandsIn, request: Request, client: str | None = None,
                 x_participant: str | None = Header(default=None)):
    actor, ns = await _ctx(x_participant, request, client)
    return await _run(_book(request).set_brand_ownership, actor, ns, order_id, body.brands, body.note,
                      key=_key(request))


@router.post("/orders/{order_id}/deliver")
async def deliver(order_id: str, request: Request, client: str | None = None,
                  x_participant: str | None = Header(default=None)):
    actor, ns = await _ctx(x_participant, request, client)
    _owner(actor)
    return await _run(_book(request).deliver, ns, order_id, key=_key(request))


@router.post("/orders/{order_id}/close")
async def close(order_id: str, request: Request, client: str | None = None,
                x_participant: str | None = Header(default=None)):
    actor, ns = await _ctx(x_participant, request, client)
    _owner(actor)
    return await _run(_book(request).close, ns, order_id, key=_key(request))


@router.post("/orders/{order_id}/cancel")
async def cancel(order_id: str, request: Request, body: CancelIn | None = None, client: str | None = None,
                 x_participant: str | None = Header(default=None)):
    actor, ns = await _ctx(x_participant, request, client)
    book = _book(request)
    return await _client_safe(book, actor, ns, book.cancel, order_id, (body or CancelIn()).reason,
                              key=_key(request), actor="owner" if actor == "owner" else "client")


@router.get("/presets")
async def presets(request: Request, x_participant: str | None = Header(default=None)):
    await _owner_ctx(x_participant, request)
    return {"presets": PRESETS}


@router.post("/orders/preset", status_code=201)
async def create_preset(body: PresetOrderIn, request: Request, client: str | None = None,
                        x_participant: str | None = Header(default=None)):
    actor, ns = await _ctx(x_participant, request, client)
    _owner(actor)
    return await _run(_book(request).create_preset_order, ns, body.preset, body.brief,
                      note=body.note, price=body.price, face=body.face,
                      consent_confirmed=body.consent_confirmed, consent_note=body.consent_note,
                      source_rights_confirmed=body.source_rights_confirmed,
                      source_rights_note=body.source_rights_note, seed=body.seed)


@router.post("/orders/{order_id}/source-rights")
async def source_rights(order_id: str, body: SourceRightsIn | None = None, request: Request = None,
                        client: str | None = None, x_participant: str | None = Header(default=None)):
    actor, ns = await _ctx(x_participant, request, client)
    body = body or SourceRightsIn()
    return await _client_safe(_book(request), actor, ns, _book(request).confirm_source_rights,
                              order_id, body.confirmed, body.note, key=_key(request),
                              actor="owner" if actor == "owner" else "client")


@router.post("/orders/{order_id}/render-preset")
async def render_preset(order_id: str, body: RenderPresetIn | None = None, request: Request = None,
                        client: str | None = None, x_participant: str | None = Header(default=None)):
    actor, ns = await _ctx(x_participant, request, client)
    _owner(actor)
    body = body or RenderPresetIn()
    return await _run(_book(request).render_preset, ns, order_id,
                      simulate_kill_mid_render=body.simulate_kill_mid_render, key=_key(request))


@router.get("/orders/{order_id}/manifest")
async def order_manifest(order_id: str, request: Request, client: str | None = None,
                         x_participant: str | None = Header(default=None)):
    actor, ns = await _ctx(x_participant, request, client)
    _owner(actor)
    o = await _run(_book(request).get, ns, order_id)
    return o.get("manifest") or {}


@router.get("/orders/{order_id}/contact-sheet")
async def contact_sheet(order_id: str, request: Request, client: str | None = None,
                        x_participant: str | None = Header(default=None)):
    actor, ns = await _ctx(x_participant, request, client)
    _owner(actor)
    o = await _run(_book(request).get, ns, order_id)
    return {
        "order_id": o["order_id"],
        "preset": o.get("preset"),
        "contact_sheet_path": o.get("contact_sheet_path"),
        "qc_report_path": o.get("qc_report_path"),
        "qc_metrics": o.get("qc_metrics"),
    }


@router.post("/orders/{order_id}/delivery-link")
async def issue_delivery_link(order_id: str, body: DeliveryLinkIn | None = None, request: Request = None,
                              client: str | None = None, x_participant: str | None = Header(default=None)):
    actor, ns = await _ctx(x_participant, request, client)
    _owner(actor)
    body = body or DeliveryLinkIn()
    return await _run(_book(request).issue_delivery_link, ns, order_id, ttl_hours=body.ttl_hours, key=_key(request))


@router.post("/orders/{order_id}/delivery-link/revoke")
async def revoke_delivery_link(order_id: str, body: ReasonIn | None = None, request: Request = None,
                               client: str | None = None, x_participant: str | None = Header(default=None)):
    actor, ns = await _ctx(x_participant, request, client)
    _owner(actor)
    body = body or ReasonIn()
    return await _run(_book(request).revoke_delivery_link, ns, order_id, reason=body.reason, key=_key(request))


@router.get("/delivery/{token}")
async def open_delivery_link(token: str, request: Request, x_participant: str | None = Header(default=None)):
    book = _book(request)
    caller_client: str | None = None
    ctok = request.headers.get(CLIENT_TOKEN_HEADER)
    if ctok is not None:
        caller_client = await _run(book.tokens.resolve, ctok.strip())
        if caller_client is None:
            raise HTTPException(404, {"code": "delivery_link_not_found", "message": "delivery link not found"})
    return await _run(book.open_delivery_link, token, caller_client=caller_client)


FEATURE = Feature(name="studio_orders", router=router)

