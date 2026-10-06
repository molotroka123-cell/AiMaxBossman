"""Live reply text for the Jeff window (SSE) and Telegram (edit-in-place drafts).

The runtime knows one thing: a per-turn *sink*, an async callable that receives
visible reply text as it arrives and ``None`` when what was shown so far must be
discarded (an attempt failed, was refused or is being retried). The sink is carried
in a ``ContextVar`` so the model call stays a plain ``handle()`` call. The text a
sink receives is a preview: the authoritative reply is always the value ``handle``
returns, which is what gets saved and delivered exactly once.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from contextvars import ContextVar
from typing import Awaitable, Callable

Sink = Callable[["str | None"], Awaitable[None]]

reply_sink: ContextVar[Sink | None] = ContextVar("jeff_reply_sink", default=None)


def _stream_leaks(text: str) -> bool:
    from .identity_guard import stream_leaks
    return stream_leaks(text)


class TurnStream:
    """Per-turn bookkeeping between a model adapter's ``on_delta`` and the sink."""

    def __init__(self, sink: Sink | None, clock=time.perf_counter):
        self.sink = sink
        self.clock = clock
        self.started = clock()
        self.first_at: float | None = None
        self.shown = False
        self.text = ""
        self.leak_stopped = False

    async def on_delta(self, text: str | None) -> None:
        if text is None:
            await self.reset()
            return
        if not text:
            return
        if self.first_at is None:
            self.first_at = self.clock()
        self.text += text
        if self.sink is not None and _stream_leaks(self.text):
            # The preview would show the base model's identity or an internal: drop it and stop
            # previewing; the final reply goes through the mandatory identity/disclosure filter.
            await self.reset()
            self.sink = None
            self.leak_stopped = True
            return
        self.shown = True
        if self.sink is not None:
            try:
                await self.sink(text)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — a broken preview must never break the reply
                self.sink = None

    async def reset(self) -> None:
        """What was shown belongs to a failed/refused attempt: tell the sink to drop it."""
        self.text = ""
        if not self.shown:
            return
        self.shown = False
        if self.sink is not None:
            with contextlib.suppress(Exception):
                await self.sink(None)

    @property
    def ttft_ms(self) -> float | None:
        return None if self.first_at is None else (self.first_at - self.started) * 1000.0


class EditPacer:
    """Rate limit for progressive edits: at most one edit per ``interval`` seconds and only
    when enough new text arrived; the final edit is never paced away."""

    def __init__(self, interval: float = 1.5, min_chars: int = 24, clock=time.monotonic):
        self.interval, self.min_chars, self.clock = interval, min_chars, clock
        self.last_at = -1e9
        self.last_len = 0

    def due(self, length: int) -> bool:
        return (length - self.last_len >= self.min_chars
                and self.clock() - self.last_at >= self.interval)

    def mark(self, length: int) -> None:
        self.last_at, self.last_len = self.clock(), length


CURSOR = " ▍"
DRAFT_LIMIT = 3900          # below Telegram's 4096; longer replies are delivered the normal way
FIRST_DRAFT_MIN_CHARS = 40


class TelegramDraft:
    """Edit-in-place preview of one reply in a private chat.

    * the first visible text becomes ONE message (plain text plus a cursor mark);
    * later text edits that same message, rate limited by ``EditPacer``;
    * ``finalize`` turns the draft into the final message with one last edit, so the
      chat shows a single message and the worker records/delivers it exactly once;
    * every failure degrades to the ordinary path: a preview error disables previews,
      a failed final edit deletes the draft so the caller can send normally.

    The preview never carries anything the final reply would not: it is the model's
    visible text (reasoning is filtered upstream) and goes through the same transport
    egress guard as any other message.
    """

    def __init__(self, telegram, person, *, reply_to: int | None, pacer: EditPacer | None = None,
                 first_chars: int = FIRST_DRAFT_MIN_CHARS):
        self.telegram, self.person, self.reply_to = telegram, person, reply_to
        self.pacer = pacer or EditPacer()
        self.first_chars = first_chars
        self.message_id: int | None = None
        self.text = ""
        self.shown = ""
        self.disabled = False
        self.sends = 0
        self.edits = 0

    async def sink(self, piece: str | None) -> None:
        if self.disabled:
            return
        if piece is None:
            self.text = ""
            return
        self.text += piece
        visible = self.text[:DRAFT_LIMIT]
        if len(self.text) > DRAFT_LIMIT:
            self.disabled = True   # too long for one message: final delivery is the normal path
            return
        try:
            if self.message_id is None:
                if len(visible) < self.first_chars:
                    return
                sent = await self.telegram.send(self.person, visible + CURSOR,
                                                reply_to_message_id=self.reply_to)
                if type(sent) is not int or sent <= 0:
                    self.disabled = True
                    return
                self.message_id, self.shown, self.sends = sent, visible, self.sends + 1
                self.pacer.mark(len(visible))
            elif visible != self.shown and self.pacer.due(len(visible)):
                await self.telegram.edit_message(self.person, self.message_id, visible + CURSOR)
                self.shown, self.edits = visible, self.edits + 1
                self.pacer.mark(len(visible))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — previews are best effort; never break the reply
            self.disabled = True

    async def finalize(self, rendered: str) -> bool:
        """True when the draft message now holds ``rendered`` (nothing else to send)."""
        if self.message_id is None:
            return False
        if len(rendered) <= DRAFT_LIMIT:
            try:
                await self.telegram.edit_message(self.person, self.message_id, rendered,
                                                 parse_mode="HTML", final=True)
                self.edits += 1
                return True
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                pass
        await self.discard()
        return False

    async def discard(self) -> None:
        """Remove the preview so the ordinary send does not leave a duplicate behind."""
        message_id, self.message_id = self.message_id, None
        if message_id is not None:
            with contextlib.suppress(Exception):
                await self.telegram.delete_message(self.person, message_id)
