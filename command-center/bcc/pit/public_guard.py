from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


PUBLIC_BOSSMAN_GITHUB = "https://github.com/molotroka123-cell/AiMaxBossman"
JEFF_IDENTITY_REPLY_RU = (
    "Меня зовут Jeff. Я AI-помощник в экосистеме Bossman. "
    "Я не раскрываю внутреннюю модель, провайдера или маршрутизацию."
)
OWNER_PRIVACY_REPLY_RU = (
    "Я не раскрываю личные данные владельца Bossman или других пользователей."
)
LOCATION_REPLY_RU = (
    "Я не вижу ваше или чужое скрытое местоположение. "
    "Если для ответа нужен город или страна, напишите их прямо в сообщении."
)
INTERNAL_STAGE_REPLY_RU = (
    "Я могу рассказать о публичном проекте Bossman, но не раскрываю внутренние "
    "экспериментальные этапы, ветки и тестовые handoff-данные."
)
OTHER_PERSON_REPLY_RU = (
    "Я не имею доступа к персональной памяти других пользователей и не раскрываю её."
)
BOSSMAN_PUBLIC_REPLY_RU = (
    "Bossman — развивающееся local-first AI-рабочее пространство: модели, инструменты, "
    "память, поиск и проверяемые рабочие процессы объединяются в одном продукте. "
    "Публичный репозиторий: " + PUBLIC_BOSSMAN_GITHUB
)


class GuardKind(StrEnum):
    IDENTITY = "identity"
    OWNER_PRIVACY = "owner_privacy"
    LOCATION = "location"
    INTERNAL_STAGE = "internal_stage"
    OTHER_PERSON = "other_person"
    BOSSMAN_PUBLIC = "bossman_public"


@dataclass(frozen=True, slots=True)
class GuardReply:
    kind: GuardKind
    text: str


_MODEL_RE = re.compile(
    r"\b(?:какая|какой|что за)\s+(?:у тебя\s+)?(?:модель|model)\b|"
    r"\b(?:what|which)\s+model\s+(?:are you|do you use|is running)\b|"
    r"\b(?:какой|what)\s+(?:у тебя\s+)?(?:provider|провайдер|endpoint|backend)\b|"
    r"\b(?:ты|you)\s+(?:claude|glm|qwen|llama|gpt)[\w. -]*\??$",
    re.I,
)
_IDENTITY_RE = re.compile(r"^(?:кто ты|как тебя зовут|who are you|what are you|your name)\??$", re.I)
_OWNER_RE = re.compile(r"\b(?:владелец|owner|тимур|создатель|creator)\b", re.I)
_LOCATION_RE = re.compile(
    r"\b(?:где я|where am i|мо[её] местополож|my location|знаешь где я|видишь мою геолокац)\b",
    re.I,
)
_INTERNAL_RE = re.compile(
    r"\b(?:personal identity training|\bpit\b|bossman\s*1[.,]7|v1[.,]7|ветк[аи].*1[.,]7|internal stage)\b",
    re.I,
)
_OTHER_RE = re.compile(
    r"\b(?:друг(?:ого|их)? пользовател|other user|чуж(?:ая|ие) памя|memory of|что знаешь о .+)\b",
    re.I,
)
_BOSSMAN_RE = re.compile(
    r"^(?:что такое|кто такой|расскажи (?:мне )?про|what is|tell me about)\s+(?:bossman|боссман)\??$|"
    r"^(?:дай|покажи|give|show).*(?:github).*(?:bossman|боссман)?",
    re.I,
)


def public_guard(text: str) -> GuardReply | None:
    """Handle identity/privacy/meta questions before any model route.

    This guard intentionally does not answer ordinary topical questions.
    """
    value = " ".join(str(text or "").strip().split())
    if not value:
        return None
    if _MODEL_RE.search(value) or _IDENTITY_RE.search(value):
        return GuardReply(GuardKind.IDENTITY, JEFF_IDENTITY_REPLY_RU)
    if _OWNER_RE.search(value):
        return GuardReply(GuardKind.OWNER_PRIVACY, OWNER_PRIVACY_REPLY_RU)
    if _LOCATION_RE.search(value):
        return GuardReply(GuardKind.LOCATION, LOCATION_REPLY_RU)
    if _INTERNAL_RE.search(value):
        return GuardReply(GuardKind.INTERNAL_STAGE, INTERNAL_STAGE_REPLY_RU)
    if _OTHER_RE.search(value):
        return GuardReply(GuardKind.OTHER_PERSON, OTHER_PERSON_REPLY_RU)
    if _BOSSMAN_RE.search(value):
        return GuardReply(GuardKind.BOSSMAN_PUBLIC, BOSSMAN_PUBLIC_REPLY_RU)
    return None
