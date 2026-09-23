"""The terminal chat's conversation preamble: one format, shared by the client that
writes it and the backend checks that must not mistake it for a request.

The chat sends the last turns as context inside the task prompt. The model needs
them; the action contract and the action router must classify only what the owner
asked NOW (owner run 2026-09-23, P1 CHAT-CONTEXT-REQUEST: an earlier «Запомни …»
turned every later question into a failed memory action with a new approval).
"""
from __future__ import annotations

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
