"""Language of Jeff's voice: which Piper model speaks a given text, which fixed phrases a call uses, how Whisper is told the language.

Jeff speaks Russian by default (the ``ru_RU`` Piper model in ``BOSSMAN_PIT_TTS_MODEL_PATH``). English is an OPT-IN second voice:
it exists only when the owner has put an English Piper model (``*.onnx`` + ``*.onnx.json``) on the host and pointed
``BOSSMAN_PIT_TTS_MODEL_PATH_EN`` at it. Nothing is downloaded here. With no English model an English text is spoken with the Russian
voice exactly as before, never refused and never sent to a cloud engine.

The language of a text is decided by its script (Latin vs Cyrillic letters): cheap, deterministic, no model.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

EN_MODEL_ENV = "BOSSMAN_PIT_TTS_MODEL_PATH_EN"
LANGUAGES = ("ru", "en", "auto")        # call setting: what the other person speaks; "auto" lets Whisper decide per utterance
_CYR = re.compile(r"[А-Яа-яЁё]")
_LAT = re.compile(r"[A-Za-z]")

DEFAULT_RU_GREETING = "Привет! Это Джефф, ИИ-ассистент. Ты меня слышишь?"

#: Fixed phrases a call says WITHOUT a model (greeting, AI disclosure, prompts). The disclosure must never depend on a model's language.
PHRASES = {
    "ru": {"greeting": DEFAULT_RU_GREETING, "disclosure": "Это Джефф, ИИ-ассистент.", "idle_prompt_text": "Ты ещё здесь?",
           "repeat_prompt_text": "Не расслышал. Повтори, пожалуйста.",
           "apology_text": "Секунду, у меня заминка. Повтори, пожалуйста."},
    "en": {"greeting": "Hi! This is Jeff, an AI assistant. Can you hear me?", "disclosure": "This is Jeff, an AI assistant.",
           "idle_prompt_text": "Are you still there?", "repeat_prompt_text": "Sorry, I didn't catch that. Could you repeat it?",
           "apology_text": "One second, I had a hiccup. Could you repeat that?"},
}


def detect_language(text: str) -> str:
    """'en' when Latin letters clearly dominate, else 'ru' (Cyrillic, digits, empty, mixed leaning Cyrillic)."""
    latin, cyrillic = len(_LAT.findall(text or "")), len(_CYR.findall(text or ""))
    return "en" if latin >= 3 and latin > 1.5 * cyrillic else "ru"


def english_model() -> str:
    """Absolute path of the owner's English Piper model, or '' when it is not (completely) installed."""
    path = os.environ.get(EN_MODEL_ENV, "").strip()
    if path and os.path.isabs(path) and Path(path).is_file() and Path(path + ".json").is_file():
        return path
    return ""


def pick_model(text: str, ru_model: str, en_model: str | None = None) -> tuple[str, str]:
    """(model path, language actually spoken). English text uses the English voice only when it is installed."""
    en = english_model() if en_model is None else en_model
    if en and detect_language(text) == "en":
        return en, "en"
    return ru_model, "ru"


def call_phrases(language: str, greeting: str) -> dict[str, str]:
    """Fixed phrases for a call. English replaces the DEFAULT greeting only; a greeting the owner wrote is always kept."""
    base = dict(PHRASES["en" if language == "en" else "ru"])
    if greeting.strip() and greeting != DEFAULT_RU_GREETING:
        base["greeting"] = greeting
    elif not greeting.strip():
        base["greeting"] = ""
    return base


def whisper_language(language: str) -> str | None:
    """What to hand to Whisper: None = detect (the 'auto' call setting), otherwise the two-letter code."""
    return None if language in ("auto", "", None) else language
