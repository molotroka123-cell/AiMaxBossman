"""One live-interpretation session: audio stream -> phrases -> local ASR -> translation -> subtitle events.

Ported as IDEAS from Cicada (macOS menu-bar interpreter, Apache-2.0; no code copied) onto Bossman's own local stack.
Boundaries (owner rules):
  * started and stopped ONLY by the owner (pult route); no agent tool can start listening to a call;
  * the owner's global STOP ends the session at the next phrase (bcc.owner_stop, fail closed);
  * audio never leaves the PC and is never written to disk; only text is kept;
  * the transcript is written to disk ONLY when the owner asked for it (keep_transcript) — default: memory only;
  * a session ends by itself after `idle_end_s` of silence (Cicada: 10 minutes) so a forgotten window does not listen forever.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .segmenter import Segmenter, Utterance, to_wav
from .translate import Router

MAX_SEGMENTS = 5000


class NoSpeech(Exception):
    pass


@dataclass
class LiveSession:
    session_id: str
    target: str
    asr: Callable[..., dict]                 # asr(wav, task="transcribe"|"translate") -> {text, language, confidence}
    router: Router
    stop_check: Callable[[], bool] = lambda: False
    sink: Callable[[dict], None] = lambda event: None
    store_dir: Path | None = None            # None = transcript in memory only
    clock: Callable[[], float] = time.monotonic
    idle_end_s: float = 600.0
    segmenter: Segmenter = field(default_factory=Segmenter)
    segments: list = field(default_factory=list)
    ended: str = ""
    _last_voice: float = 0.0
    _started: float = 0.0

    def __post_init__(self):
        self._started = self._last_voice = self.clock()

    # ------------------------------------------------------------------ stream
    def feed(self, pcm: bytes) -> list[dict]:
        if self.ended:
            return []
        if self.stop_check():
            self.end("owner_stop")
            return []
        events = [self._process(u) for u in self.segmenter.feed(pcm)]
        events = [e for e in events if e]
        if events:
            self._last_voice = self.clock()
        elif self.clock() - self._last_voice >= self.idle_end_s:
            self.end("silence_timeout")
        return events

    def _process(self, utt: Utterance) -> dict | None:
        if self.stop_check():
            self.end("owner_stop")
            return None
        if len(self.segments) >= MAX_SEGMENTS:
            self.end("too_long")
            return None
        wav = to_wav(utt.pcm)
        try:
            heard = self.asr(wav, task="transcribe")
        except NoSpeech:
            return None
        except Exception as exc:  # noqa: BLE001 - a busy/broken ASR skips one phrase, it does not end the call
            self.sink({"type": "asr_error", "session_id": self.session_id, "start_ms": utt.start_ms,
                       "error": str(exc)[:80] or type(exc).__name__})
            return None
        text = str(heard.get("text") or "").strip()
        if not text:
            return None
        lang = str(heard.get("language") or "")[:2].lower()
        cache: dict = {}

        def english_of():
            if "en" not in cache:
                try:
                    cache["en"] = str(self.asr(wav, task="translate").get("text") or "").strip() or None
                except Exception:  # noqa: BLE001 - no English pivot -> Router reports "unsupported"
                    cache["en"] = None
            return cache["en"]

        tr = self.router.translate(text, source=lang, target=self.target, english_of=english_of)
        event = {"type": "subtitle", "session_id": self.session_id, "seq": len(self.segments) + 1,
                 "start_ms": utt.start_ms, "end_ms": utt.end_ms, "language": lang, "text": text,
                 "translation": tr.text, "quality": tr.quality, "engine": tr.engine,
                 "confidence": heard.get("confidence")}
        self.segments.append(event)
        if self.store_dir is not None:
            self.store_dir.mkdir(parents=True, exist_ok=True)
            with (self.store_dir / f"{self.session_id}.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        self.sink(event)
        return event

    def end(self, reason: str = "owner") -> dict:
        if not self.ended:
            for utt in self.segmenter.flush() if reason in ("owner", "silence_timeout") else []:
                self._process(utt)
            self.ended = reason
            self.sink({"type": "ended", "session_id": self.session_id, "reason": reason})
        return self.summary()

    def summary(self) -> dict:
        langs = sorted({s["language"] for s in self.segments if s["language"]})
        quality: dict = {}
        for s in self.segments:
            quality[s["quality"]] = quality.get(s["quality"], 0) + 1
        return {"session_id": self.session_id, "ended": self.ended or None, "segments": len(self.segments),
                "languages": langs, "target": self.target, "quality": quality,
                "stored": self.store_dir is not None, "audio_stored": False}

    # ------------------------------------------------------------------ export
    def export_srt(self, *, translated: bool = True) -> str:
        def ts(ms: int) -> str:
            h, rem = divmod(ms, 3_600_000)
            m, rem = divmod(rem, 60_000)
            s, ms_ = divmod(rem, 1000)
            return f"{h:02d}:{m:02d}:{s:02d},{ms_:03d}"
        rows = []
        for i, s in enumerate(self.segments, 1):
            body = s["translation"] if translated else s["text"]
            if translated and s["translation"] != s["text"]:
                body = f"{s['translation']}\n{s['text']}"
            rows.append(f"{i}\n{ts(s['start_ms'])} --> {ts(s['end_ms'])}\n{body}\n")
        return "\n".join(rows)

    def export_markdown(self) -> str:
        lines = [f"# Перевод звонка {self.session_id}", "",
                 f"Целевой язык: {self.target} · фраз: {len(self.segments)} · "
                 f"языки: {', '.join(self.summary()['languages']) or '—'}", ""]
        for s in self.segments:
            mm, ss = divmod(s["start_ms"] // 1000, 60)
            flag = "" if s["quality"] in ("source", "direct") else f" _({s['quality']})_"
            lines.append(f"- **{mm:02d}:{ss:02d}** [{s['language'] or '?'}] {s['text']}")
            if s["translation"] != s["text"]:
                lines.append(f"  - → {s['translation']}{flag}")
        lines += ["", "_Машинный перевод, локально. Аудио не сохранялось._"]
        return "\n".join(lines)
