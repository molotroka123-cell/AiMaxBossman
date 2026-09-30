"""Jeff on a live voice call: the unchanged participant pipeline, delivered as speech (mirror of ``bcc.pit.web``).

Reuse contract (docs/telegram-calls): a call is another SURFACE of the same Jeff, not a new brain. Every turn goes through
the same ``public_guard``, forbidden-command refusal, per-person consent and zero-start rules, free/local-only routing,
reasoning strip and presentation renderer as Telegram Jeff. This module adds only:

- ``CallParticipantRuntime`` — a ``ParticipantRuntime`` whose delivery shim has no Bot API method at all, forced local-only
  (no cloud, no web search on a call), spoken reply shape, ``surface="call"`` (the owner's private block rule applies);
- ``reply()`` — one transcript in, one speakable answer out, cancellable; in-band failure strings become stable codes;
- ``commit_turn()`` / ``discard_turn()`` — Jeff learns only from what the participant said AFTER the answer was actually
  spoken; no transcript is written at rest (``_record_chat`` is never called), and a barge-in that spoke nothing learns nothing;
- ``finish_call()`` — ONE consent-gated summary fact in the participant's own namespace (never owner-global memory).

The peer of a call is an ordinary participant: keyed by its Telegram id in the default namespace (so it shares facts, consent
and style with its chats with the Jeff bot), zero-start (no memory, local only) until the participant's own consent says
otherwise. ``_welcome_if_first_contact`` is deliberately NOT called: it would silently enable remote processing.
"""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import itertools
import time
from dataclasses import dataclass

from bcc.telegram_companion.config import CompanionError, Person

from .behavior_scores import BehaviorEvent
from .config import PITSettings
from .models import EvidenceKind, MemoryCandidate, Sensitivity
from .presentation import spoken_reply_text
from .public_guard import public_guard
from . import runtime as _rt
from .runtime import ParticipantRuntime

#: Spoken reply shape for a LOCAL model (replaces the 180-word Telegram shape; the safety text that follows is kept).
CALL_SHAPE_SUFFIX = (" Отвечай законченными короткими фразами, не больше сорока слов, разговорно, "
                     "без списков, ссылок и разметки. ")
CALL_TURN_DEADLINE_SECONDS = 20.0
CALL_MAX_TOKENS = 300
SUMMARY_CATEGORY = "communication"
SUMMARY_KEY = "last_call_summary"

#: In-band failure strings of ``_chat_route`` -> stable code (compared by equality, they are constants).
_INBAND = {
    _rt.NO_MODEL_RU: "NO_MODEL",
    _rt.NO_REMOTE_RU: "NO_REMOTE",
    _rt.CLOUD_PAUSED_RU: "CLOUD_PAUSED",
    _rt.PROVIDER_DOWN_RU: "PROVIDER_DOWN",
    _rt.INCOMPLETE_REPLY_RU: "REPLY_INCOMPLETE",
}


class NoTelegramTransport:
    """Transport shim of the call surface: it has no Bot API method, so a call can never poll or send through either bot."""

    def __init__(self):
        self.authorize_delivery = lambda person: False

    async def call(self, method: str, payload: dict):
        raise CompanionError("CALL_SURFACE_HAS_NO_TELEGRAM")

    async def preflight(self):
        raise CompanionError("CALL_SURFACE_HAS_NO_TELEGRAM")

    async def fetch_file(self, *args, **kwargs):
        raise CompanionError("CALL_SURFACE_HAS_NO_TELEGRAM")

    async def send(self, *args, **kwargs):
        raise CompanionError("CALL_SURFACE_HAS_NO_TELEGRAM")

    send_photo = send_document = send_voice = send_video = delete_message = send

    async def close(self) -> None:
        return None


@dataclass(frozen=True)
class CallReply:
    text: str                 # speakable Russian text ("" when nothing should be spoken)
    kind: str                 # "model" | "guard" | "refused" | "error" | "cancelled"
    code: str = ""            # stable code for kind in {"error", "refused"}
    update_id: int | None = None   # pass to commit_turn / discard_turn (model replies only)


def _suppress(fn) -> None:
    with contextlib.suppress(Exception):
        fn()


class CallParticipantRuntime(ParticipantRuntime):
    """The unchanged participant pipeline, delivered as speech."""

    allow_web = False
    turn_deadline_seconds = CALL_TURN_DEADLINE_SECONDS
    local_shape_suffix = CALL_SHAPE_SUFFIX

    def __init__(self, settings: PITSettings):
        # Local-only by construction: a call never spends the cloud budget and never sends call text to a provider.
        settings = dataclasses.replace(
            settings, web_only=True, bot_token="", local_chat_only=True, chat_models=(),
            max_tokens=max(64, min(settings.max_tokens, CALL_MAX_TOKENS)))
        super().__init__(settings)
        # The base class opened the Telegram client and the Telegram conversation store: keep the store (it is Jeff's own
        # per-participant history, read-only for us), drop the Bot API client.
        self._telegram_client = self.telegram
        self.telegram = NoTelegramTransport()
        self.surface = "call"
        self._update_ids = itertools.count(1_000_000_000)
        self._pending_call_turns: dict[int, tuple[str, str, str]] = {}      # update_id -> (person_key, text, message_id)

    async def close(self) -> None:
        client, self._telegram_client = getattr(self, "_telegram_client", None), None
        if client is not None:
            with contextlib.suppress(Exception):
                await client.close()
        await super().close()

    # ------------------------------------------------------------ one turn
    def _person(self, peer_user_id: int) -> Person:
        return Person(user_id=peer_user_id, chat_id=peer_user_id, role="guest")

    async def reply(self, peer_user_id: int, transcript: str, *, session_history: list[dict] | None = None,
                    cancel: asyncio.Event | None = None) -> CallReply:
        """Jeff's answer to one transcript. Never raises for model problems; ``cancel`` (barge-in / STOP) wins at once."""
        person = self._person(peer_user_id)
        person_key = self.vault.key_for_telegram(peer_user_id)
        if self._blocked(peer_user_id, peer_user_id):
            return CallReply("", "refused", "BLOCKED")                      # the owner's private block beats everything
        text = (transcript or "").strip()
        if not text:
            return CallReply("", "refused", "EMPTY")
        if text.startswith("/"):                                            # a transcript is chat input, never a command
            self.behavior.privacy_probe(person_key, kind="tool_probe")
            return CallReply(spoken_reply_text(_rt.FORBIDDEN_REPLY_RU), "guard", "FORBIDDEN")
        consent = self.vault.consent(person_key)                            # zero-start: absent file = everything off
        guard = public_guard(text)
        if guard is not None:
            self.behavior.privacy_probe(person_key, kind=guard.kind.value)
            return CallReply(spoken_reply_text(guard.text), "guard", guard.kind.value)
        update_id = next(self._update_ids)
        message_id = f"call:{update_id}"
        task = asyncio.get_running_loop().create_task(self._chat_route(
            person, person_key, text, consent, message_id=message_id, update_id=update_id,
            session_history=[dict(m) for m in (session_history or [])]))
        waiters: set[asyncio.Future] = {task}
        cancel_task = None
        if cancel is not None:
            cancel_task = asyncio.get_running_loop().create_task(cancel.wait())
            waiters.add(cancel_task)
        try:
            done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            task.cancel()
            self._pending_chat_records.pop(update_id, None)
            raise
        finally:
            if cancel_task is not None and not cancel_task.done():
                cancel_task.cancel()
        if task not in done:                                                # barge-in / STOP while the model was thinking
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            self._pending_chat_records.pop(update_id, None)
            return CallReply("", "cancelled")
        try:
            answer = task.result()
        except Exception as exc:  # noqa: BLE001 - never leak provider text
            self._pending_chat_records.pop(update_id, None)
            return CallReply("", "error", type(exc).__name__[:40])
        code = _INBAND.get(answer)
        if code:
            self._pending_chat_records.pop(update_id, None)
            return CallReply("", "error", code)
        spoken = spoken_reply_text(answer.split("\n\nИсточники:", 1)[0])
        if not spoken.strip():
            self._pending_chat_records.pop(update_id, None)
            return CallReply("", "error", "EMPTY_REPLY")
        self._pending_call_turns[update_id] = (person_key, text, message_id)
        return CallReply(spoken, "model", update_id=update_id)

    # ------------------------------------------------------------ learning (after the answer was actually spoken)
    def commit_turn(self, update_id: int, *, spoken: bool) -> None:
        """Jeff learns from the participant's own words only after at least one sentence of the answer was spoken."""
        record = self._pending_chat_records.pop(update_id, None)
        pending = self._pending_call_turns.pop(update_id, None)
        if not spoken or record is None or pending is None:
            return
        person_key, text, message_id = pending
        who, _key, _t, _a, _items, _web, _mid, memory_epoch, _question = record
        if (self._memory_epoch.get(person_key, 0) != memory_epoch
                or not self.vault.consent(person_key).memory_enabled):
            return
        # NOT ``_record_chat``: that would seal the whole turn into the store. Only explicit self-statements become facts.
        self._learn(person_key, text, message_id)
        self.behavior.record(person_key, BehaviorEvent.CONTEXT_CONTINUED)

    def discard_turn(self, update_id: int) -> None:
        self._pending_chat_records.pop(update_id, None)
        self._pending_call_turns.pop(update_id, None)

    # ------------------------------------------------------------ end of call
    def finish_call(self, peer_user_id: int, call_id: str, summary: str) -> bool:
        """ONE consent-gated summary fact in this participant's own namespace. Returns whether it was stored."""
        person_key = self.vault.key_for_telegram(peer_user_id)
        summary = " ".join((summary or "").split())[:400]
        if not summary or not self.vault.consent(person_key).memory_enabled:
            return False
        candidate = MemoryCandidate(
            id=f"call:{call_id}:summary", category=SUMMARY_CATEGORY, key=SUMMARY_KEY, value=summary, confidence=0.94,
            evidence_kind=EvidenceKind.EXPLICIT, sensitivity=Sensitivity.NORMAL, source_message_id=f"call:{call_id}",
            source_model="call-summary", observed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            tags=["call"])
        result = self.collector.ingest(person_key, [candidate])
        return result.accepted > 0
