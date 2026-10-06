from __future__ import annotations

from dataclasses import dataclass
import html
import re


JEFF_PUBLIC_NAME = "Jeff"


@dataclass(frozen=True, slots=True)
class InternalRouteMeta:
    selected_model: str
    provider: str
    reason_code: str


_REASONING_BLOCK = re.compile(r"<(think|thinking|reasoning)>.*?</\1>", re.IGNORECASE | re.DOTALL)
_REASONING_TAIL_CLOSE = re.compile(r"^.*?</(?:think|thinking|reasoning)>", re.IGNORECASE | re.DOTALL)
_REASONING_OPEN = re.compile(r"<(?:think|thinking|reasoning)>", re.IGNORECASE)


def strip_private_reasoning(text: str) -> str:
    """Remove model-private reasoning that some chat templates inline in content.

    Reasoning is never participant content: a closed block is removed, a
    dangling close tag (template opened it in the prompt) drops everything
    before it, and an unterminated open block drops everything after it.
    """
    value = _REASONING_BLOCK.sub("", str(text or ""))
    if re.search(r"</(?:think|thinking|reasoning)>", value, re.IGNORECASE):
        value = _REASONING_TAIL_CLOSE.sub("", value, count=1)
    opened = _REASONING_OPEN.search(value)
    if opened:
        value = value[:opened.start()]
    return value


def render_jeff_reply(answer: str) -> str:
    """Render the participant-visible text without backend/model disclosure.

    Internal route metadata belongs in telemetry/evidence only and is never
    concatenated to the Telegram answer.
    """
    text = strip_private_reasoning(str(answer or "")).strip()
    # A model can ignore the style instruction. Remove the recurring generic
    # sign-off only when an actual answer precedes it.
    generic_tail = re.compile(
        r"(?:\n\s*)+(?:Чем могу помочь\??|"
        r"Тебе удобнее, когда я сам предлагаю следующий шаг, "
        r"или лучше отвечать строго на вопрос\?)\s*$",
        re.IGNORECASE,
    )
    return generic_tail.sub("", text).rstrip()


def spoken_reply_text(answer: str) -> str:
    """Turn visible answer formatting into text suitable for local TTS."""
    value = render_jeff_reply(answer)
    value = re.sub(r"<[^>]{1,120}>", "", value)
    value = re.sub(r"\[([^\]]{1,200})\]\([^)]{1,500}\)", r"\1", value)
    value = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", value)
    value = re.sub(r"(?m)^\s*[-*]\s+", "", value)
    value = re.sub(r"[*_`]+", "", value)
    return html.unescape(value).strip()


def public_model_label() -> str:
    return JEFF_PUBLIC_NAME
