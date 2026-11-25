"""Translators for live calls — local first, every result says how it was made.

Chain (what is installed today in Bossman, no cloud):
  * same language as the target          -> passthrough (quality "source")
  * en -> ru                              -> pinned Marian opus-mt-en-ru (the video studio's verified weights), in-process
  * X  -> en                              -> Whisper's own task="translate" on the audio (done by the ASR step)
  * X  -> ru (X != en)                    -> Whisper X->en, then Marian en->ru (quality "two_hop": flagged, not hidden)
  * anything else                         -> UNSUPPORTED_PAIR: the original text is shown, nothing is invented.
The Marian model is loaded once and kept (a live call cannot reload weights per phrase).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Protocol


@dataclass(frozen=True)
class Translation:
    text: str
    quality: str          # source | direct | two_hop | unsupported | failed
    engine: str


class TextTranslator(Protocol):
    pairs: frozenset

    def __call__(self, text: str) -> str: ...


class MarianEnRu:
    """In-process pinned Marian en->ru. Weights verified by the video studio's identity check."""
    pairs = frozenset({("en", "ru")})

    def __init__(self, model_path: str):
        self.model_path = model_path
        self._lock = threading.Lock()
        self._model = None

    def _load(self):
        from bcc.video_studio.language import _model_identity  # same pinned-weights check, no second copy
        _model_identity(self.model_path)
        import os
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        from transformers import MarianMTModel, MarianTokenizer
        tok = MarianTokenizer.from_pretrained(self.model_path, local_files_only=True)
        model = MarianMTModel.from_pretrained(self.model_path, local_files_only=True, weights_only=True).eval()
        return tok, model

    def __call__(self, text: str) -> str:
        with self._lock:
            if self._model is None:
                self._model = self._load()
            tok, model = self._model
            import torch
            with torch.inference_mode():
                enc = tok(text[:1200], return_tensors="pt", truncation=True, max_length=256)
                out = model.generate(**enc, max_new_tokens=256, num_beams=2, do_sample=False)
            return tok.decode(out[0], skip_special_tokens=True)


class Router:
    """Picks the path for (source, target). `english_of` gives the Whisper X->en text for this phrase, lazily."""

    def __init__(self, translators: list | None = None):
        self.translators = list(translators or [])

    def _find(self, src: str, tgt: str):
        for t in self.translators:
            if (src, tgt) in t.pairs:
                return t
        return None

    def translate(self, text: str, *, source: str, target: str,
                  english_of: Callable[[], str | None] | None = None) -> Translation:
        source, target = (source or "").lower()[:2], (target or "").lower()[:2]
        if not text.strip():
            return Translation("", "source", "none")
        if source == target:
            return Translation(text, "source", "passthrough")
        direct = self._find(source, target)
        try:
            if direct:
                return Translation(direct(text), "direct", type(direct).__name__)
            if target == "en" and english_of:
                en = english_of()
                if en:
                    return Translation(en, "direct", "whisper-translate")
            if english_of and source != "en":
                second = self._find("en", target)
                if second:
                    en = english_of()
                    if en:
                        return Translation(second(en), "two_hop", "whisper-translate+" + type(second).__name__)
        except Exception as exc:  # noqa: BLE001 - a broken model must not kill the call; it is reported
            return Translation(text, "failed", type(exc).__name__)
        return Translation(text, "unsupported", "none")
