"""Модель данных Video Factory (Этап 7).

Состояние джобы и сцен — единственный durable-чекпоинт на диске (`job.json`,
пишется атомарно). Никакого второго durable-хранилища мы не заводим: зеркало в
Postgres опционально и best-effort (см. pipeline._mirror_db).

Инвариант takes: артефакт сцены НИКОГДА не перезаписывается. Каждая попытка
генерации пишет отдельный «дубль» `take-NNN.mp4`; выбранный (валидный) дубль
кладётся в `Scene.output`, а весь список попыток — в `Scene.takes`.
"""
from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field
from enum import Enum

from .. import errors

# --- инвариант длительности --------------------------------------------------
#
# До закрытия VF-004/VF-005 длительность не проверялась НИГДЕ, кроме pydantic-а
# в routes.py (0 < d <= 120). Библиотечный вход (`VideoFactory.create`) и
# чекпоинт на диске пропускали что угодно, и это давало сразу два дефекта:
#   * NaN/inf доезжали до `json.dump`, который пишет НЕстандартные литералы
#     `NaN`/`Infinity` — такой `job.json` не читается ни одним строгим парсером
#     (браузер, jq, Go/Rust-клиент), то есть чекпоинт становится нечитаемым для
#     всех, кроме Python;
#   * NaN у модели превращался в 0.1 с у рендерера (`max(0.1, nan)` → 0.1), а
#     duration_s=1e6 запускал ffmpeg без верхней границы и без таймаута.
# Поэтому длительность нормализуется ОДНОЙ функцией на всех входах.
MIN_SCENE_DURATION_S = 0.1
_DEFAULT_MAX_SCENE_DURATION_S = 600.0


def max_scene_duration_s() -> float:
    """Верхняя граница длительности сцены (сек). Оператор может поднять её через
    `BOSSMAN_VIDEO_MAX_SCENE_S`; мусор в переменной — молча дефолт."""
    raw = os.getenv("BOSSMAN_VIDEO_MAX_SCENE_S", "").strip()
    if raw:
        try:
            v = float(raw)
        except ValueError:
            return _DEFAULT_MAX_SCENE_DURATION_S
        if math.isfinite(v) and v > 0.0:
            return v
    return _DEFAULT_MAX_SCENE_DURATION_S


def normalize_duration(value, *, field_name: str = "duration_s") -> float:
    """Вернуть конечную длительность в [MIN, MAX] или бросить типизированную
    `errors.ArtifactRejected` (422, не retryable).

    Бросаем, а не подчищаем молча: тихая замена NaN на 0.1 — это ровно то
    расхождение «модель говорит одно, рендерер делает другое», из-за которого
    владелец видит успешную сцену не той длительности, что заказывал."""
    try:
        d = float(value)
    except (TypeError, ValueError) as exc:
        raise errors.ArtifactRejected(
            f"{field_name} is not a number: {value!r}",
            extra={"field": field_name},
        ) from exc
    if not math.isfinite(d):
        raise errors.ArtifactRejected(
            f"{field_name} must be finite, got {d!r}",
            extra={"field": field_name},
        )
    cap = max_scene_duration_s()
    if d < MIN_SCENE_DURATION_S or d > cap:
        raise errors.ArtifactRejected(
            f"{field_name}={d} out of range [{MIN_SCENE_DURATION_S}, {cap}]",
            extra={"field": field_name, "min": MIN_SCENE_DURATION_S, "max": cap},
        )
    return d


class JobState(str, Enum):
    """Жизненный цикл джобы. Значения совпадают с прототипом (на них держится
    приёмочный тест), плюс INTERRUPTED для сверки после рестарта."""

    PLANNED = "planned"
    QUEUED = "queued"
    RUNNING = "running"
    FAILED = "failed"
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


# --- статусы сцены (простые строки, совместимо с checkpoint_scene(status=...)) ---

SCENE_PLANNED = "planned"
SCENE_RUNNING = "running"
SCENE_COMPLETE = "complete"
SCENE_FAILED = "failed"


@dataclass(slots=True)
class Scene:
    """Одна сцена джобы. `output` — выбранный (валидный) дубль; `takes` — все
    произведённые попытки (включая забракованные), чтобы retry не затирал
    предыдущий артефакт и оставлял след для разбора."""

    id: str
    prompt: str
    duration_s: float = 5.0
    status: str = SCENE_PLANNED
    output: str | None = None          # имя выбранного дубля (take-NNN.mp4)
    attempts: int = 0
    takes: list[str] = field(default_factory=list)   # все произведённые дубли
    error: str | None = None
    # Машинный код причины (ErrorCode.value). До VF-003 наружу уезжал только
    # человеческий текст, а типизированный код оставался в логе — владелец не мог
    # отличить «провайдер упал» от «результат не прошёл валидацию» без грепа логов.
    error_code: str | None = None

    def to_public(self) -> dict:
        return {
            "id": self.id,
            "prompt": self.prompt,
            "duration_s": self.duration_s,
            "status": self.status,
            "output": self.output,
            "attempts": self.attempts,
            "takes": list(self.takes),
            "error": self.error,
            "error_code": self.error_code,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Scene":
        return cls(
            id=str(d["id"]),
            prompt=str(d.get("prompt", "")),
            duration_s=float(d.get("duration_s", 5.0)),
            status=str(d.get("status", SCENE_PLANNED)),
            output=d.get("output"),
            attempts=int(d.get("attempts", 0)),
            takes=list(d.get("takes", []) or []),
            error=d.get("error"),
            error_code=d.get("error_code"),
        )


@dataclass(slots=True)
class VideoJob:
    """Джоба = упорядоченный список сцен + состояние. `id` — hex uuid, `created_at`
    — эпоха секунд. Позиционный контракт (id, title, scenes) совместим с
    прототипом."""

    id: str
    title: str
    scenes: list[Scene]
    state: JobState = JobState.PLANNED
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    error: str | None = None

    def scene(self, scene_id: str) -> Scene:
        for s in self.scenes:
            if s.id == scene_id:
                return s
        raise KeyError(f"нет такой сцены: {scene_id}")

    def to_public(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "state": self.state.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
            "scenes": [s.to_public() for s in self.scenes],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "VideoJob":
        return cls(
            id=str(d["id"]),
            title=str(d.get("title", "")),
            scenes=[Scene.from_dict(s) for s in d.get("scenes", [])],
            state=JobState(str(d.get("state", "planned"))),
            created_at=float(d.get("created_at", time.time())),
            updated_at=float(d.get("updated_at", d.get("created_at", time.time()))),
            error=d.get("error"),
        )
