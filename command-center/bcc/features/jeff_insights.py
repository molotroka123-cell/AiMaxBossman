"""Owner insights for Jeff 2.0 (page «Jeff · обзор»): participants, narratives, health, trends, weekly digest.

Endpoints (mounted under /api with the normal owner session or token auth; participants have no Command Center
session, so they can never reach them):
  GET  /jeff-insights/overview             — participants table, narrative paths, health, trends
  GET  /jeff-insights/participants         — the participants table only
  GET  /jeff-insights/narratives           — narrative PATHS and metadata (never text)
  GET  /jeff-insights/narratives/{key}     — one narrative's text (owner only, one participant per call)
  GET  /jeff-insights/trends?days=14       — daily series
  GET  /jeff-insights/digest               — the weekly digest text (numbers only, no participant text)
  POST /jeff-insights/digest/send          — put this week's digest into the Pult outbox (once per week)
  GET  /jeff-insights/pult-outbox          — digests waiting for the owner Pult (read by the companion only)
  POST /jeff-insights/pult-outbox/{id}/delivered — the companion acknowledges one sent digest

All data comes from the files Jeff already keeps (``bcc.pit.j2.insights``); nothing here is a second database and
no Telegram id or absolute path leaves the API.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request

from ..pit.config import pit_home
from ..pit.identity import validate_person_key
from ..pit.j2.insights import DEFAULT_TREND_DAYS, InsightsCollector
from . import Feature
from .jeff_settings import _cc_data_dir, _current, _paths, participants

router = APIRouter()


def _collector(request: Request) -> tuple[InsightsCollector, dict[str, str], bool]:
    path, jeff_dir, cfg = _paths(request)
    labels: dict[str, str] = {}
    try:
        overlay, _, _ = _current(path)
        labels = {row["key"]: row["label"] for row in participants(_cc_data_dir(request), jeff_dir, cfg, overlay)}
    except Exception:  # noqa: BLE001 - labels are a nicety; neutral labels are used without them
        labels = {}
    return InsightsCollector(pit_home(jeff_dir), clock=time.time), labels, bool(cfg)


@router.get("/jeff-insights/overview")
async def overview(request: Request):
    collector, labels, configured = _collector(request)
    data = collector.overview(labels)
    data["configured"] = configured
    data["digest_week"] = collector.last_digest_week()
    return data


@router.get("/jeff-insights/participants")
async def list_participants(request: Request):
    collector, labels, configured = _collector(request)
    return {"configured": configured, "participants": collector.participants(labels)}


@router.get("/jeff-insights/narratives")
async def list_narratives(request: Request):
    collector, labels, configured = _collector(request)
    return {"configured": configured, "narratives": collector.narratives(labels)}


@router.get("/jeff-insights/narratives/{person_key}")
async def get_narrative(person_key: str, request: Request):
    try:
        key = validate_person_key(person_key)
    except ValueError:
        raise HTTPException(422, "Участник задаётся ключом Jeff (64 hex), не Telegram ID.") from None
    collector, labels, _ = _collector(request)
    found = collector.narrative_text(key)
    if found is None:
        raise HTTPException(404, "Для этого участника нарратива ещё нет.")
    return {**found, "label": labels.get(key) or f"Участник {key[:6]}"}


@router.get("/jeff-insights/trends")
async def get_trends(request: Request, days: int = DEFAULT_TREND_DAYS):
    collector, _, _ = _collector(request)
    return collector.trends(days)


@router.get("/jeff-insights/digest")
async def get_digest(request: Request):
    collector, _, _ = _collector(request)
    return {**collector.weekly_digest(), "last_sent_week": collector.last_digest_week()}


@router.post("/jeff-insights/digest/send")
async def send_digest(request: Request):
    collector, _, _ = _collector(request)
    return await collector.deliver_digest()


# The owner Pult (Telegram companion, owner only) drains the digest outbox: it reads the pending items, sends each to
# the OWNER, then acknowledges the id. Delivery is at-least-once (ack after send); the companion also remembers what it
# sent, so a lost ack repeats only the ack. Same owner-only auth as the rest of this router.
@router.get("/jeff-insights/pult-outbox")
async def pult_outbox(request: Request, limit: int = 5):
    collector, _, _ = _collector(request)
    return {"items": collector.pending_pult_items(limit)}


@router.post("/jeff-insights/pult-outbox/{item_id}/delivered")
async def pult_outbox_delivered(item_id: str, request: Request):
    collector, _, _ = _collector(request)
    if not collector.ack_pult_item(item_id):
        raise HTTPException(404, "Такой записи в исходящих Пульта нет.")
    return {"id": item_id, "delivered": True}


FEATURE = Feature(name="jeff_insights", router=router)
