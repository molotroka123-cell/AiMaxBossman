"""`learning.youtube` / `learning.youtube_status` — the chat/pult agent learns from public YouTube lessons.

Thin adapter over bcc.learning_video.ingest (the same orchestration as `tools/youtube_learning_fetch.py`; one tree,
no parallel pipeline). Stream Tube ideas, 24.11.2026: SponsorBlock strip before chunking, RYD source prior,
FetchController (player-client chain, persisted breaker, optional yt-dlp SHA-256 pin, honest BOT_CHECK).

Boundaries:
  * permission `browser.read` (reading the public web; not dangerous): granted per agent -> AUTO, otherwise ASK;
  * global owner STOP is checked fail-closed before anything runs;
  * only public youtube.com / youtu.be URLs, at most MAX_URLS per call; no cookies, no sign-in, no video download;
  * the store is `<data_dir>/learning/youtube/` (claims.jsonl, per-video subtitle folders, breaker state);
  * claims are UNVERIFIED: this tool never says "learned". The answer is a report with exact verdicts.
"""
from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from ..tools import REGISTRY, ToolContext, ToolResult, ToolSpec
from . import Feature

INGEST_TOOL = "learning.youtube"
STATUS_TOOL = "learning.youtube_status"


def store_dir(svc) -> Path:
    return Path(svc.settings.data_dir) / "learning" / "youtube"


def _stop_active(svc) -> bool:
    from ..owner_stop import owner_stop_for
    return owner_stop_for(svc).active


def _summary(rep: dict) -> str:
    lines = ["🎓 YouTube → обучение: %d видео, с субтитрами %d · вердикт %s" % (
        rep["videos"], rep["with_transcript"], rep["verdict"])]
    for r in rep["results"]:
        p = r.get("pipeline") or {}
        prior = (r.get("prior") or {})
        pr = prior.get("prior")
        lines.append("• %s · загрузка %s · реклама %s · claims %s · приоритет %s" % (
            r["video_id"], r["fetch"]["verdict"], (p.get("sponsor_strip") or {}).get("status", r.get("sponsor_status", "—")),
            p.get("claims", 0), "%.2f" % pr if isinstance(pr, float) else prior.get("status", "—")))
    lines.append("Все claims — UNVERIFIED: это кандидаты в знания, а не выученное.")
    return "\n".join(lines)


async def _t_ingest(args: dict, ctx: ToolContext) -> ToolResult:
    from ..learning_video.fetch_control import Breaker, FetchController
    from ..learning_video.ingest import BREAKER_FILE, MAX_URLS, run, video_id_from_url

    urls = args.get("urls") or []
    if isinstance(urls, str):
        urls = [urls]
    if not isinstance(urls, list) or not 1 <= len(urls) <= MAX_URLS:
        return ToolResult(content=f"{INGEST_TOOL}: нужно 1..{MAX_URLS} ссылок YouTube", error=True)
    try:
        for u in urls:
            video_id_from_url(str(u))
    except ValueError as exc:
        return ToolResult(content=f"{INGEST_TOOL}: {exc}", error=True)
    lang = str(args.get("lang") or "ru")
    if lang not in ("ru", "en"):
        return ToolResult(content=f"{INGEST_TOOL}: lang — ru или en", error=True)
    svc = ctx.svc
    if _stop_active(svc):
        return ToolResult(content=f"{INGEST_TOOL}: активен STOP владельца — ничего не запущено", error=True)
    ytdlp = shutil.which("yt-dlp")
    if not ytdlp:
        return ToolResult(content=f"{INGEST_TOOL}: BLOCKED — yt-dlp не найден на этом ПК", error=True)
    out = store_dir(svc)
    ctrl = FetchController(ytdlp=ytdlp, breaker=Breaker(path=out / BREAKER_FILE),
                           expected_sha256=os.getenv("BOSSMAN_YTDLP_SHA256"))
    factory = getattr(svc, "_learning_video_controller", None)  # test seam: an injected controller/http
    http_get = None
    if callable(factory):
        ctrl, http_get = factory(out)
    rep = await asyncio.to_thread(run, [str(u) for u in urls], out, lang=lang,
                                  signals=not bool(args.get("no_signals")), controller=ctrl, http_get=http_get)
    text = _summary(rep)
    return ToolResult(content=text, one_line=text.splitlines()[0], external=True,
                      error=rep["with_transcript"] == 0,
                      data={"verdict": rep["verdict"], "videos": rep["videos"], "with_transcript": rep["with_transcript"],
                            "results": [{"video_id": r["video_id"], "fetch": r["fetch"]["verdict"],
                                         "attempts": r["fetch"].get("attempts", []),
                                         "claims": (r.get("pipeline") or {}).get("claims", 0),
                                         "sponsor": ((r.get("pipeline") or {}).get("sponsor_strip") or {}).get("status")}
                                        for r in rep["results"]]})


async def _t_status(args: dict, ctx: ToolContext) -> ToolResult:
    from ..learning_video.ingest import store_status
    st = await asyncio.to_thread(store_status, store_dir(ctx.svc))
    b = st["breaker"]
    head = "🎓 Обучение YouTube: claims %d (UNVERIFIED) · предохранитель %s" % (
        st["claims_total"], "ОТКРЫТ ещё %d с" % b["open_for_s"] if b.get("open") else
        ("повреждён" if b.get("status") == "CORRUPT" else "закрыт"))
    return ToolResult(content=head, one_line=head, data=st)


SPECS = [
    ToolSpec(
        name=INGEST_TOOL,
        description=("Выучить уроки из публичных YouTube-видео: субтитры через yt-dlp (цепочка клиентов, "
                     "предохранитель), реклама вырезается SponsorBlock, порядок — по рейтингу RYD; результат — "
                     "UNVERIFIED claims в локальном хранилище. Learn from public YouTube lessons (subtitles only)."),
        handler=_t_ingest,
        input_schema={"urls": {"type": "array", "items": {"type": "string"},
                               "description": "1..10 ссылок youtube.com / youtu.be"},
                      "lang": {"type": "string", "enum": ["ru", "en"]},
                      "no_signals": {"type": "boolean", "description": "без SponsorBlock/RYD"}},
        required=["urls"],
        category="read", permission="browser.read", source="builtin", default_effect="ask",
        timeout_seconds=1800.0, idempotent=False, external_output=True,
    ),
    ToolSpec(
        name=STATUS_TOOL,
        description="Состояние обучения по YouTube: число claims, предохранитель загрузок. Read-only.",
        handler=_t_status,
        input_schema={},
        category="read", source="builtin", default_effect="auto",
    ),
]


async def _setup(svc) -> None:
    for spec in SPECS:
        REGISTRY.register(spec)


FEATURE = Feature(name="tools_learning_video", router=None, setup=_setup)
