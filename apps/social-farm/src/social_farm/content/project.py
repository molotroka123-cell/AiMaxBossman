"""Проект контента и выбор, из которого потом соберётся ревизия.

`12_CONTENT_STUDIO`: студия нейтральна к провайдеру. Проект ничего не знает ни
про Instagram, ни про размеры кадра — он держит замысел (бриф, исходники,
варианты) и выбор автора. Всё, что зависит от площадки, начинается на шаге
«план рендера», когда появляется конкретный аккаунт.

Здесь же — состав `Selection`, который целиком входит в хеш ревизии
(решение C4): подпись, ассеты, аккаунты назначения, время. Ничего из этого
нельзя изменить после одобрения, не получив другой хеш, поэтому набор полей
здесь — не удобство, а граница неизменяемости.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..domain.identity import utc_now
from ..media.asset import MediaAsset


class ProjectStatus(str, Enum):
    """Перечень из `content_project.schema.json`, закрытый."""

    DRAFT = "DRAFT"
    GENERATING = "GENERATING"
    READY_REVIEW = "READY_REVIEW"
    APPROVED = "APPROVED"
    SCHEDULED = "SCHEDULED"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class ContentType(str, Enum):
    """Тип контента. Имена нейтральны к провайдеру, как и вся студия."""

    IMAGE = "IMAGE"
    CAROUSEL = "CAROUSEL"
    REEL = "REEL"
    STORY = "STORY"


#: Тип контента → нормализованная возможность (`domain/safety.py`).
CAPABILITY_FOR: dict[ContentType, str] = {
    ContentType.IMAGE: "media.publish.image",
    ContentType.CAROUSEL: "media.publish.carousel",
    ContentType.REEL: "media.publish.reel",
    ContentType.STORY: "media.publish.story",
}

#: Сколько ассетов допускает тип контента. Это свойство СОСТАВА, а не файла,
#: поэтому проверяется здесь, а не движком правил медиа.
ASSET_COUNT: dict[ContentType, tuple[int, int]] = {
    ContentType.IMAGE: (1, 1),
    ContentType.CAROUSEL: (2, 10),
    ContentType.REEL: (1, 1),
    ContentType.STORY: (1, 1),
}


class SelectionError(ValueError):
    """Выбор автора не собирается в публикуемый состав."""


@dataclass(frozen=True, slots=True)
class ContentProject:
    """Проект: замысел и его исходники (`content_project.schema.json`)."""

    id: str
    status: ProjectStatus = ProjectStatus.DRAFT
    brief: str | None = None
    target_account_ids: tuple[str, ...] = ()
    current_revision_id: str | None = None
    created_at: str = field(default_factory=utc_now)

    def to_schema_dict(self) -> dict[str, Any]:
        return {"id": self.id, "brief": self.brief, "status": self.status.value,
                "target_account_ids": list(self.target_account_ids),
                "current_revision_id": self.current_revision_id,
                "created_at": self.created_at}


@dataclass(frozen=True, slots=True)
class Selection:
    """Что именно автор выбрал публиковать (шаг 4 конвейера).

    Ассеты — объекты `MediaAsset`, а не идентификаторы. Разница
    принципиальная: идентификатор можно выдумать, а `MediaAsset` без
    содержимого в хранилище не проходит сверку конвейера. Тип здесь несёт
    инвариант «сгенерированное медиа сначала становится нашим файлом».
    """

    caption: str
    assets: tuple[MediaAsset, ...]
    target_account_ids: tuple[str, ...]
    content_type: ContentType
    schedule_at: str | None = None

    def __post_init__(self) -> None:
        if not self.assets:
            raise SelectionError("публиковать нечего: не выбрано ни одного ассета")
        if not self.target_account_ids:
            raise SelectionError("не выбрано ни одного аккаунта назначения")
        low, high = ASSET_COUNT[self.content_type]
        if not low <= len(self.assets) <= high:
            raise SelectionError(
                f"{self.content_type.value} требует от {low} до {high} ассетов, "
                f"а выбрано {len(self.assets)}")
        seen: set[str] = set()
        for asset in self.assets:
            if asset.id in seen:
                raise SelectionError(f"ассет {asset.id} выбран дважды")
            seen.add(asset.id)

    @property
    def capability(self) -> str:
        return CAPABILITY_FOR[self.content_type]

    def with_assets(self, assets: tuple[MediaAsset, ...]) -> "Selection":
        """Тот же выбор, но с другим составом ассетов (после рендера)."""
        return Selection(caption=self.caption, assets=tuple(assets),
                         target_account_ids=self.target_account_ids,
                         content_type=self.content_type, schedule_at=self.schedule_at)


__all__ = ["ASSET_COUNT", "CAPABILITY_FOR", "ContentProject", "ContentType",
           "ProjectStatus", "Selection", "SelectionError"]
