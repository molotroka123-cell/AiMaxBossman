"""Jeff's call surface for the answering machine: the same ``CallParticipantRuntime`` with the reply brief of a message-taking
assistant. Kept apart from ``bcc.pit`` (which this module only imports): the call surface itself is unchanged."""
from __future__ import annotations

from bcc.pit.call_surface import CALL_SHAPE_SUFFIX, CallParticipantRuntime

from ..answering_policy import ANSWERING_BRIEF


class AnsweringParticipantRuntime(CallParticipantRuntime):
    """Local-only, spoken, per-caller zero-start memory (all inherited); only the reply brief differs."""

    local_shape_suffix = CALL_SHAPE_SUFFIX + ANSWERING_BRIEF
