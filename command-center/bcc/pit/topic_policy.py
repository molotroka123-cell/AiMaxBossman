from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from .models import ConsentState


class SensitiveTopic(StrEnum):
    POLITICS = "political_preference"
    RELIGION = "religion"


_POLITICS = re.compile(
    r"\b(?:политик|выбор|парт|президент|парламент|правительств|войн[аы]|украин|росси|"
    r"politic|election|party|president|parliament|government|ukraine|russia|war)\b",
    re.I,
)
_RELIGION = re.compile(
    r"\b(?:религи|бог|ислам|мусульман|христиан|иуд|атеист|вер[ау]|religion|god|islam|muslim|christian|jewish|atheis)\b",
    re.I,
)


@dataclass(frozen=True, slots=True)
class SensitiveTopicState:
    active: frozenset[SensitiveTopic]


def detect_user_initiated_topics(user_text: str) -> SensitiveTopicState:
    text = str(user_text or "")
    active: set[SensitiveTopic] = set()
    if _POLITICS.search(text):
        active.add(SensitiveTopic.POLITICS)
    if _RELIGION.search(text):
        active.add(SensitiveTopic.RELIGION)
    return SensitiveTopicState(frozenset(active))


def may_discuss(topic: SensitiveTopic, state: SensitiveTopicState) -> bool:
    """Discussion is allowed only after the participant brought the topic up."""
    return topic in state.active


def may_store_durably(
    topic: SensitiveTopic,
    *,
    state: SensitiveTopicState,
    consent: ConsentState,
    explicitly_stated_by_user: bool,
) -> bool:
    """Sensitive durable memory needs topic relevance + explicit statement + opt-in."""
    return (
        topic in state.active
        and explicitly_stated_by_user
        and consent.memory_enabled
        and consent.sensitive_memory_enabled
    )


def may_proactively_ask(topic: SensitiveTopic, state: SensitiveTopicState) -> bool:
    """Never introduce politics/religion merely to enrich a profile."""
    return topic in state.active
