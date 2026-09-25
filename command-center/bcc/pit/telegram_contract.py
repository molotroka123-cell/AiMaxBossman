from __future__ import annotations

import hashlib
from dataclasses import dataclass


ONBOARDING_RU = (
    "Я могу локально запоминать твои предпочтения и контекст, чтобы отвечать точнее. "
    "Память хранится у владельца Bossman. Ты можешь посмотреть её командой /memory, "
    "приостановить /pause_memory, экспортировать /export_me или удалить /delete_me. "
    "Чувствительные данные по умолчанию в долговременную память не записываются. "
    "Если локальная модель недоступна, удалённая модель используется только при отдельном согласии; "
    "передача ей сохранённого профиля — ещё одно отдельное разрешение."
)

USER_COMMANDS = frozenset({
    "/memory",
    "/why_memory",
    "/forget",
    "/pause_memory",
    "/resume_memory",
    "/export_me",
    "/delete_me",
    "/style",
    "/privacy",
})


@dataclass(frozen=True, slots=True)
class TelegramEnvelope:
    update_id: int
    user_id: int
    chat_id: int
    message_id: int
    text: str


def idempotency_key(envelope: TelegramEnvelope) -> str:
    payload = f"{envelope.update_id}:{envelope.chat_id}:{envelope.message_id}".encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def is_allowed_user(user_id: int, allowlist: set[int] | frozenset[int]) -> bool:
    return int(user_id) in {int(v) for v in allowlist}
