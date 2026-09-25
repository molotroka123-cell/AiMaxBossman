from __future__ import annotations

from dataclasses import dataclass


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
    return str(answer or "").strip()


def public_model_label() -> str:
    return JEFF_PUBLIC_NAME
