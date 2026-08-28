"""Фикстуры ответов провайдера: версионированные, синтетические, помеченные.

`71_PROVIDER_FIXTURE_STRATEGY` требует хранить рядом с фикстурой версию
провайдера, дату наблюдения, породивший тест и состояние редакции. Здесь всё
четыре поля есть, и одно из них заполнено неудобным значением: **`observed_at`
равен `null`, а `origin` — `SYNTHETIC`**.

Так и есть. Ни один из этих ответов не наблюдался у Meta: приложения Meta,
скоупов и Instagram Professional аккаунта не было. Фикстуры описывают ФОРМУ
ответа, чтобы прогнать логику адаптера, и не описывают содержимое ответов Meta.
Пометка стоит в каждом файле, чтобы никто не принял их за наблюдение — включая
нас самих через полгода.

Разборщики обязаны терпеть лишние поля (`71`): провайдер добавляет поля, не
спрашивая, и падение на неизвестном ключе означало бы, что любое расширение у
Meta ломает публикацию.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .transport import GraphResponse

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


class FixtureError(ValueError):
    """Фикстура не читается или не помечена."""


@dataclass(frozen=True, slots=True)
class FixtureRecord:
    """Фикстура вместе с её происхождением. Поля происхождения обязательны."""

    name: str
    provider: str
    provider_api_version: str
    origin: str
    observed_at: str | None
    redaction: str
    source_test: str
    warning: str
    status: int
    headers: dict[str, str]
    body: dict[str, Any]

    @property
    def synthetic(self) -> bool:
        return self.origin.upper() == "SYNTHETIC"

    def response(self) -> GraphResponse:
        return GraphResponse(status=self.status, headers=dict(self.headers),
                             body=dict(self.body),
                             request_id=self.headers.get("x-fixture-request-id"))


def _read(name: str) -> dict[str, Any]:
    path = FIXTURE_DIR / f"{name}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FixtureError(f"фикстура {name} не читается: {exc}") from exc


def load(name: str) -> FixtureRecord:
    raw = _read(name)
    head = dict(raw.get("fixture") or {})
    missing = [k for k in ("provider", "provider_api_version", "origin", "redaction",
                           "source_test") if not head.get(k)]
    if missing:
        raise FixtureError(
            f"фикстура {name} не помечена: нет полей {missing}. "
            f"Непомеченная фикстура через полгода неотличима от наблюдения.")
    return FixtureRecord(
        name=str(head.get("name") or name), provider=str(head["provider"]),
        provider_api_version=str(head["provider_api_version"]),
        origin=str(head["origin"]), observed_at=head.get("observed_at"),
        redaction=str(head["redaction"]), source_test=str(head["source_test"]),
        warning=str(head.get("warning") or ""),
        status=int(raw.get("status") or 200),
        headers={str(k): str(v) for k, v in (raw.get("headers") or {}).items()},
        body=dict(raw.get("body") or {}))


def response(name: str) -> GraphResponse:
    return load(name).response()


def catalogue() -> list[str]:
    return sorted(p.stem for p in FIXTURE_DIR.glob("*.json"))


# Какая фикстура играет за какую операцию по умолчанию. Это карта «счастливого
# пути»; отказы тест ставит в очередь явно, потому что молчаливый отказ по
# умолчанию — худший вид фикстуры.
DEFAULT_PACK: dict[str, str] = {
    "account_profile_read": "account_profile",
    "account_permissions_read": "account_permissions",
    "media_list": "media_list",
    "media_read": "media_read",
    "media_container_create": "publish_container_created",
    "media_container_status": "publish_status_finished",
    "media_publish": "publish_published",
    "comments_list": "comments_list",
    "comment_reply": "comment_reply_created",
    "comment_moderate": "comment_moderated",
    "mentions_list": "mentions_list",
    "conversations_list": "conversations_list",
    "messages_list": "messages_list",
    "message_send": "message_sent",
    "insights_account": "insights_account",
    "insights_media": "insights_media",
    "webhook_subscribe": "webhook_subscribed",
    "reconcile_media": "media_list",
    "media_delete": "media_deleted",
    "media_caption_edit": "media_caption_edited",
    "account_profile_update": "account_profile_updated",
}


def default_pack() -> dict[str, GraphResponse]:
    """Набор ответов на все объявленные операции — для тестов и `--dry-run`."""
    return {operation: response(name) for operation, name in DEFAULT_PACK.items()}


__all__ = ["DEFAULT_PACK", "FIXTURE_DIR", "FixtureError", "FixtureRecord",
           "catalogue", "default_pack", "load", "response"]
