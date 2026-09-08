"""Путь от замысла до задания генератору: сценарий → кадры → запросы.

Смысл этого модуля не в том, чтобы «собрать текст запроса». Смысл в том, ЧТО
именно в него попадёт, а что не попадёт никогда.

Запрос уходит наружу, к чужому сервису. Всё, что в нём окажется, окажется у
провайдера. Поэтому запрос здесь не склеивается из свободного контекста —
такого параметра просто нет. Он собирается по именам из перечисленных полей
канона и описания кадра, и добавить в него «ещё немного контекста на всякий
случай» не через что. Это не проверка, которую можно забыть вызвать: это
отсутствие двери.

Второе решение — про запретные утверждения. Кадр, чьё описание содержит то,
что персоне запрещено утверждать, не превращается в «запрос без этой фразы».
Он не превращается в запрос вообще: тихая правка означала бы, что сцена
осталась в плане и снялась не той, а заметить это можно будет только в готовом
ролике.

Раскадровка детерминирована. Модель здесь не нужна: длительность делится,
кадры нумеруются, порядок сохраняется. Тратить на это вызов модели значило бы
платить за то, что арифметика делает точнее.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence
import hashlib

from .higgsfield_browser_contracts import BrowserGenerationRequest, MediaKind
from .persona import StreamerPersona

# Ключи канона, попадающие в запрос к генератору. Перечень закрыт: всё, чего
# здесь нет, наружу не уходит, даже если лежит в канонe.
PROMPT_FACT_KEYS: tuple[str, ...] = (
    "appearance", "outfit", "location", "props", "lighting", "camera",
)
# Сколько секунд максимум в одном кадре. Дальше кадр делится: длинная
# непрерывная генерация — это та самая единственная долгая работа, от которой
# поток и не должен зависеть.
MAX_SHOT_SECONDS = 10.0
MIN_SHOT_SECONDS = 1.0


class ForbiddenContent(ValueError):
    """Кадр требует того, что персоне запрещено."""

    def __init__(self, shot_id: str, matched: str) -> None:
        super().__init__(
            f"кадр {shot_id}: описание содержит запретное для персоны "
            f"{matched!r}; кадр не отправляется")
        self.shot_id = shot_id
        self.matched = matched


@dataclass(frozen=True, slots=True)
class ScriptBeat:
    """Один смысловой такт сценария."""

    beat_id: str
    text: str
    seconds: float
    media_kind: MediaKind = MediaKind.VIDEO
    visual_note: str = ""

    def __post_init__(self) -> None:
        if not self.beat_id.strip():
            raise ValueError("у такта сценария должен быть идентификатор")
        if not self.text.strip():
            raise ValueError(f"такт {self.beat_id} пуст")
        if self.seconds <= 0:
            raise ValueError(f"такт {self.beat_id} без длительности")


@dataclass(frozen=True, slots=True)
class Shot:
    """Один кадр: то, что уйдёт одним заданием генератору."""

    shot_id: str
    beat_id: str
    ordinal: int
    media_kind: MediaKind
    seconds: float
    description: str
    aspect_ratio: str = "9:16"
    continuity_keys: tuple[str, ...] = PROMPT_FACT_KEYS

    def to_dict(self) -> dict[str, Any]:
        return {"shot_id": self.shot_id, "beat_id": self.beat_id,
                "ordinal": self.ordinal, "media_kind": self.media_kind.value,
                "seconds": self.seconds, "aspect_ratio": self.aspect_ratio}


@dataclass(frozen=True, slots=True)
class Script:
    """Сценарий сегмента: такты по порядку."""

    script_id: str
    title: str
    beats: tuple[ScriptBeat, ...]

    def __post_init__(self) -> None:
        if not self.beats:
            raise ValueError("сценарий без тактов не раскадровывается")
        seen = {beat.beat_id for beat in self.beats}
        if len(seen) != len(self.beats):
            raise ValueError("идентификаторы тактов повторяются")

    @property
    def seconds(self) -> float:
        return sum(beat.seconds for beat in self.beats)


def plan_shots(script: Script, persona: StreamerPersona, *,
               max_shot_seconds: float = MAX_SHOT_SECONDS,
               aspect_ratio: str = "9:16") -> tuple[Shot, ...]:
    """Разложить сценарий на кадры. Детерминированно и без модели."""
    if max_shot_seconds < MIN_SHOT_SECONDS:
        raise ValueError(
            f"кадр короче {MIN_SHOT_SECONDS} с не генерируется ни одним "
            f"известным путём")
    shots: list[Shot] = []
    ordinal = 0
    for beat in script.beats:
        forbidden = persona.forbid(beat.text) or persona.forbid(beat.visual_note)
        if forbidden:
            raise ForbiddenContent(beat.beat_id, forbidden)
        pieces = max(1, int(-(-beat.seconds // max_shot_seconds)))
        each = beat.seconds / pieces
        for piece in range(pieces):
            shots.append(Shot(
                shot_id=f"{script.script_id}.{beat.beat_id}.{piece}",
                beat_id=beat.beat_id, ordinal=ordinal,
                media_kind=beat.media_kind, seconds=round(each, 3),
                description=(beat.visual_note or beat.text).strip(),
                aspect_ratio=aspect_ratio))
            ordinal += 1
    return tuple(shots)


def build_prompt(shot: Shot, persona: StreamerPersona) -> str:
    """Собрать текст запроса ПОИМЁННО. Свободного контекста здесь нет.

    Ни одного параметра, через который в запрос можно передать «остальное»:
    именно так в запрос к чужому сервису и попадают ключи, пути и куски чужой
    переписки. Что не перечислено — не уходит.
    """
    forbidden = persona.forbid(shot.description)
    if forbidden:
        raise ForbiddenContent(shot.shot_id, forbidden)

    parts = [shot.description.strip(),
             f"стиль речи: {persona.speaking_style}".strip()]
    for key in shot.continuity_keys:
        value = persona.value_of(key)
        if value:
            parts.append(f"{key}: {value}")
    parts.append(f"кадр {shot.aspect_ratio}")
    if shot.media_kind is MediaKind.VIDEO:
        parts.append(f"длительность {shot.seconds:g} с")
    return "; ".join(part for part in parts if part)


def prompt_digest(prompt: str) -> str:
    """Отпечаток запроса для свидетельства. Сам запрос в журнал не обязателен."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def to_generation_requests(shots: Sequence[Shot], persona: StreamerPersona, *,
                           mission_id: str, output_workspace: Path,
                           deadline_epoch_s: float | None = None,
                           max_attempts: int = 2,
                           reference_assets: Sequence[Path] = ()
                           ) -> tuple[BrowserGenerationRequest, ...]:
    """Превратить кадры в задания. Одно задание — один кадр."""
    requests = []
    for shot in shots:
        requests.append(BrowserGenerationRequest(
            mission_id=mission_id, media_kind=shot.media_kind,
            prompt=build_prompt(shot, persona),
            output_workspace=Path(output_workspace),
            aspect_ratio=shot.aspect_ratio,
            duration_seconds=(shot.seconds if shot.media_kind is MediaKind.VIDEO
                              else None),
            reference_assets=tuple(reference_assets),
            deadline_epoch_s=deadline_epoch_s,
            max_attempts=max_attempts,
            job_id=f"{mission_id}:{shot.shot_id}"))
    return tuple(requests)


@dataclass(frozen=True, slots=True)
class ShotPlan:
    """Раскадровка вместе с запросами и их отпечатками — для свидетельства."""

    script_id: str
    shots: tuple[Shot, ...]
    requests: tuple[BrowserGenerationRequest, ...]
    prompt_digests: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"script_id": self.script_id,
                "shots": [shot.to_dict() for shot in self.shots],
                "jobs": [request.job_id for request in self.requests],
                "prompt_digests": dict(self.prompt_digests)}


def plan_segment(script: Script, persona: StreamerPersona, *, mission_id: str,
                 output_workspace: Path, aspect_ratio: str = "9:16",
                 max_shot_seconds: float = MAX_SHOT_SECONDS,
                 deadline_epoch_s: float | None = None,
                 max_attempts: int = 2) -> ShotPlan:
    """Весь путь от сценария до заданий, одной операцией."""
    shots = plan_shots(script, persona, max_shot_seconds=max_shot_seconds,
                       aspect_ratio=aspect_ratio)
    requests = to_generation_requests(
        shots, persona, mission_id=mission_id, output_workspace=output_workspace,
        deadline_epoch_s=deadline_epoch_s, max_attempts=max_attempts)
    digests = {request.job_id: prompt_digest(request.prompt)
               for request in requests}
    return ShotPlan(script_id=script.script_id, shots=shots, requests=requests,
                    prompt_digests=digests)


__all__ = ["MAX_SHOT_SECONDS", "MIN_SHOT_SECONDS", "PROMPT_FACT_KEYS",
           "ForbiddenContent", "Script", "ScriptBeat", "Shot", "ShotPlan",
           "build_prompt", "plan_segment", "plan_shots", "prompt_digest",
           "to_generation_requests"]
