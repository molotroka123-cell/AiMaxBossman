"""Live call interpreter for Bossman — Cicada's ideas on Bossman's local Windows stack.

Pipeline: system audio (WASAPI loopback) -> Segmenter (phrases) -> local faster-whisper (bcc.pit.speech)
-> Router (passthrough / pinned Marian en->ru / Whisper X->en / two-hop) -> subtitle events on the bus
-> transcript in memory (disk only on the owner's request) -> SRT / Markdown export.
Owner-only (no agent tool), stops on the global STOP, audio never stored. Stage 2 (not built yet):
speaking the translation into the call through a virtual microphone (VB-Cable) with local Piper TTS.
"""
from __future__ import annotations

from .segmenter import Segmenter, Utterance, to_wav
from .session import LiveSession, NoSpeech
from .translate import MarianEnRu, Router, Translation


def whisper_asr(wav: bytes, task: str = "transcribe") -> dict:
    """Local faster-whisper through bcc.pit.speech (the same cached model as the voice window)."""
    from bcc.pit import speech
    try:
        r = speech.transcribe_wav(wav, language="auto", task=task, beam_size=1)
    except speech.SpeechError as exc:
        if str(exc) == "VOICE_NO_SPEECH":
            raise NoSpeech() from exc
        raise
    return {"text": r["text"], "language": r.get("language", ""), "confidence": r.get("confidence")}


__all__ = ["Segmenter", "Utterance", "to_wav", "LiveSession", "NoSpeech", "MarianEnRu", "Router",
           "Translation", "whisper_asr"]
