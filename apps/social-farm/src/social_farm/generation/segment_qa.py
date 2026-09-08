"""Приёмка готового кадра и сборка сегмента для студии.

Между «файл получен» и «сегмент можно ставить в эфир» стоят две вещи, и обе
здесь.

**Непрерывность.** Кадр может быть безупречным сам по себе и при этом
показывать другого человека в другой куртке. Расхождение по запертому факту —
брак; по остальному — конфликт, который записывается конфликтом и канона не
меняет (`persona.ContinuityLedger`).

**Свидетельство.** Студия получает не список файлов, а перечень принятых
кадров с их хешами, длительностями и порядком. Сегмент, у которого хоть один
кадр не принят, не собирается: подставить вместо него предыдущий и не сказать
об этом — это ролик, который никто не согласовывал.

Публикацией и эфиром здесь не пахнет. Сборка сегмента — это файл на диске
владельца; наружу его выпускает существующий разрешительный путь, а не этот
модуль.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .higgsfield_browser_contracts import ArtifactReceipt, MediaKind
from .persona import (ContinuityFinding, ContinuityLedger, ContinuityObservation,
                      ContinuityStatus, StreamerPersona)
from .pipeline import Shot

# Насколько готовый кадр может разойтись с заказанной длительностью. Генераторы
# округляют к своей сетке кадров, и требовать точности до миллисекунды значило
# бы браковать исправное.
DURATION_TOLERANCE_S = 0.75


@dataclass(frozen=True, slots=True)
class ShotVerdict:
    """Принят кадр или нет — и по какой именно причине."""

    shot_id: str
    accepted: bool
    reasons: tuple[str, ...] = ()
    findings: tuple[ContinuityFinding, ...] = ()

    @property
    def contested(self) -> tuple[ContinuityFinding, ...]:
        return tuple(f for f in self.findings
                     if f.status is ContinuityStatus.CONTESTED)

    def to_dict(self) -> dict[str, Any]:
        return {"shot_id": self.shot_id, "accepted": self.accepted,
                "reasons": list(self.reasons),
                "findings": [f.to_dict() for f in self.findings]}


def review_shot(shot: Shot, artifact: ArtifactReceipt, *,
                ledger: ContinuityLedger,
                observed: Mapping[str, str] | None = None,
                now: float | None = None,
                measured_duration_s: float | None = None) -> ShotVerdict:
    """Проверить один готовый кадр против заказа и канона."""
    moment = time.time() if now is None else now
    reasons: list[str] = []

    if artifact.media_kind is not shot.media_kind:
        reasons.append(
            f"заказан {shot.media_kind.value}, получен {artifact.media_kind.value}")
    if (shot.media_kind is MediaKind.VIDEO and measured_duration_s is not None
            and abs(measured_duration_s - shot.seconds) > DURATION_TOLERANCE_S):
        reasons.append(
            f"длительность {measured_duration_s:.2f} с вместо заказанных "
            f"{shot.seconds:.2f} с")

    findings = []
    for key, value in dict(observed or {}).items():
        finding = ledger.observe(ContinuityObservation(
            key=key, value=value, source=f"qa:{shot.shot_id}",
            observed_at_epoch_s=moment, shot_id=shot.shot_id))
        findings.append(finding)
        if finding.blocking:
            reasons.append(
                f"{key}: в кадре {finding.observed_value!r}, канон держит "
                f"{finding.canon_value!r}")

    return ShotVerdict(shot_id=shot.shot_id, accepted=not reasons,
                       reasons=tuple(reasons), findings=tuple(findings))


@dataclass(frozen=True, slots=True)
class SegmentManifest:
    """То, что забирает студия. Каждый кадр — со своим свидетельством."""

    segment_id: str
    persona_id: str
    persona_version: int
    entries: tuple[dict[str, Any], ...]
    total_seconds: float
    built_at_epoch_s: float

    def to_dict(self) -> dict[str, Any]:
        return {"version": 1, "segment_id": self.segment_id,
                "persona": {"id": self.persona_id, "version": self.persona_version},
                "total_seconds": round(self.total_seconds, 3),
                "built_at_epoch_s": self.built_at_epoch_s,
                "shots": [dict(entry) for entry in self.entries]}

    def write(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8")
        return target


class IncompleteSegment(RuntimeError):
    """Сегмент нельзя собрать: часть кадров не принята.

    Подставить вместо непринятого кадра соседний и промолчать — это ролик,
    которого никто не утверждал. Поэтому сборка отказывает, а не выкручивается.
    """

    def __init__(self, missing: Sequence[str]) -> None:
        super().__init__(
            f"сегмент не собирается: не приняты кадры {list(missing)}")
        self.missing = tuple(missing)


def build_segment(segment_id: str, persona: StreamerPersona,
                  shots: Sequence[Shot],
                  artifacts: Mapping[str, ArtifactReceipt],
                  verdicts: Mapping[str, ShotVerdict], *,
                  now: float | None = None) -> SegmentManifest:
    """Собрать перечень принятых кадров по порядку раскадровки."""
    moment = time.time() if now is None else now
    missing = [shot.shot_id for shot in shots
               if shot.shot_id not in artifacts
               or not verdicts.get(shot.shot_id,
                                   ShotVerdict(shot.shot_id, False)).accepted]
    if missing:
        raise IncompleteSegment(missing)

    entries = []
    total = 0.0
    for shot in sorted(shots, key=lambda item: item.ordinal):
        artifact = artifacts[shot.shot_id]
        total += shot.seconds
        entries.append({
            "shot_id": shot.shot_id, "ordinal": shot.ordinal,
            "beat_id": shot.beat_id, "media_kind": shot.media_kind.value,
            "seconds": shot.seconds, "aspect_ratio": shot.aspect_ratio,
            "job_id": artifact.job_id, "path": str(artifact.path),
            "sha256": artifact.sha256, "bytes": artifact.bytes_size,
            "qa": dict(artifact.qa_verdicts)})
    return SegmentManifest(segment_id=segment_id, persona_id=persona.persona_id,
                           persona_version=persona.version,
                           entries=tuple(entries), total_seconds=total,
                           built_at_epoch_s=moment)


__all__ = ["DURATION_TOLERANCE_S", "IncompleteSegment", "SegmentManifest",
           "ShotVerdict", "build_segment", "review_shot"]
