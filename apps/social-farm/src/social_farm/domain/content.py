"""Неизменяемые ревизии контента и одобрение, привязанное к точной ревизии.

Спека требует, чтобы одобрение было привязано к хешу ревизии, но не говорит,
что именно хешируется и как канонизируется (`DIGEST_CORE` G2), а в таблице
`approvals` вообще нет ссылки на ревизию (C5). Без обоих решений инвариант
«одобрено ровно то, что опубликуется» неисполним: сравнивать нечего.

Здесь оба закрыты. Хеш считается одной функцией над канонизированным JSON, и
она — единственное место, где это происходит. Одобрение несёт хеш, который
человек видел; перед публикацией он сверяется с текущим. Любая правка рождает
новую ревизию, а значит новый хеш, а значит старое одобрение становится
недействительным само, без отдельного механизма отзыва.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


class ContentError(ValueError):
    """Нарушение неизменяемости или состава ревизии."""


def canonical_json(payload: Any) -> str:
    """Канонизация для хеширования: сортировка ключей, UTF-8, без пробелов.

    Два одинаковых по смыслу объекта обязаны дать одну строку, иначе хеш
    начнёт расходиться от порядка ключей, и одобрение будет слетать само по
    себе. Это и есть вся ценность канонизации.
    """
    return json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str)


def content_hash(*, project_id: str, revision_no: int, caption: str,
                 assets: list[dict[str, Any]] | None = None,
                 target_account_ids: list[str] | None = None,
                 schedule_at: str | None = None) -> str:
    """sha256 от состава ревизии.

    В хеш входит то, от чего зависит результат публикации: текст, СОСТАВ медиа
    вместе с их контрольными суммами, список аккаунтов назначения и время.
    Контрольные суммы обязательны: подменённый файл при том же идентификаторе
    иначе прошёл бы под старым одобрением.
    """
    body = {
        "project_id": project_id,
        "revision_no": int(revision_no),
        "caption": caption or "",
        "assets": sorted(
            ({"id": str(a.get("id") or ""),
              "checksum_sha256": str(a.get("checksum_sha256") or "")}
             for a in (assets or [])),
            key=lambda a: (a["id"], a["checksum_sha256"])),
        "target_account_ids": sorted(str(t) for t in (target_account_ids or [])),
        "schedule_at": _utc(schedule_at),
    }
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _utc(value: Any) -> str:
    """Время в хеше — всегда UTC ISO-8601. Иначе смена часового пояса меняет хеш."""
    if not value:
        return ""
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat()
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return str(value)
    moment = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class ContentRevision:
    """Ревизия. Неизменяема по построению: `frozen=True` и хеш внутри."""

    id: str
    project_id: str
    revision_no: int
    caption: str
    assets: tuple[dict[str, Any], ...] = ()
    target_account_ids: tuple[str, ...] = ()
    schedule_at: str | None = None
    supersedes_revision_id: str | None = None
    approved_at: str | None = None
    approved_by: str | None = None
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return content_hash(project_id=self.project_id, revision_no=self.revision_no,
                            caption=self.caption, assets=list(self.assets),
                            target_account_ids=list(self.target_account_ids),
                            schedule_at=self.schedule_at)

    @property
    def approved(self) -> bool:
        return bool(self.approved_at)

    def next_revision(self, **changes: Any) -> "ContentRevision":
        """Правка — это НОВАЯ ревизия, а не изменение старой.

        Одобрение не переносится: то, что человек видел, осталось в предыдущей
        ревизии, и её хеш другой. Отдельный механизм «отозвать одобрение» не
        нужен — он получается сам.
        """
        forbidden = {"id", "project_id", "revision_no", "supersedes_revision_id",
                     "approved_at", "approved_by"} & set(changes)
        if forbidden:
            raise ContentError(
                f"эти поля нельзя задать при создании следующей ревизии: "
                f"{sorted(forbidden)} — они вычисляются")
        data = {"caption": self.caption, "assets": self.assets,
                "target_account_ids": self.target_account_ids,
                "schedule_at": self.schedule_at, "metadata": dict(self.metadata)}
        data.update(changes)
        return ContentRevision(
            id=f"{self.project_id}:rev{self.revision_no + 1}",
            project_id=self.project_id, revision_no=self.revision_no + 1,
            caption=str(data["caption"]), assets=tuple(data["assets"]),
            target_account_ids=tuple(data["target_account_ids"]),
            schedule_at=data["schedule_at"], supersedes_revision_id=self.id,
            approved_at=None, approved_by=None,
            created_at=datetime.now(timezone.utc).isoformat(),
            metadata=dict(data["metadata"]))

    def approve(self, actor_id: str, *, at: str | None = None) -> "ContentRevision":
        if not actor_id:
            raise ContentError("одобрение без указания, кто одобрил, не принимается")
        return ContentRevision(
            id=self.id, project_id=self.project_id, revision_no=self.revision_no,
            caption=self.caption, assets=self.assets,
            target_account_ids=self.target_account_ids, schedule_at=self.schedule_at,
            supersedes_revision_id=self.supersedes_revision_id,
            approved_at=at or datetime.now(timezone.utc).isoformat(),
            approved_by=actor_id, created_at=self.created_at,
            metadata=dict(self.metadata))


@dataclass(frozen=True, slots=True)
class Approval:
    """Одобрение работы, привязанное к точной ревизии контента (решение C5)."""

    id: str
    job_id: str
    status: str
    content_revision_id: str = ""
    approved_content_hash: str = ""
    capability: str = ""
    account_id: str = ""
    policy_version: int = 0
    requested_at: str = ""
    expires_at: str | None = None
    decided_at: str | None = None
    actor_id: str | None = None
    decision_note: str = ""

    def is_expired(self, now: datetime | None = None) -> bool:
        if not self.expires_at:
            return False
        moment = now or datetime.now(timezone.utc)
        try:
            deadline = datetime.fromisoformat(str(self.expires_at).replace("Z", "+00:00"))
        except ValueError:
            return False
        if not deadline.tzinfo:
            deadline = deadline.replace(tzinfo=timezone.utc)
        return moment >= deadline

    def validate_for(self, revision: ContentRevision, *, capability: str,
                     account_id: str, policy_version: int,
                     now: datetime | None = None) -> None:
        """Годится ли это одобрение прямо сейчас, перед самым действием.

        Проверяется всё, что могло измениться с момента, когда человек нажал
        «да»: содержимое, действие, аккаунт, версия политики и срок. Каждое
        расхождение означает, что одобрено было не это.
        """
        if self.status != "APPROVED":
            raise ContentError(f"одобрение {self.id} в состоянии {self.status}")
        if self.is_expired(now):
            raise ContentError(
                f"одобрение {self.id} истекло ({self.expires_at}); "
                f"работа отменяется с причиной APPROVAL_EXPIRED")
        if self.content_revision_id != revision.id:
            raise ContentError(
                f"одобрение выдано на ревизию {self.content_revision_id}, "
                f"а публикуется {revision.id}")
        if self.approved_content_hash != revision.content_hash:
            raise ContentError(
                "содержимое изменилось после одобрения: хеш ревизии не совпадает. "
                "Нужно новое одобрение — опубликовать можно только то, что видел человек.")
        if self.capability and self.capability != capability:
            raise ContentError(
                f"одобрение выдано на действие {self.capability}, а выполняется {capability}")
        if self.account_id and self.account_id != account_id:
            raise ContentError(
                f"одобрение выдано для аккаунта {self.account_id}, а действие в {account_id}")
        if policy_version < self.policy_version:
            raise ContentError(
                f"версия политики понизилась ({policy_version} < {self.policy_version}) — "
                f"одобрение больше не действительно")


__all__ = ["Approval", "ContentError", "ContentRevision", "canonical_json", "content_hash"]
