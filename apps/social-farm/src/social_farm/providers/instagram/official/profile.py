"""Профиль провайдера: факты версии, вынесенные из кода в данные.

Спека говорит это прямо (`41_EXTERNAL_API_FACTS_2026_08`): точные эндпоинты,
скоупы и лимиты — факты, которые берутся из актуальной документации Meta во
время внедрения, а не из спецификации. Значит, им не место в исходниках
адаптера: код, в который вписан путь `/{version}/{id}/media`, устаревает молча,
а данные с явной пометкой «не сверено» устаревают заметно.

## Почему в поставляемом профиле пусто

Приложения Meta нет, скоупов нет, Instagram Professional аккаунта нет. Значит,
проверить ни один путь, ни один скоуп и ни один лимит невозможно. Поставляемый
профиль — **шаблон**: структура заполнена, значения `null`, статус сверки
`UNVERIFIED`.

Правдоподобное выдуманное число здесь было бы хуже отсутствующего. Пустое поле
останавливает работу и требует сверки; правдоподобное — проходит ревью,
доезжает до боя и обнаруживается тогда, когда публикация уже отклонена
провайдером или, хуже, лимит превышен.

Отсюда поведение адаптера на несверенном профиле: **все возможности
`TEMPORARILY_DISABLED` с причиной, ни одного выполнимого действия**. Это не
поломка — это честный ответ «профиль не сверен с документацией Meta».

## Что даёт отпечаток профиля

Любое изменение профиля — новая версия Graph API, другой скоуп, заполненный
лимит — меняет отпечаток, а отпечаток входит в проверку снимка возможностей.
Смена версии Graph API поэтому не может «тихо продолжиться на старых
возможностях»: снимок с чужим отпечатком считается просроченным.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from ....domain.content import canonical_json

PROFILE_DIR = Path(__file__).resolve().parent / "profiles"
TEMPLATE_PROFILE = PROFILE_DIR / "graph.template.json"


class ProfileError(ValueError):
    """Профиль не читается или противоречив."""


class ProfileIncomplete(ProfileError):
    """В профиле нет значения, без которого действие невозможно.

    Отдельный класс, а не общий `ProfileError`: «мы этого не знаем» и «профиль
    сломан» приводят к разным ответам владельцу.
    """


class VerificationStatus(str, Enum):
    """Сверен ли профиль с документацией провайдера.

    `FIXTURE` — третье состояние, и оно обязательно. Без него профиль фикстуры
    пришлось бы объявлять сверенным, чтобы тесты работали, и тогда пометка
    «сверено» перестала бы что-либо значить.
    """

    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"
    FIXTURE = "FIXTURE"

    @property
    def allows_live_calls(self) -> bool:
        """Живой вызов провайдера разрешён только со сверенного профиля."""
        return self is VerificationStatus.VERIFIED


@dataclass(frozen=True, slots=True)
class EndpointSpec:
    """Одна операция у провайдера.

    `path is None` означает «не сверено», а не «нет такой операции». Разница
    видна вызывающему: первое лечится сверкой с документацией, второе — ничем.
    """

    operation: str
    method: str = "GET"
    path: str | None = None
    required_permissions: tuple[str, ...] | None = None
    mutating: bool = False
    notes: str = ""

    @property
    def resolved(self) -> bool:
        return bool(self.path)

    def require_path(self) -> str:
        if not self.path:
            raise ProfileIncomplete(
                f"путь операции {self.operation} не заполнен в профиле провайдера; "
                f"сверьте с документацией Meta и заполните профиль — "
                f"догадка здесь опаснее остановки")
        return self.path


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    """Как возможность связана с операциями и разрешениями провайдера.

    `required_permissions is None` — «не сверено». Пустой кортеж — «разрешений
    не требуется». Их нельзя путать: из первого следует «не знаем, можно ли»,
    из второго — «можно».
    """

    name: str
    operations: tuple[str, ...] = ()
    required_permissions: tuple[str, ...] | None = None
    app_review: bool | None = None
    professional_account_required: bool = True
    officially_exposed: bool | None = None
    notes: str = ""


@dataclass(frozen=True, slots=True)
class MediaRules:
    """Ограничения провайдера по медиа.

    `known is False` — единственное честное состояние без документации Meta.
    Из него следует `G16`: автоматическая публикация блокируется, работа уходит
    на одобрение человека. Значение не выдумывается.
    """

    known: bool = False
    source: str = "REQUIRES_META_DOCS"
    limits: dict[str, Any] = field(default_factory=dict)

    def limit(self, path: str) -> Any:
        node: Any = self.limits
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node


@dataclass(frozen=True, slots=True)
class RateLimitSpec:
    """Откуда читать лимиты. Имена заголовков провайдера — тоже факт версии.

    `retry_after_header` заполнен и в шаблоне: `Retry-After` — заголовок из
    RFC 9110, а не факт Meta. Всё остальное пусто до сверки.
    """

    retry_after_header: str = "Retry-After"
    usage_headers: tuple[str, ...] = ()
    documented_limits: dict[str, Any] = field(default_factory=dict)
    bucket_strategy: str = "provider:account:capability"


@dataclass(frozen=True, slots=True)
class ProviderProfile:
    """Версионированный профиль. Данные, не код."""

    profile_id: str
    provider: str
    adapter_version: str
    verification: VerificationStatus
    provider_api_version: str | None = None
    base_url: str | None = None
    verified_at: str | None = None
    verified_by: str | None = None
    warning: str = ""
    endpoints: dict[str, EndpointSpec] = field(default_factory=dict)
    capabilities: dict[str, CapabilitySpec] = field(default_factory=dict)
    media_rules: MediaRules = field(default_factory=MediaRules)
    rate_limit: RateLimitSpec = field(default_factory=RateLimitSpec)
    error_codes: dict[str, str] = field(default_factory=dict)
    idempotency_header: str | None = None
    webhook_fields: tuple[str, ...] | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    # -- сверка -----------------------------------------------------------
    @property
    def verified(self) -> bool:
        return self.verification is VerificationStatus.VERIFIED

    @property
    def allows_live_calls(self) -> bool:
        return self.verification.allows_live_calls

    def unverified_reason(self) -> str:
        if self.verified:
            return ""
        if self.verification is VerificationStatus.FIXTURE:
            return ("профиль провайдера — фикстура: значения синтетические и "
                    "к настоящему Instagram отношения не имеют")
        return ("профиль провайдера не сверен с документацией Meta: "
                "эндпоинты, разрешения и лимиты в нём не заполнены. "
                "Заполните профиль и отметьте сверку — до этого действия "
                "не предлагаются")

    # -- отпечаток --------------------------------------------------------
    @property
    def fingerprint(self) -> str:
        """sha256 от канонизированного профиля.

        Входит в проверку снимка возможностей: изменённый профиль — это другие
        возможности, даже если версия Graph API осталась той же.
        """
        return hashlib.sha256(
            canonical_json(self.raw).encode("utf-8")).hexdigest()[:16]

    @property
    def version_stamp(self) -> str:
        """Всё, чем возможность привязана к версии, одной строкой."""
        return (f"{self.provider}/{self.adapter_version}/"
                f"{self.provider_api_version or 'unknown'}/{self.fingerprint}")

    # -- доступ -----------------------------------------------------------
    def endpoint(self, operation: str) -> EndpointSpec | None:
        return self.endpoints.get(operation)

    def require_endpoint(self, operation: str) -> EndpointSpec:
        spec = self.endpoints.get(operation)
        if spec is None:
            raise ProfileIncomplete(
                f"операция {operation} не объявлена в профиле провайдера")
        return spec

    def capability(self, name: str) -> CapabilitySpec | None:
        return self.capabilities.get(name)

    def capability_names(self) -> list[str]:
        return sorted(self.capabilities)

    def known_permissions(self) -> set[str]:
        out: set[str] = set()
        for spec in self.capabilities.values():
            out.update(spec.required_permissions or ())
        return out

    # -- загрузка ---------------------------------------------------------
    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ProviderProfile":
        try:
            status = VerificationStatus(
                str((raw.get("verification") or {}).get("status", "UNVERIFIED")))
        except ValueError as exc:
            raise ProfileError(
                f"неизвестный статус сверки профиля: "
                f"{(raw.get('verification') or {}).get('status')!r}") from exc

        endpoints = {}
        for name, body in (raw.get("operations") or {}).items():
            body = dict(body or {})
            perms = body.get("required_permissions")
            endpoints[name] = EndpointSpec(
                operation=name, method=str(body.get("method") or "GET").upper(),
                path=body.get("path"),
                required_permissions=tuple(perms) if perms is not None else None,
                mutating=bool(body.get("mutating")),
                notes=str(body.get("notes") or ""))

        capabilities = {}
        for name, body in (raw.get("capabilities") or {}).items():
            body = dict(body or {})
            perms = body.get("required_permissions")
            capabilities[name] = CapabilitySpec(
                name=name, operations=tuple(body.get("operations") or ()),
                required_permissions=tuple(perms) if perms is not None else None,
                app_review=body.get("app_review"),
                professional_account_required=bool(
                    body.get("professional_account_required", True)),
                officially_exposed=body.get("officially_exposed"),
                notes=str(body.get("notes") or ""))

        media = dict(raw.get("media_rules") or {})
        limits = dict(raw.get("rate_limit") or {})
        verification = dict(raw.get("verification") or {})
        return cls(
            profile_id=str(raw.get("profile_id") or "unnamed"),
            provider=str(raw.get("provider") or "instagram"),
            adapter_version=str(raw.get("adapter_version") or "0.0.0"),
            verification=status,
            provider_api_version=raw.get("provider_api_version"),
            base_url=raw.get("base_url"),
            verified_at=verification.get("checked_against_provider_docs_at"),
            verified_by=verification.get("checked_by"),
            warning=str(verification.get("warning") or ""),
            endpoints=endpoints, capabilities=capabilities,
            media_rules=MediaRules(
                known=bool(media.get("known")),
                source=str(media.get("source") or "REQUIRES_META_DOCS"),
                limits=dict(media.get("limits") or {})),
            rate_limit=RateLimitSpec(
                retry_after_header=str(limits.get("retry_after_header")
                                       or "Retry-After"),
                usage_headers=tuple(limits.get("usage_headers") or ()),
                documented_limits=dict(limits.get("documented_limits") or {}),
                bucket_strategy=str(limits.get("bucket_strategy")
                                    or "provider:account:capability")),
            error_codes={str(k): str(v)
                         for k, v in (raw.get("error_codes") or {}).items()},
            idempotency_header=(raw.get("idempotency") or {}).get("header"),
            webhook_fields=(tuple((raw.get("webhooks") or {}).get("fields"))
                            if (raw.get("webhooks") or {}).get("fields") is not None
                            else None),
            raw=dict(raw))

    @classmethod
    def load(cls, path: Path | str) -> "ProviderProfile":
        source = Path(path)
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ProfileError(f"профиль провайдера {source} не читается: {exc}") from exc
        return cls.from_dict(raw)

    @classmethod
    def shipped(cls) -> "ProviderProfile":
        """Профиль, который едет в поставке. Он намеренно не сверен."""
        return cls.load(TEMPLATE_PROFILE)


__all__ = ["CapabilitySpec", "EndpointSpec", "MediaRules", "PROFILE_DIR",
           "ProfileError", "ProfileIncomplete", "ProviderProfile", "RateLimitSpec",
           "TEMPLATE_PROFILE", "VerificationStatus"]
