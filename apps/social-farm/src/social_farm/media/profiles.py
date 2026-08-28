"""Профили провайдера: ограничения медиа и правила рендера. Данные, не код.

`58_MEDIA_RULE_ENGINE` начинается с требования «Do not scatter constants across
code», и всё устройство этого файла из него следует: сами числа лежат в
версионированном JSON рядом, здесь — только их разбор и смысл.

## Главное различие: «нет ограничения» и «мы не знаем»

Это ось, вокруг которой вращается весь движок правил. Спека требует: «If
provider rule is unknown: do not invent a value». Значит незнание обязано быть
представимым — иначе его нечем отличить от свободы.

* **ключ отсутствует** → `UNKNOWN`. Правило не проверено. Проверка, которой
  оно нужно, даёт `FAIL_PROVIDER_RULE_UNKNOWN`, и автопубликация блокируется
  (решение G16).
* **ключ равен `null`** → ограничения нет, и это проверено. Заявление, а не
  умолчание.

Направление выбрано так, что забывчивость безопасна: автор профиля, забывший
поле, получает блокировку, а не разрешение. Обратный порядок («забыл — значит
можно») превратил бы каждый пробел в тихое разрешение публиковать.

Схема `media_profile.schema.json` этого различия выразить не может: там поля
просто `[integer, null]`. Схема остаётся контрактом API, а внутри у нас на
одно состояние больше — тот же приём, что и в решении C3.

## О `verified_at`

Живого приложения Meta в этой среде нет (блокер B1 аудита), поэтому профили
сверены с публичной документацией провайдера, а не с работающим API. Это
записано в самом профиле полем `verification`, а не спрятано: профиль,
объявляющий себя проверенным по документации, честнее профиля, молчащего о
происхождении своих чисел.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROFILE_DIR = Path(__file__).resolve().parent / "profiles"


class ProfileError(ValueError):
    """Профиль не разбирается или противоречив."""


class _Unknown:
    """Единственный экземпляр: «правило провайдера нам неизвестно».

    Ложно в булевом контексте, чтобы `if profile.max_bytes:` не принимало
    незнание за ноль, и заметно в логах.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNKNOWN"

    def __bool__(self) -> bool:
        return False


UNKNOWN = _Unknown()

#: Значение поля: конкретное, `None` (ограничения нет) либо `UNKNOWN`.
Rule = Any


def _rule(raw: dict[str, Any], key: str) -> Rule:
    """Отсутствует → UNKNOWN; явный null → None; иначе значение."""
    return raw[key] if key in raw else UNKNOWN


@dataclass(frozen=True, slots=True)
class ProviderMediaProfile:
    """Ограничения провайдера на один тип контента (`58_MEDIA_RULE_ENGINE`)."""

    provider: str
    content_type: str
    profile_version: int = 1
    api_version: str | None = None
    adapter_version: str = ""
    account_types: tuple[str, ...] = ()
    mime_allowlist: Rule = UNKNOWN
    container_allowlist: Rule = UNKNOWN
    codec_allowlist: Rule = UNKNOWN
    audio_codec_allowlist: Rule = UNKNOWN
    max_bytes: Rule = UNKNOWN
    min_width: Rule = UNKNOWN
    max_width: Rule = UNKNOWN
    min_height: Rule = UNKNOWN
    max_height: Rule = UNKNOWN
    duration_min_s: Rule = UNKNOWN
    duration_max_s: Rule = UNKNOWN
    aspect_rules: Rule = UNKNOWN
    # Что движку разрешено чинить перекодированием, а что — отказ.
    allow_transcode: bool = False
    allow_downscale: bool = False
    allow_aspect_pad: bool = False
    source_ref: str | None = None
    verified_at: str = ""
    verification: str = "UNVERIFIED"
    notes: str = ""

    @property
    def ref(self) -> str:
        """Ссылка на профиль, которая идёт в отчёт валидации и в ассет."""
        return f"{self.provider}:{self.content_type}:v{self.profile_version}"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ProviderMediaProfile":
        for required in ("provider", "content_type", "verified_at"):
            if not raw.get(required):
                raise ProfileError(f"в профиле медиа нет обязательного поля {required}")
        return cls(
            provider=str(raw["provider"]), content_type=str(raw["content_type"]),
            profile_version=int(raw.get("profile_version") or 1),
            api_version=raw.get("api_version"),
            adapter_version=str(raw.get("adapter_version") or ""),
            account_types=tuple(raw.get("account_types") or ()),
            mime_allowlist=_rule(raw, "mime_allowlist"),
            container_allowlist=_rule(raw, "container_allowlist"),
            codec_allowlist=_rule(raw, "codec_allowlist"),
            audio_codec_allowlist=_rule(raw, "audio_codec_allowlist"),
            max_bytes=_rule(raw, "max_bytes"),
            min_width=_rule(raw, "min_width"), max_width=_rule(raw, "max_width"),
            min_height=_rule(raw, "min_height"), max_height=_rule(raw, "max_height"),
            duration_min_s=_rule(raw, "duration_min_s"),
            duration_max_s=_rule(raw, "duration_max_s"),
            aspect_rules=_rule(raw, "aspect_rules"),
            allow_transcode=bool(raw.get("allow_transcode", False)),
            allow_downscale=bool(raw.get("allow_downscale", False)),
            allow_aspect_pad=bool(raw.get("allow_aspect_pad", False)),
            source_ref=raw.get("source_ref"), verified_at=str(raw["verified_at"]),
            verification=str(raw.get("verification") or "UNVERIFIED"),
            notes=str(raw.get("notes") or ""))

    def unknown_rules(self) -> tuple[str, ...]:
        """Какие правила профиля не проверены. Прямо в отчёт валидации."""
        names = ("mime_allowlist", "container_allowlist", "codec_allowlist",
                 "audio_codec_allowlist", "max_bytes", "min_width", "max_width",
                 "min_height", "max_height", "duration_min_s", "duration_max_s",
                 "aspect_rules")
        return tuple(n for n in names if getattr(self, n) is UNKNOWN)

    def to_schema_dict(self) -> dict[str, Any]:
        """Проекция на `media_profile.schema.json`.

        Схема не различает `UNKNOWN` и `null`, поэтому непроверенные правила в
        неё просто не попадают: отсутствие ключа — единственная форма, которой
        схема позволяет сказать «здесь ничего не утверждается».
        """
        out: dict[str, Any] = {"provider": self.provider,
                               "content_type": self.content_type,
                               "verified_at": self.verified_at}
        if self.api_version is not None:
            out["api_version"] = self.api_version
        if self.account_types:
            out["account_types"] = list(self.account_types)
        for name in ("mime_allowlist", "container_allowlist", "codec_allowlist",
                     "max_bytes", "min_width", "max_width", "min_height",
                     "max_height", "duration_min_s", "duration_max_s",
                     "aspect_rules"):
            value = getattr(self, name)
            if value is not UNKNOWN:
                out[name] = list(value) if isinstance(value, tuple) else value
        if self.source_ref is not None:
            out["source_ref"] = self.source_ref
        return out


@dataclass(frozen=True, slots=True)
class RenderProfile:
    """Как готовить контент под конкретную цель (`66_CONTENT_RENDER_PROFILES`).

    Профиль рендера — это цель («сделай 1080×1920 h264»), а профиль медиа —
    приёмка («что провайдер вообще примет»). Их два, потому что они меняются
    по разным причинам: цель мы выбираем сами, приёмку меняет провайдер.
    """

    provider: str
    content_type: str
    profile_version: int = 1
    account_type: str | None = None
    locale: str | None = None
    max_caption_length: Rule = UNKNOWN
    media_profile_ref: str = ""
    target_width: int | None = None
    target_height: int | None = None
    target_video_codec: str | None = None
    target_audio_codec: str | None = None
    target_container: str | None = None
    cover_rule: str = ""
    subtitle_rule: str = ""
    hashtag_mapping: dict[str, Any] = field(default_factory=dict)
    provider_version: str | None = None
    verified_at: str = ""

    @property
    def ref(self) -> str:
        return f"{self.provider}:{self.content_type}:render:v{self.profile_version}"

    @property
    def target_aspect(self) -> float | None:
        if not self.target_width or not self.target_height:
            return None
        return self.target_width / self.target_height

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RenderProfile":
        return cls(
            provider=str(raw["provider"]), content_type=str(raw["content_type"]),
            profile_version=int(raw.get("profile_version") or 1),
            account_type=raw.get("account_type"), locale=raw.get("locale"),
            max_caption_length=_rule(raw, "max_caption_length"),
            media_profile_ref=str(raw.get("media_profile_ref") or ""),
            target_width=raw.get("target_width"), target_height=raw.get("target_height"),
            target_video_codec=raw.get("target_video_codec"),
            target_audio_codec=raw.get("target_audio_codec"),
            target_container=raw.get("target_container"),
            cover_rule=str(raw.get("cover_rule") or ""),
            subtitle_rule=str(raw.get("subtitle_rule") or ""),
            hashtag_mapping=dict(raw.get("hashtag_mapping") or {}),
            provider_version=raw.get("provider_version"),
            verified_at=str(raw.get("verified_at") or ""))


@dataclass(frozen=True, slots=True)
class ProfileBundle:
    """Один версионированный файл профилей одного провайдера."""

    provider: str
    version: int
    media: dict[str, ProviderMediaProfile]
    render: dict[str, RenderProfile]

    def media_profile(self, content_type: str) -> ProviderMediaProfile:
        """Нет профиля на этот тип — это не «разрешено», это отказ.

        Опубликовать контент, для которого мы не знаем НИ ОДНОГО ограничения
        провайдера, — ровно то, что запрещает `58_MEDIA_RULE_ENGINE`.
        """
        profile = self.media.get(content_type.upper())
        if profile is None:
            raise ProfileError(
                f"для {self.provider}/{content_type} нет профиля медиа: правила "
                f"провайдера неизвестны целиком, публиковать вслепую нельзя")
        return profile

    def render_profile(self, content_type: str) -> RenderProfile:
        profile = self.render.get(content_type.upper())
        if profile is None:
            raise ProfileError(
                f"для {self.provider}/{content_type} нет профиля рендера")
        return profile


def load_bundle(path: str | Path) -> ProfileBundle:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    provider = str(raw.get("provider") or "")
    if not provider:
        raise ProfileError(f"в файле профилей {path} не указан провайдер")
    version = int(raw.get("profile_version") or 1)
    shared = {"provider": provider, "profile_version": version,
              "adapter_version": raw.get("adapter_version") or "",
              "api_version": raw.get("api_version"),
              "verified_at": raw.get("verified_at") or "",
              "verification": raw.get("verification") or "UNVERIFIED",
              "source_ref": raw.get("source_ref")}
    media: dict[str, ProviderMediaProfile] = {}
    for content_type, entry in (raw.get("media_profiles") or {}).items():
        merged = {**shared, **entry, "content_type": content_type.upper()}
        media[content_type.upper()] = ProviderMediaProfile.from_dict(merged)
    render: dict[str, RenderProfile] = {}
    for content_type, entry in (raw.get("render_profiles") or {}).items():
        merged = {"provider": provider, "profile_version": version,
                  "provider_version": raw.get("api_version"),
                  "verified_at": raw.get("verified_at") or "",
                  **entry, "content_type": content_type.upper()}
        render[content_type.upper()] = RenderProfile.from_dict(merged)
    return ProfileBundle(provider=provider, version=version, media=media, render=render)


def load_provider(provider: str, *, directory: str | Path | None = None) -> ProfileBundle:
    """Загрузить профили провайдера по имени.

    Берётся файл с наибольшей версией: профили версионируются, а работает
    последний. Старые остаются на диске, потому что ими объясняются прошлые
    публикации — ревизия, одобренная год назад, проверялась не этими числами.
    """
    root = Path(directory) if directory else PROFILE_DIR
    found = sorted(root.glob(f"{provider}.v*.json"))
    if not found:
        raise ProfileError(
            f"профилей провайдера {provider} нет в {root}: правила его медиа "
            f"неизвестны, автоматическая публикация невозможна")
    return load_bundle(found[-1])


__all__ = ["PROFILE_DIR", "ProfileBundle", "ProfileError", "ProviderMediaProfile",
           "RenderProfile", "Rule", "UNKNOWN", "load_bundle", "load_provider"]
