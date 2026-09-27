from __future__ import annotations

from dataclasses import dataclass
import re


JEFF_PUBLIC_NAME = "Jeff"


@dataclass(frozen=True, slots=True)
class InternalRouteMeta:
    selected_model: str
    provider: str
    reason_code: str


def render_jeff_reply(answer: str) -> str:
    """Render the participant-visible text without backend/model disclosure.

    Internal route metadata belongs in telemetry/evidence only and is never
    concatenated to the Telegram answer.
    """
    text = str(answer or "").strip()
    # A model can ignore the style instruction. Remove the recurring generic
    # sign-off only when an actual answer precedes it.
    generic_tail = re.compile(
        r"(?:\n\s*)+(?:Чем могу помочь\??|"
        r"Тебе удобнее, когда я сам предлагаю следующий шаг, "
        r"или лучше отвечать строго на вопрос\?)\s*$",
        re.IGNORECASE,
    )
    return generic_tail.sub("", text).rstrip()


def public_model_label() -> str:
    return JEFF_PUBLIC_NAME
