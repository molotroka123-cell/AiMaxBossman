"""Модель данных Video Factory (Этап 7).

Состояние джобы и сцен — единственный durable-чекпоинт на диске (`job.json`,
пишется атомарно). Никакого второго durable-хранилища мы не заводим: зеркало в
Postgres опционально и best-effort (см. pipeline._mirror_db).

Инвариант takes: артефакт сцены НИКОГДА не перезаписывается. Каждая попытка
генерации пишет отдельный «дубль» `take-NNN.mp4`; выбранный (валидный) дубль
кладётся в `Scene.output`, а весь список попыток — в `Scene.takes`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


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
