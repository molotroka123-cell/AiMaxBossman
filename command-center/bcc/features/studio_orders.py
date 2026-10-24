"""Studio orders API (authenticated like every feature router, under /api): Direct-Generator jobs sold as a service.

  GET  /studio/price-list                 owner: the effective price list (defaults + <data>/direct-gen/studio/price_list.json)
  POST /studio/orders                     owner: create an order of the client's jobs -> status quoted
  GET  /studio/orders, /orders/{id}       owner: full view (receipt with cost/margin, licence warnings, brand flags);
                                          a client: the client view only (price, status, deliverables, disclosure)
  POST /studio/orders/{id}/quote          owner: re-price a quoted order from the current price list
  POST /studio/orders/{id}/accept         owner or the client: quoted -> accepted (price frozen)
  POST /studio/orders/{id}/start          owner: accepted -> in_work
  POST /studio/orders/{id}/disclose       owner: record «AI-generated · Bossman Motion Studio» on the finished files
  POST /studio/orders/{id}/brands         owner: record that the client owns the listed brands (clears the brand gate)
  POST /studio/orders/{id}/deliver        owner: accepted|in_work -> delivered; refused (409 + problems) unless every job
                                          is completed, every file disclosed, every face job has consent and no
                                          uncleared third-party brand is named. Model licence = owner warning only.
  POST /studio/orders/{id}/cancel         owner or the client: any state before delivered -> cancelled
Who is who: the `X-Participant` header exactly as in direct_gen. The owner ('owner', the default) addresses a client's
orders with `?client=<64-hex key>` (no `client` = the owner's own namespace); a client key addresses only its own
orders and never gets the owner's view. Another client's order id is 404. Every call reads/writes JSON files, so it
runs in a worker thread (asyncio.to_thread): sync reads on the loop froze the whole backend once (24.10).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..direct_gen.orders import DISCLOSURE, OrderBook, OrderError
from ..direct_gen.store import JobStore, valid_participant
from . import Feature
from .direct_gen import _who

router = APIRouter(prefix="/studio", tags=["studio"])


class ItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(max_length=16)
    job_ids: list[Annotated[str, Field(max_length=64)]] = Field(min_length=1, max_length=20)
    # optional per-line price (string or number, > 0); otherwise the price list's price of `kind`
    price: str | float | int | None = None
    face: bool = False
    consent_confirmed: bool = False
    consent_note: str = Field(default="", max_length=200)


class OrderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ItemIn] = Field(min_length=1, max_length=50)
    brief: str = Field(default="", max_length=2000)
    note: str = Field(default="", max_length=500)


class DiscloseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_ids: list[Annotated[str, Field(max_length=64)]] | None = Field(default=None, max_length=1000)
    caption: str = Field(default=DISCLOSURE, max_length=200)


class BrandsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brands: list[Annotated[str, Field(max_length=80)]] = Field(min_length=1, max_length=20)
    note: str = Field(default="", max_length=200)


class CancelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="", max_length=200)


def _book(request: Request) -> OrderBook:
    svc = request.app.state.svc
    book = getattr(svc, "studio_orders", None)
    if book is None:
        dg = getattr(svc, "direct_gen", None)
        store = getattr(dg, "store", None) or JobStore(Path(svc.settings.data_dir))
        book = svc.studio_orders = OrderBook(Path(svc.settings.data_dir), store)
    return book


def _ctx(x_participant: str | None, request: Request, client: str | None) -> tuple[str, str]:
    """(actor, namespace): the caller and whose orders it addresses. Only the owner may name another client."""
    actor = _who(x_participant, request)
    if client is None:
        return actor, actor
    if actor != "owner":
        raise HTTPException(403, {"code": "owner_only", "message": "only the owner may address another client"})
    try:
        return actor, valid_participant(client)
    except ValueError as exc:
        raise HTTPException(422, {"code": "invalid_participant", "message": str(exc)}) from None


def _owner(actor: str) -> None:
    if actor != "owner":
        raise HTTPException(403, {"code": "owner_only", "message": "this step is the studio owner's"})


async def _run(fn, *args):
    try:
        return await asyncio.to_thread(fn, *args)
    except OrderError as exc:
        raise HTTPException(exc.status, exc.detail) from None


async def _client_safe(book: OrderBook, actor: str, ns: str, fn, *args):
    """A client caller gets the client view of the result, never the owner's (cost, margin, licence warnings)."""
    out = await _run(fn, ns, *args)
    if actor == "owner":
        return out
    return await _run(book.get_for_client, ns, out["order_id"])


@router.get("/price-list")
async def price_list(request: Request, x_participant: str | None = Header(default=None)):
    _owner(_who(x_participant, request))
    pl = await _run(_book(request).price_list)
    return {**pl, "prices": {k: str(v) for k, v in pl["prices"].items()},
            "power_watts": str(pl["power_watts"]), "kwh_price": str(pl["kwh_price"])}


@router.post("/orders", status_code=201)
async def create(body: OrderIn, request: Request, client: str | None = None,
                 x_participant: str | None = Header(default=None)):
    actor, ns = _ctx(x_participant, request, client)
    _owner(actor)
    return await _run(_book(request).create, ns, body.model_dump())


@router.get("/orders")
async def orders(request: Request, client: str | None = None, x_participant: str | None = Header(default=None)):
    actor, ns = _ctx(x_participant, request, client)
    book = _book(request)
    return {"orders": await _run(book.list if actor == "owner" else book.list_for_client, ns)}


@router.get("/orders/{order_id}")
async def order(order_id: str, request: Request, client: str | None = None,
                x_participant: str | None = Header(default=None)):
    actor, ns = _ctx(x_participant, request, client)
    book = _book(request)
    return await _run(book.get if actor == "owner" else book.get_for_client, ns, order_id)


@router.post("/orders/{order_id}/quote")
async def quote(order_id: str, request: Request, client: str | None = None,
                x_participant: str | None = Header(default=None)):
    actor, ns = _ctx(x_participant, request, client)
    _owner(actor)
    return await _run(_book(request).quote, ns, order_id)


@router.post("/orders/{order_id}/accept")
async def accept(order_id: str, request: Request, client: str | None = None,
                 x_participant: str | None = Header(default=None)):
    actor, ns = _ctx(x_participant, request, client)
    book = _book(request)
    return await _client_safe(book, actor, ns, book.accept, order_id)


@router.post("/orders/{order_id}/start")
async def start(order_id: str, request: Request, client: str | None = None,
                x_participant: str | None = Header(default=None)):
    actor, ns = _ctx(x_participant, request, client)
    _owner(actor)
    return await _run(_book(request).start, ns, order_id)


@router.post("/orders/{order_id}/disclose")
async def disclose(order_id: str, request: Request, body: DiscloseIn | None = None, client: str | None = None,
                   x_participant: str | None = Header(default=None)):
    actor, ns = _ctx(x_participant, request, client)
    _owner(actor)
    body = body or DiscloseIn()
    return await _run(_book(request).disclose, ns, order_id, body.job_ids, body.caption)


@router.post("/orders/{order_id}/brands")
async def brands(order_id: str, body: BrandsIn, request: Request, client: str | None = None,
                 x_participant: str | None = Header(default=None)):
    actor, ns = _ctx(x_participant, request, client)
    return await _run(_book(request).set_brand_ownership, actor, ns, order_id, body.brands, body.note)


@router.post("/orders/{order_id}/deliver")
async def deliver(order_id: str, request: Request, client: str | None = None,
                  x_participant: str | None = Header(default=None)):
    actor, ns = _ctx(x_participant, request, client)
    _owner(actor)
    return await _run(_book(request).deliver, ns, order_id)


@router.post("/orders/{order_id}/cancel")
async def cancel(order_id: str, request: Request, body: CancelIn | None = None, client: str | None = None,
                 x_participant: str | None = Header(default=None)):
    actor, ns = _ctx(x_participant, request, client)
    book = _book(request)
    return await _client_safe(book, actor, ns, book.cancel, order_id, (body or CancelIn()).reason)


FEATURE = Feature(name="studio_orders", router=router)
