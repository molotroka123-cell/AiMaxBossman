"""Персона потока: то, что модели менять не разрешено.

У постоянного ведущего есть вещи, которые не обсуждаются между сценами: как он
выглядит, как говорит, чего не утверждает, где происходит действие. Если
позволить каждой генерации уточнять их «по смыслу», к десятому ролику ведущий
будет другим человеком в другом городе — и никто не сможет назвать сцену, где
это случилось, потому что каждый отдельный шаг выглядел разумно.

Отсюда устройство модуля:

* **канон версионирован.** Изменить его можно только явно, с повышением версии
  и с причиной. Не «модель предложила лучше» — новая версия, у которой есть
  автор и дата;
* **часть фактов заперта.** Запертый факт не меняется даже с повышением версии,
  пока его не отопрут отдельным действием. Внешность, запретные утверждения и
  правила спонсоров лежат именно там;
* **наблюдения не переписывают канон.** То, что модель «увидела» в кадре,
  попадает в журнал наблюдений. Совпало — подтверждение; разошлось — конфликт,
  который остаётся конфликтом. Молчаливого «уточнения» канона не существует, и
  это ровно тот случай из V7, где убеждение не становится правдой оттого, что
  его повторили.

Секретов здесь нет и быть не может: канон уходит в текст запроса к внешнему
генератору. Поэтому поля перечислены поимённо, а не собираются из произвольного
словаря — см. `pipeline.build_prompt`.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Iterable, Mapping


class PersonaCanonViolation(ValueError):
    """Попытка изменить канон в обход версии или замка."""


class ContinuityStatus(str, Enum):
    """Как наблюдение соотносится с каноном."""

    CONFIRMED = "confirmed"
    CONTESTED = "contested"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PersonaFact:
    """Один факт канона и то, разрешено ли его менять."""

    key: str
    value: str
    # Запертый факт — это то, изменение чего означает другого ведущего.
    # Отпирается отдельным действием, а не флагом в запросе на изменение.
    locked: bool = False
    note: str = ""

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("у факта канона должен быть ключ")
        if not self.value.strip():
            raise ValueError(f"факт {self.key!r} без значения ничего не удерживает")

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "value": self.value, "locked": self.locked,
                "note": self.note}


@dataclass(frozen=True, slots=True)
class StreamerPersona:
    """Канон одного ведущего в одной версии."""

    persona_id: str
    version: int
    display_name: str
    speaking_style: str
    facts: Mapping[str, PersonaFact] = field(default_factory=dict)
    forbidden_claims: tuple[str, ...] = ()
    forbidden_topics: tuple[str, ...] = ()
    visual_references: tuple[str, ...] = ()
    language: str = "ru"
    changed_reason: str = "первая версия"

    def __post_init__(self) -> None:
        if not self.persona_id.strip():
            raise ValueError("персона без идентификатора не сохраняется")
        if self.version < 1:
            raise ValueError("версия канона начинается с единицы")
        if not self.display_name.strip():
            raise ValueError("у ведущего должно быть имя")
        for key, fact in self.facts.items():
            if key != fact.key:
                raise ValueError(
                    f"факт лежит под ключом {key!r}, а называет себя {fact.key!r}")

    # ---- чтение

    def fact(self, key: str) -> PersonaFact | None:
        return self.facts.get(key)

    def value_of(self, key: str) -> str:
        found = self.facts.get(key)
        return found.value if found else ""

    def locked_keys(self) -> frozenset[str]:
        return frozenset(key for key, fact in self.facts.items() if fact.locked)

    def to_dict(self) -> dict[str, Any]:
        return {"persona_id": self.persona_id, "version": self.version,
                "display_name": self.display_name,
                "speaking_style": self.speaking_style,
                "facts": {key: fact.to_dict() for key, fact in sorted(self.facts.items())},
                "forbidden_claims": list(self.forbidden_claims),
                "forbidden_topics": list(self.forbidden_topics),
                "visual_references": list(self.visual_references),
                "language": self.language, "changed_reason": self.changed_reason}

    # ---- изменение

    def revise(self, changes: Mapping[str, PersonaFact], *, version: int,
               reason: str, unlock: Iterable[str] = ()) -> "StreamerPersona":
        """Новая версия канона. Единственный способ его изменить.

        Повышение версии обязательно и проверяется: правка «на месте» не
        оставляет следа, по которому можно понять, с какой сцены ведущий стал
        другим.
        """
        if version <= self.version:
            raise PersonaCanonViolation(
                f"канон версии {self.version} не переписывается версией {version}: "
                f"изменение персоны обязано быть отдельной версией")
        if not str(reason).strip():
            raise PersonaCanonViolation(
                "изменение канона без причины неотличимо от случайного")
        unlocked = frozenset(str(key) for key in unlock)
        merged = dict(self.facts)
        for key, fact in changes.items():
            existing = merged.get(key)
            if existing is not None and existing.locked and key not in unlocked:
                why = existing.note or "изменение означает другого ведущего"
                raise PersonaCanonViolation(
                    f"факт {key!r} заперт: {why}. Отпирается отдельным "
                    f"действием, а не вместе с правкой")
            merged[key] = fact
        return replace(self, version=int(version), facts=merged,
                       changed_reason=str(reason))

    def forbid(self, text: str) -> str:
        """Назвать запретное утверждение или тему, если оно есть в тексте."""
        lowered = str(text or "").lower()
        for claim in self.forbidden_claims:
            if claim.lower() in lowered:
                return claim
        for topic in self.forbidden_topics:
            if topic.lower() in lowered:
                return topic
        return ""


@dataclass(frozen=True, slots=True)
class ContinuityObservation:
    """Что модель или проверка «увидела» в готовом кадре."""

    key: str
    value: str
    source: str
    observed_at_epoch_s: float
    shot_id: str = ""


@dataclass(frozen=True, slots=True)
class ContinuityFinding:
    """Как наблюдение соотносится с каноном. Канон при этом не меняется."""

    key: str
    status: ContinuityStatus
    canon_value: str
    observed_value: str
    locked: bool
    detail: str = ""

    @property
    def blocking(self) -> bool:
        """Расхождение по запертому факту — это брак, а не разночтение."""
        return self.status is ContinuityStatus.CONTESTED and self.locked

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "status": self.status.value,
                "canon": self.canon_value, "observed": self.observed_value,
                "locked": self.locked, "blocking": self.blocking,
                "detail": self.detail}


class ContinuityLedger:
    """Журнал наблюдений о персоне. Пишет только наблюдения, не канон.

    Разделение простое и намеренное: канон меняет человек, версией; журнал
    накапливает то, что видели, включая расхождения. Слить их в одно значило бы
    дать модели право переписать ведущего, повторив утверждение достаточно
    уверенно.
    """

    def __init__(self, persona: StreamerPersona) -> None:
        self.persona = persona
        self.observations: list[ContinuityObservation] = []

    def observe(self, observation: ContinuityObservation) -> ContinuityFinding:
        """Записать наблюдение и сказать, как оно ложится на канон."""
        self.observations.append(observation)
        return self.compare(observation)

    def compare(self, observation: ContinuityObservation) -> ContinuityFinding:
        """Сопоставить наблюдение с каноном, ничего не записывая."""
        fact = self.persona.fact(observation.key)
        if fact is None:
            return ContinuityFinding(
                key=observation.key, status=ContinuityStatus.UNKNOWN,
                canon_value="", observed_value=observation.value, locked=False,
                detail="канон об этом ничего не говорит; наблюдение записано и "
                       "канона не меняет")
        if _same(fact.value, observation.value):
            return ContinuityFinding(
                key=observation.key, status=ContinuityStatus.CONFIRMED,
                canon_value=fact.value, observed_value=observation.value,
                locked=fact.locked)
        return ContinuityFinding(
            key=observation.key, status=ContinuityStatus.CONTESTED,
            canon_value=fact.value, observed_value=observation.value,
            locked=fact.locked,
            detail=("расхождение по запертому факту: кадр не принимается"
                    if fact.locked else
                    "расхождение записано как конфликт; канон не изменён"))

    def findings(self) -> list[ContinuityFinding]:
        return [self.compare(observation) for observation in self.observations]


def _same(canon: str, observed: str) -> bool:
    return canon.strip().casefold() == observed.strip().casefold()


__all__ = ["ContinuityFinding", "ContinuityLedger", "ContinuityObservation",
           "ContinuityStatus", "PersonaCanonViolation", "PersonaFact",
           "StreamerPersona"]
