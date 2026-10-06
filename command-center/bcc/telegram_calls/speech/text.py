"""Text side of the voice path: make LLM output speakable and cut it into sentences while it streams.

Latency matters: the first sentence must reach TTS as early as possible, so the *first* chunk may end at
a clause boundary (comma / dash / colon) once it is long enough, later chunks end at sentence ends.
"""
from __future__ import annotations

import re

#: The model is told to append this marker after a farewell; it is removed from the text and
#: flips ``SentenceChunker.end_call`` so the session hangs up after the goodbye is spoken.
END_MARKER = "[конец]"
_END_RE = re.compile(r"\[\s*конец\s*\]", re.IGNORECASE)

_MD_CODE = re.compile(r"```.*?```", re.DOTALL)
_MD_INLINE = re.compile(r"`([^`]*)`")
_MD_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)]+)\)")
_URL = re.compile(r"https?://\S+")
_MD_EMPH = re.compile(r"(\*\*|__|\*|_|~~|#+\s*|>\s*)")
_LIST_BULLET = re.compile(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0000FE0F\U0000200D]+")
_SPACES = re.compile(r"[ \t ]+")
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

_SENTENCE_END = re.compile(r"(?<=[.!?…])[\"»)\]]*\s+|(?<=\n)")
_CLAUSE_END = re.compile(r"(?<=[,;:—–])\s+")


def clean_for_speech(text: str) -> str:
    """Strip everything a listener should not hear: markdown, links, code, emoji, list bullets."""
    text = _THINK.sub(" ", text)
    text = _MD_CODE.sub(" ", text)
    text = _MD_INLINE.sub(r"\1", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _URL.sub(" ", text)
    text = _LIST_BULLET.sub("", text)
    text = _MD_EMPH.sub("", text)
    text = _EMOJI.sub(" ", text)
    text = text.replace("\r", "")
    text = re.sub(r"\n{2,}", ". ", text)
    text = text.replace("\n", " ")
    text = _SPACES.sub(" ", text)
    text = re.sub(r"\s+([,.!?…;:])", r"\1", text)
    text = re.sub(r"\.{4,}", "…", text)
    return text.strip()


class SentenceChunker:
    """Feed streamed text deltas; get speakable chunks as soon as they are complete."""

    def __init__(self, *, first_min_chars: int = 24, min_chars: int = 12, max_chars: int = 220):
        self.first_min, self.min_chars, self.max_chars = first_min_chars, min_chars, max_chars
        self._buf = ""
        self._emitted = 0
        self.end_call = False

    def feed(self, delta: str) -> list[str]:
        self._buf += delta
        if _END_RE.search(self._buf):
            self.end_call = True
            self._buf = _END_RE.sub("", self._buf)
        # never split inside an unfinished marker
        if "[" in self._buf[-8:]:
            head = self._buf[: self._buf.rfind("[")]
            tail = self._buf[self._buf.rfind("["):]
            out = self._cut(head)
            self._buf = tail if len(tail) < 8 else ""
            return out
        out = self._cut(self._buf)
        return out

    def _cut(self, text: str) -> list[str]:
        out: list[str] = []
        while True:
            need = self.first_min if self._emitted == 0 else self.min_chars
            cut = self._find_cut(text, need)
            if cut is None:
                break
            chunk, text = text[:cut], text[cut:]
            spoken = clean_for_speech(chunk)
            if spoken and re.search(r"\w", spoken):
                out.append(spoken)
                self._emitted += 1
        self._buf = text
        return out

    def _find_cut(self, text: str, need: int) -> int | None:
        for m in _SENTENCE_END.finditer(text):
            if m.end() >= need and len(text) > m.end() - 1:
                return m.end()
        if self._emitted == 0:                       # earliest possible first chunk: a clause boundary
            for m in _CLAUSE_END.finditer(text):
                if m.end() >= max(need, 30):
                    return m.end()
        if len(text) >= self.max_chars:              # runaway sentence: cut at the last space
            k = text.rfind(" ", 0, self.max_chars)
            return k + 1 if k > 0 else self.max_chars
        return None

    def flush(self) -> list[str]:
        """End of stream: whatever is left (minus the end marker) is the last chunk."""
        if _END_RE.search(self._buf):
            self.end_call = True
            self._buf = _END_RE.sub("", self._buf)
        spoken = clean_for_speech(self._buf)
        self._buf = ""
        if spoken and re.search(r"\w", spoken):
            self._emitted += 1
            return [spoken]
        return []
