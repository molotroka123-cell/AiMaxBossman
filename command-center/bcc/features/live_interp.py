"""Live call interpreter — pult routes (owner-only). Thin adapter over bcc.live_interp.

Endpoints (prefix /api):
  GET  /live-interp/status                 capture/ASR/translation readiness + the active session (never starts anything)
  POST /live-interp/start                  {target: "ru"|"en"|..., keep_transcript: bool} — owner click; refused under STOP
  POST /live-interp/stop                   end the active session
  POST /live-interp/feed                   raw PCM16 mono 16 kHz body — for hosts without WASAPI and for tests
  GET  /live-interp/events?after=N         subtitles for the pult overlay (polling)
  GET  /live-interp/export?fmt=srt|md      transcript of the active or last session
Nothing here registers an agent tool: an agent cannot start listening to a call (same rule as telegram_calls).
Bus events: live_interp.subtitle / live_interp.ended (text only, never audio).
"""
from __future__ import annotations

import asyncio
import os
import secrets
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from . import Feature
from ..live_interp import LiveSession, MarianEnRu, Router, whisper_asr
from ..live_interp.capture import capture_status, loopback_chunks
from ..owner_stop import owner_stop_for

router = APIRouter()
MAX_EVENTS = 2000
MAX_FEED_BYTES = 16000 * 2 * 30  # 30 s of audio per POST


class _State:
    def __init__(self):
        self.session: LiveSession | None = None
        self.last: LiveSession | None = None
        self.events: list[dict] = []
        self.seq = 0
        self.lock = threading.Lock()
        self.capture_thread: threading.Thread | None = None
        self.capture_error = ""


def _state(svc) -> _State:
    st = getattr(svc, "_live_interp", None)
    if st is None:
        st = _State()
        svc._live_interp = st
    return st


def _router() -> Router:
    model = os.environ.get("BOSSMAN_VIDEO_TRANSLATION_MODEL", "")
    return Router([MarianEnRu(model)] if model else [])


def _sink(svc, st: _State):
    loop = asyncio.get_event_loop()

    def push(event: dict):
        with st.lock:
            st.seq += 1
            st.events.append({**event, "id": st.seq})
            del st.events[:-MAX_EVENTS]
        name = "live_interp.subtitle" if event.get("type") == "subtitle" else "live_interp." + str(event.get("type"))
        bus = getattr(svc, "bus", None)
        if bus is not None:
            try:
                asyncio.run_coroutine_threadsafe(bus.emit(name, **{k: v for k, v in event.items() if k != "type"}), loop)
            except RuntimeError:
                pass
    return push


def _asr_factory(svc):
    return getattr(svc, "_live_interp_asr", None) or whisper_asr  # test seam: injected ASR


@router.get("/live-interp/status")
async def status(request: Request) -> dict:
    svc = request.app.state.svc
    st = _state(svc)
    s = st.session
    return {"capture": capture_status(), "translation_model": bool(os.environ.get("BOSSMAN_VIDEO_TRANSLATION_MODEL")),
            "global_stop": owner_stop_for(svc).active, "active": s.summary() if s else None,
            "capture_error": st.capture_error or None, "agent_tool": False, "audio_stored": False}


@router.post("/live-interp/start")
async def start(request: Request) -> dict:
    svc = request.app.state.svc
    body = await request.json() if (await request.body()) else {}
    target = str(body.get("target") or "ru").lower()[:2]
    if not target.isalpha():
        raise HTTPException(400, "target must be a two-letter language code")
    if owner_stop_for(svc).active:
        raise HTTPException(409, "активен STOP владельца — перевод звонка не запускается")
    st = _state(svc)
    if st.session and not st.session.ended:
        raise HTTPException(409, "сессия уже идёт")
    keep = bool(body.get("keep_transcript"))
    store = Path(svc.settings.data_dir) / "live_interp" if keep else None
    session = LiveSession(session_id=secrets.token_hex(6), target=target, asr=_asr_factory(svc), router=_router(),
                          stop_check=lambda: owner_stop_for(svc).active, sink=_sink(svc, st), store_dir=store)
    st.session, st.last, st.events, st.capture_error = session, session, [], ""
    capture = capture_status()
    if body.get("capture", True) and capture["status"] == "READY":  # pragma: no cover - Windows audio only
        def run():
            try:
                for chunk in loopback_chunks(lambda: bool(session.ended)):
                    session.feed(chunk)
            except Exception as exc:  # noqa: BLE001
                st.capture_error = type(exc).__name__
                session.end("capture_error")
        st.capture_thread = threading.Thread(target=run, name="live-interp-capture", daemon=True)
        st.capture_thread.start()
    return {"session_id": session.session_id, "target": target, "keep_transcript": keep, "capture": capture}


@router.post("/live-interp/stop")
async def stop(request: Request) -> dict:
    st = _state(request.app.state.svc)
    if not st.session:
        raise HTTPException(404, "нет активной сессии")
    summary = await asyncio.to_thread(st.session.end, "owner")
    st.session = None
    return summary


@router.post("/live-interp/feed")
async def feed(request: Request) -> dict:
    st = _state(request.app.state.svc)
    if not st.session or st.session.ended:
        raise HTTPException(409, "нет активной сессии")
    pcm = await request.body()
    if not pcm or len(pcm) > MAX_FEED_BYTES or len(pcm) % 2:
        raise HTTPException(400, "нужен PCM16 mono 16 kHz, до 30 секунд за запрос")
    events = await asyncio.to_thread(st.session.feed, pcm)
    return {"subtitles": len(events), "ended": st.session.ended or None}


@router.get("/live-interp/events")
async def events(request: Request, after: int = 0) -> dict:
    st = _state(request.app.state.svc)
    with st.lock:
        rows = [e for e in st.events if e["id"] > after]
    return {"events": rows[:500], "last_id": st.seq}


@router.get("/live-interp/export")
async def export(request: Request, fmt: str = "md"):
    st = _state(request.app.state.svc)
    session = st.session or st.last
    if not session:
        raise HTTPException(404, "нет сессии")
    if fmt == "srt":
        return PlainTextResponse(session.export_srt(), media_type="application/x-subrip")
    if fmt == "md":
        return PlainTextResponse(session.export_markdown(), media_type="text/markdown")
    raise HTTPException(400, "fmt: srt или md")


FEATURE = Feature(name="live_interp", router=router)
