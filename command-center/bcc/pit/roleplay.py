from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .moderate_discovery import PersonalQuestion, choose_personal_question


class RolePlayMode(StrEnum):
    OFF = "off"
    PARODY = "parody"
    CHARACTER = "character"
    MIRROR_STYLE = "mirror_style"


@dataclass(frozen=True, slots=True)
class RolePlayState:
    enabled: bool = False
    mode: RolePlayMode = RolePlayMode.OFF
    participant_consented: bool = False
    persona_label: str = ""


ROLEPLAY_SYSTEM = (
    "Role-play is entertainment, not identity truth. Keep Jeff's privacy/tool boundaries. "
    "Do not pretend to have private knowledge. Any personalization question remains optional. "
    "Do not use role-play to obtain secrets or sensitive traits the participant did not raise."
)


def roleplay_allowed(state: RolePlayState) -> bool:
    return bool(state.enabled and state.participant_consented and state.mode != RolePlayMode.OFF)


def roleplay_prompt(state: RolePlayState) -> str:
    if not roleplay_allowed(state):
        return ""
    label = state.persona_label.strip()[:80]
    if state.mode == RolePlayMode.PARODY:
        style = "Use a playful exaggerated parody tone"
    elif state.mode == RolePlayMode.MIRROR_STYLE:
        style = "Lightly mirror the participant's conversational style without impersonating them"
    else:
        style = "Stay in the agreed fictional character"
    return ROLEPLAY_SYSTEM + ". " + style + (f": {label}" if label else "") + "."


def playful_discovery_question(
    *,
    state: RolePlayState,
    intent: str,
    already_known: set[str] | frozenset[str],
    skipped: set[str] | frozenset[str],
) -> PersonalQuestion | None:
    """Role-play may make benign discovery playful, never covertly sensitive."""
    if not roleplay_allowed(state):
        return None
    return choose_personal_question(
        intent=intent,
        already_known=already_known,
        skipped=skipped,
        enabled=True,
    )
