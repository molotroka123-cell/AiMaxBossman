"""Own-recording pipeline: rights gate -> transcript -> highlights -> 9:16 render -> ffprobe validation -> metadata -> queue."""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Callable

from . import highlights as hl_mod
from . import media, metadata, rights
from . import transcribe as tr_mod
from .store import Store


def enqueue(store: Store, source_id: str, doc: dict, state: str) -> dict:
    """The ONLY way an item enters the queue: the rights gate runs here, every time."""
    rights.check(store.get_source(source_id))
    return store.add_short(source_id, doc, state=state)


def render_short(store: Store, source: dict, hl: dict, transcript: dict, *, out_dir: Path, info: dict,
                 chat: Callable | None) -> dict:
    rights.check(store.get_source(source["id"]))              # re-check right before cutting (file may have changed)
    iid = "sh_" + uuid.uuid4().hex[:10]
    work = out_dir / iid
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    start, dur = hl["start"], min(hl["dur"], media.MAX_S)
    face = media.face_center_x(source["path"], start, dur)
    if face["faces"] and not source.get("people_in_frame"):
        source = store.mark_people_detected(source["id"], {"method": face["method"], "segment": [start, start + dur], "frames_with_face": face["faces"]})
    plan = media.plan_frame(info["width"], info["height"], face["x"])
    words = tr_mod.words_in(transcript, start, start + dur)
    chunks = media.chunk_words(words)
    if source.get("has_burned_captions"):
        # the source already shows this speech as on-screen text: never show the same sentence twice
        ass_name, captions = None, {"chunks": 0, "language": transcript.get("language"), "burned": False,
                                    "reason": "источник уже содержит текст речи на экране (has_burned_captions): повтор не добавляем"}
    else:
        media.write_ass(work / "subs.ass", chunks, plan["out_w"], plan["out_h"],
                        margin_v_frac=float(source.get("caption_margin_v") or 0.22))   # below the face by default
        ass_name, captions = "subs.ass", {"chunks": len(chunks), "language": transcript.get("language"), "burned": True}
    out = work / f"{iid}.mp4"
    media.render_clip(source["path"], out, start=start, dur=dur, src_w=info["width"], src_h=info["height"],
                      face_x=face["x"], ass_name=ass_name, work=work)
    render_s = round(time.time() - t0, 1)
    val = media.validate_output(out, expected_w=plan["out_w"], expected_h=plan["out_h"], expected_s=dur, src_w=info["width"])
    media.poster(out, work / "poster.jpg", at=min(1.5, dur / 3))
    t1 = time.time()
    text = tr_mod.text_in(transcript, start, start + dur) or " ".join(c["text"] for c in chunks)
    lang = "ru" if transcript.get("language") == "ru" else "en"
    meta = metadata.generate(text, chat=chat, lang=lang, synthetic=metadata.needs_disclosure(source),
                             credit=source.get("credit"))
    doc = {"id": iid, "kind": "clip", "file": str(out), "poster": str(work / "poster.jpg"), "segment": {"start": start, "end": start + dur, "dur": dur},
           "highlight": {k: hl.get(k) for k in ("score", "heuristic", "why", "llm", "signals")}, "framing": {**plan, "face": face},
           "captions": captions,
           "validation": val, "metadata": meta, "privacy": "private", "ai_generated": bool(source.get("ai_generated")),
           "timings": {"render_s": render_s, "metadata_s": round(time.time() - t1, 1)}}
    if val["ok"] and meta["title"]:
        return enqueue(store, source["id"], doc, "ready")
    return enqueue(store, source["id"], doc, "draft")


def process_source(store: Store, source_id: str, data_dir: Path, *, k: int = 3, chat: Callable | None = None,
                   whisper_model: str = "small", transcriber: Callable | None = None, actor: str = "editor",
                   progress: Callable[[str], None] | None = None) -> dict:
    say = progress or (lambda m: None)
    src = store.get_source(source_id)
    rights.check(src)                                          # refuses before any work is done
    t_all = time.time()
    timings = {}
    info = media.probe(src["path"])
    if not info["has_video"] or not info["has_audio"]:
        raise media.RenderError("источнику нужны видео и звук")
    say("transcript")
    t = time.time()
    fp = src["rights_approval"].get("fingerprint") or rights.fingerprint(src["path"])
    tr = (transcriber or tr_mod.transcribe)(src["path"], model=whisper_model, cache_dir=data_dir / "cache", cache_key=fp)
    timings["transcript_s"] = round(time.time() - t, 1)
    say("signals")
    t = time.time()
    energy = media.audio_energy(src["path"])
    scenes = media.scene_changes(src["path"])
    timings["signals_s"] = round(time.time() - t, 1)
    say("highlights")
    t = time.time()
    hl = hl_mod.find_highlights(tr, energy, scenes["cuts"], k=k, chat=chat,
                                max_s=min(media.MAX_S, max(15.0, info["duration"])))
    timings["highlights_s"] = round(time.time() - t, 1)
    store.audit(actor, "highlights.found", {"source": source_id, "candidates": hl["candidates_total"], "chosen": len(hl["highlights"]),
                                            "llm": hl["llm_status"]})
    items = []
    for n, h in enumerate(hl["highlights"]):
        say(f"render {n + 1}/{len(hl['highlights'])}")
        t = time.time()
        it = render_short(store, src, h, tr, out_dir=data_dir / "shorts", info=info, chat=chat)
        it = store.update_short(it["id"], {"timings": {**it["timings"], "total_item_s": round(time.time() - t, 1)}})
        store.audit(actor, "short.created", {"id": it["id"], "source": source_id, "state": it["state"],
                                             "valid": it["validation"]["ok"], "segment": it["segment"]})
        items.append(it)
    timings["total_s"] = round(time.time() - t_all, 1)
    return {"source": source_id, "probe": info, "transcript": {"language": tr.get("language"), "segments": len(tr["segments"]),
            "model": tr.get("model"), "cached": tr.get("cached", False)}, "scenes": {"method": scenes["method"], "cuts": len(scenes["cuts"])},
            "highlights": hl, "items": items, "timings": timings}


def rerender(store: Store, iid: str, data_dir: Path, *, chat: Callable | None = None, whisper_model: str = "small",
             transcriber: Callable | None = None, actor: str = "editor") -> dict:
    """Re-cut the same moment with the source's CURRENT settings (captions, framing); the old unapproved item is
    withdrawn (rejected, superseded_by). Approved/published items are never touched here."""
    old = store.get_short(iid)
    if old is None:
        raise KeyError(iid)
    if old["state"] not in ("draft", "ready") or old.get("kind") != "clip":
        raise ValueError(f"перерендер только для неодобренных нарезок (сейчас {old['state']})")
    src = store.get_source(old["source_id"])
    rights.check(src)
    info = media.probe(src["path"])
    fp = src["rights_approval"].get("fingerprint") or rights.fingerprint(src["path"])
    tr = (transcriber or tr_mod.transcribe)(src["path"], model=whisper_model, cache_dir=data_dir / "cache", cache_key=fp)
    hl = {**(old.get("highlight") or {}), "start": old["segment"]["start"], "dur": old["segment"]["dur"]}
    t = time.time()
    new = render_short(store, src, hl, tr, out_dir=data_dir / "shorts", info=info, chat=chat)
    new = store.update_short(new["id"], {"replaces": iid, "timings": {**new["timings"], "total_item_s": round(time.time() - t, 1)}})
    store.update_short(iid, {"superseded_by": new["id"]})
    store.transition(iid, "rejected", actor, f"заменён перерендером {new['id']}")
    store.audit(actor, "short.rerendered", {"old": iid, "new": new["id"], "captions_burned": new["captions"]["burned"]})
    return new
