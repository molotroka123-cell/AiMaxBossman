"""The terminal chat's conversation preamble: one format, shared by the client that
writes it and the backend checks that must not mistake it for a request.

The chat sends the last turns as context inside the task prompt. The model needs
them; the action contract and the action router must classify only what the owner
asked NOW (owner run 2026-09-23, P1 CHAT-CONTEXT-REQUEST: an earlier «Запомни …»
turned every later question into a failed memory action with a new approval).
"""
from __future__ import annotations

import re

HEADER = "Контекст беседы (предыдущие ходы этой сессии терминала):\n"
MARKER = "\n\nНовое сообщение владельца:\n"


def compose(parts: list[str]) -> str:
    """Preamble to put before the new message; empty when there is no history."""
    return HEADER + "\n\n".join(parts) + MARKER if parts else ""


def current_request(prompt: str) -> str:
    """The new message of a chat prompt; any other prompt unchanged.

    Only a prompt that STARTS with the header is split, so a marker typed into an
    ordinary request cannot hide part of it from the checks."""
    text = prompt or ""
    if not text.startswith(HEADER) or MARKER not in text:
        return text
    return text.rsplit(MARKER, 1)[1]


# Explicitly delimited DATA is not a request (02.11.2026): a block whose START and END lines carry the SAME
# upper-case label («НАЧАЛО РАСШИФРОВКИ … КОНЕЦ РАСШИФРОВКИ», «BEGIN TRANSCRIPT … END TRANSCRIPT»). Used by the
# action classifiers (action_contract, action_router): words spoken in a YouTube transcript («go to the website»,
# «push it to GitHub») are not the owner's request. Unclosed or mismatched markers hide nothing.
_DATA_BLOCK_RE = re.compile(
    r"^[ \t]*(?:НАЧАЛО|BEGIN)[ \t]+(?P<label>[A-ZА-ЯЁ][A-ZА-ЯЁ_ ]{2,40}?)[ \t]*$"
    r".*?"
    r"^[ \t]*(?:КОНЕЦ|END)[ \t]+(?P=label)[ \t]*$",
    re.M | re.S)


def without_data_blocks(text: str) -> str:
    return _DATA_BLOCK_RE.sub(" DATA ", text)
