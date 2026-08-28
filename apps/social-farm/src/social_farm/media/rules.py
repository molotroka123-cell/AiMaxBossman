"""Движок правил медиа: девять исходов из `58_MEDIA_RULE_ENGINE`.

Движок отвечает ровно на один вопрос: **примет ли провайдер этот файл, и если
нет, можно ли это починить, ничего не соврав об авторском замысле.**

## Что чинится преобразованием, а что нет

Граница проведена по смыслу, а не по технической возможности. ffmpeg умеет
обрезать видео и растягивать кадр; движок этого не предлагает.

* **чинится** — формат, кодек, контейнер, слишком большой кадр (уменьшение),
  несовпадение соотношения ПОЛЯМИ. Здесь меняется носитель, а не содержание.
* **не чинится** — длительность (обрезка выкидывает то, что автор снял),
  кадрирование под соотношение (обрезка меняет кадр), кадр меньше минимума
  (увеличение дорисовывает пиксели, которых не было), размер файла.

Последнее стоит пояснить: перекодирование МОГЛО БЫ уложить файл в лимит, но
пообещать это нельзя, не зная целевого качества. Обещание преобразования,
которое потом не сработает, хуже честного отказа сейчас — работа успеет уйти в
расписание и упадёт в момент публикации.

## Порядок исходов

Из нескольких нарушений докладывается самое определённое:

```
FAIL_CORRUPT > FAIL_UNSUPPORTED > FAIL_TOO_LARGE > FAIL_DURATION
            > FAIL_CODEC > FAIL_ASPECT > FAIL_PROVIDER_RULE_UNKNOWN
            > PASS_WITH_TRANSFORM > PASS
```

`FAIL_PROVIDER_RULE_UNKNOWN` стоит НИЖЕ конкретных отказов намеренно. Если
файл заведомо велик, надо так и сказать — это действие, которое владелец может
выполнить. «Правила провайдера неизвестны» полезно только тогда, когда всё
проверяемое уже проверено и прошло: тогда это единственное, что осталось.
Выше `PASS_WITH_TRANSFORM` — потому что незнание не лечится перекодированием.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from ..domain.errors import ProviderError
from .asset import AssetType, MediaAsset
from .probe import CorruptMedia, ProbeResult, ProbeUnavailable, probe
from .profiles import UNKNOWN, ProviderMediaProfile

#: Допуск при сравнении соотношений сторон. Спека числа не даёт; 1% относительной
#: разницы берётся потому, что 1080×1350 и 1078×1348 — это один и тот же кадр
#: после округления при масштабировании, и считать их разными соотношениями
#: значит отклонять корректный рендер собственного конвейера.
ASPECT_TOLERANCE = 0.01


class ValidationOutcome(str, Enum):
    """Перечень закрыт `58_MEDIA_RULE_ENGINE`. Ровно девять."""

    PASS = "PASS"
    PASS_WITH_TRANSFORM = "PASS_WITH_TRANSFORM"
    FAIL_UNSUPPORTED = "FAIL_UNSUPPORTED"
    FAIL_CORRUPT = "FAIL_CORRUPT"
    FAIL_TOO_LARGE = "FAIL_TOO_LARGE"
    FAIL_DURATION = "FAIL_DURATION"
    FAIL_CODEC = "FAIL_CODEC"
    FAIL_ASPECT = "FAIL_ASPECT"
    FAIL_PROVIDER_RULE_UNKNOWN = "FAIL_PROVIDER_RULE_UNKNOWN"


_SEVERITY = {
    ValidationOutcome.FAIL_CORRUPT: 90,
    ValidationOutcome.FAIL_UNSUPPORTED: 80,
    ValidationOutcome.FAIL_TOO_LARGE: 70,
    ValidationOutcome.FAIL_DURATION: 60,
    ValidationOutcome.FAIL_CODEC: 50,
    ValidationOutcome.FAIL_ASPECT: 40,
    ValidationOutcome.FAIL_PROVIDER_RULE_UNKNOWN: 30,
    ValidationOutcome.PASS_WITH_TRANSFORM: 20,
    ValidationOutcome.PASS: 10,
}

_HARD_FAILURES = frozenset(o for o in ValidationOutcome
                           if o.value.startswith("FAIL_")
                           and o is not ValidationOutcome.FAIL_PROVIDER_RULE_UNKNOWN)


class UnprobedAsset(RuntimeError):
    """Валидировать неизмеренный ассет нечем.

    Не исход валидации: это отсутствие данных, а не вердикт о файле.
    Отображается на `NOT_SUPPORTED`, как и отсутствие ffprobe.
    """


@dataclass(frozen=True, slots=True)
class MediaFacts:
    """Измеренные свойства файла — то, с чем сверяется профиль."""

    type: AssetType
    mime: str
    container: str | None = None
    codec: str | None = None
    audio_codec: str | None = None
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    bytes: int = 0
    prober: str = ""

    @property
    def aspect(self) -> float | None:
        if not self.width or not self.height:
            return None
        return self.width / self.height

    @classmethod
    def from_probe(cls, result: ProbeResult) -> "MediaFacts":
        return cls(type=result.type, mime=result.mime, container=result.container,
                   codec=result.codec, audio_codec=result.audio_codec,
                   width=result.width, height=result.height,
                   duration_ms=result.duration_ms, bytes=result.bytes,
                   prober=result.prober)

    @classmethod
    def from_asset(cls, asset: MediaAsset) -> "MediaFacts":
        if not asset.probed:
            raise UnprobedAsset(
                f"ассет {asset.id} не измерен ничем: валидировать его нечем. "
                f"NOT_SUPPORTED — неизмеренный файл не публикуется")
        return cls(type=asset.type, mime=asset.mime, container=asset.container,
                   codec=asset.codec, audio_codec=asset.audio_codec,
                   width=asset.width, height=asset.height,
                   duration_ms=asset.duration_ms, bytes=asset.bytes,
                   prober=asset.prober)


@dataclass(frozen=True, slots=True)
class MediaValidation:
    """Вердикт по одному ассету против одного профиля."""

    outcome: ValidationOutcome
    profile_ref: str
    reasons: tuple[str, ...] = ()
    transforms: tuple[str, ...] = ()
    unknown_rules: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        """Годится ли к публикации — возможно, после преобразования."""
        return self.outcome in (ValidationOutcome.PASS,
                                ValidationOutcome.PASS_WITH_TRANSFORM)

    @property
    def needs_transform(self) -> bool:
        return self.outcome is ValidationOutcome.PASS_WITH_TRANSFORM

    @property
    def blocks_auto_publish(self) -> bool:
        """Всё, кроме чистого `PASS` и починимого, запрещает автопубликацию.

        `FAIL_PROVIDER_RULE_UNKNOWN` попадает сюда по решению G16: правило не
        выдумывается, работа уходит человеку.
        """
        return not self.passed

    def to_error(self) -> ProviderError | None:
        """Отображение на закрытый перечень ошибок через `ALIASES`.

        Все девять `FAIL_*` уже объявлены в `domain/errors.py` как
        `MEDIA_INVALID` с исходным кодом в `safe_detail`. Перечень ошибок не
        расширяется — он контракт.
        """
        if self.passed:
            return None
        detail = f"{self.outcome.value}: {'; '.join(self.reasons)}" if self.reasons \
            else self.outcome.value
        return ProviderError.of(self.outcome.value, safe_detail=detail[:500],
                                user_action=self._user_action())

    def _user_action(self) -> str:
        if self.outcome is ValidationOutcome.FAIL_PROVIDER_RULE_UNKNOWN:
            return ("Правила провайдера для этого типа контента не проверены "
                    f"({', '.join(self.unknown_rules)}). Публикация возможна только "
                    "после подтверждения человеком.")
        return "Замените файл или исправьте его перед публикацией."


# ------------------------------------------------------------- соотношения

_RATIO = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\s*$")


def parse_ratio(text: str) -> float:
    match = _RATIO.match(str(text))
    if not match:
        raise ValueError(f"нечитаемое соотношение сторон: {text!r}")
    width, height = float(match.group(1)), float(match.group(2))
    if height <= 0:
        raise ValueError(f"нулевая высота в соотношении {text!r}")
    return width / height


def aspect_matches(aspect: float, rules: tuple[str, ...] | list[str]) -> bool:
    """Правило — либо точное `W:H`, либо диапазон `W:H..W:H`."""
    for rule in rules:
        text = str(rule)
        if ".." in text:
            low_text, high_text = text.split("..", 1)
            low, high = parse_ratio(low_text), parse_ratio(high_text)
            if min(low, high) * (1 - ASPECT_TOLERANCE) <= aspect \
                    <= max(low, high) * (1 + ASPECT_TOLERANCE):
                return True
        else:
            target = parse_ratio(text)
            if abs(aspect - target) <= target * ASPECT_TOLERANCE:
                return True
    return False


# ------------------------------------------------------------- сама проверка

def _relevant_rules(kind: AssetType) -> tuple[str, ...]:
    """Какие правила профиля нужны для файла этого типа.

    Длительность у изображения не спрашивается: неизвестное правило,
    неприменимое к файлу, ничего не блокирует. Блокирует только незнание того,
    что нам действительно нужно было проверить.
    """
    common = ("mime_allowlist", "container_allowlist", "max_bytes")
    if kind is AssetType.IMAGE:
        return common + ("codec_allowlist", "min_width", "max_width", "min_height",
                         "max_height", "aspect_rules")
    if kind is AssetType.VIDEO:
        return common + ("codec_allowlist", "audio_codec_allowlist", "min_width",
                         "max_width", "min_height", "max_height", "aspect_rules",
                         "duration_min_s", "duration_max_s")
    if kind is AssetType.AUDIO:
        return common + ("audio_codec_allowlist", "duration_min_s", "duration_max_s")
    return common


def _in_allowlist(value: str | None, allowed: object) -> bool:
    if not value:
        return False
    lowered = str(value).lower()
    items = [str(a).lower() for a in (allowed or ())]
    if lowered in items:
        return True
    # Контейнер ffprobe часто перечисляет списком: "mov,mp4,m4a,3gp,3g2,mj2".
    return any(part in items for part in lowered.split(","))


def validate(facts: MediaFacts, profile: ProviderMediaProfile) -> MediaValidation:
    """Сверить измерения с профилем и вернуть один из девяти исходов."""
    verdicts: list[ValidationOutcome] = []
    reasons: list[str] = []
    transforms: list[str] = []

    def fail(outcome: ValidationOutcome, reason: str) -> None:
        verdicts.append(outcome)
        reasons.append(reason)

    def fixable(outcome: ValidationOutcome, reason: str, transform: str,
                allowed: bool) -> None:
        """Нарушение, которое преобразование чинит, — если профиль это разрешает."""
        if allowed:
            verdicts.append(ValidationOutcome.PASS_WITH_TRANSFORM)
            transforms.append(transform)
        else:
            verdicts.append(outcome)
        reasons.append(reason)

    unknown = tuple(name for name in _relevant_rules(facts.type)
                    if getattr(profile, name) is UNKNOWN)

    # --- формат: mime и контейнер
    if profile.mime_allowlist is not UNKNOWN and profile.mime_allowlist is not None:
        if not _in_allowlist(facts.mime, profile.mime_allowlist):
            fixable(ValidationOutcome.FAIL_UNSUPPORTED,
                    f"MIME {facts.mime} не принимается: разрешены "
                    f"{list(profile.mime_allowlist)}",
                    f"перекодировать в {list(profile.mime_allowlist)[0]}",
                    bool(profile.allow_transcode))
    if profile.container_allowlist is not UNKNOWN \
            and profile.container_allowlist is not None and facts.container:
        if not _in_allowlist(facts.container, profile.container_allowlist):
            fixable(ValidationOutcome.FAIL_UNSUPPORTED,
                    f"контейнер {facts.container} не принимается: разрешены "
                    f"{list(profile.container_allowlist)}",
                    f"переупаковать в {list(profile.container_allowlist)[0]}",
                    bool(profile.allow_transcode))

    # --- размер файла. Хард-отказ: см. докстроку модуля.
    if profile.max_bytes is not UNKNOWN and profile.max_bytes is not None:
        if facts.bytes > int(profile.max_bytes):
            fail(ValidationOutcome.FAIL_TOO_LARGE,
                 f"{facts.bytes} байт при лимите {int(profile.max_bytes)}")

    # --- длительность. Обрезать нельзя: это правка замысла, а не носителя.
    if facts.duration_ms is not None:
        seconds = facts.duration_ms / 1000.0
        if profile.duration_min_s is not UNKNOWN and profile.duration_min_s is not None \
                and seconds < float(profile.duration_min_s):
            fail(ValidationOutcome.FAIL_DURATION,
                 f"длительность {seconds:.2f} с меньше минимума "
                 f"{profile.duration_min_s} с")
        if profile.duration_max_s is not UNKNOWN and profile.duration_max_s is not None \
                and seconds > float(profile.duration_max_s):
            fail(ValidationOutcome.FAIL_DURATION,
                 f"длительность {seconds:.2f} с больше максимума "
                 f"{profile.duration_max_s} с")

    # --- кодеки
    if profile.codec_allowlist is not UNKNOWN and profile.codec_allowlist is not None \
            and facts.codec:
        if not _in_allowlist(facts.codec, profile.codec_allowlist):
            fixable(ValidationOutcome.FAIL_CODEC,
                    f"кодек {facts.codec} не принимается: разрешены "
                    f"{list(profile.codec_allowlist)}",
                    f"перекодировать в {list(profile.codec_allowlist)[0]}",
                    bool(profile.allow_transcode))
    if profile.audio_codec_allowlist is not UNKNOWN \
            and profile.audio_codec_allowlist is not None and facts.audio_codec:
        if not _in_allowlist(facts.audio_codec, profile.audio_codec_allowlist):
            fixable(ValidationOutcome.FAIL_CODEC,
                    f"аудиокодек {facts.audio_codec} не принимается: разрешены "
                    f"{list(profile.audio_codec_allowlist)}",
                    f"перекодировать звук в {list(profile.audio_codec_allowlist)[0]}",
                    bool(profile.allow_transcode))

    # --- размеры кадра
    for axis, value, low_name, high_name in (
            ("ширина", facts.width, "min_width", "max_width"),
            ("высота", facts.height, "min_height", "max_height")):
        if value is None:
            continue
        low, high = getattr(profile, low_name), getattr(profile, high_name)
        if low is not UNKNOWN and low is not None and value < int(low):
            # Увеличение дорисовывает пиксели, которых не было. Это не рендер.
            fail(ValidationOutcome.FAIL_UNSUPPORTED,
                 f"{axis} {value} меньше минимума {int(low)}; увеличивать кадр "
                 f"мы не будем — это дорисовка, а не преобразование")
        if high is not UNKNOWN and high is not None and value > int(high):
            fixable(ValidationOutcome.FAIL_UNSUPPORTED,
                    f"{axis} {value} больше максимума {int(high)}",
                    f"уменьшить кадр до {int(high)} по {axis}",
                    bool(profile.allow_downscale))

    # --- соотношение сторон
    if profile.aspect_rules is not UNKNOWN and profile.aspect_rules is not None \
            and facts.aspect is not None and profile.aspect_rules:
        if not aspect_matches(facts.aspect, list(profile.aspect_rules)):
            # Чинится ТОЛЬКО полями. Растягивание запрещено прямо
            # (14_MEDIA_TRANSFORM_PIPELINE), кадрирование меняет кадр.
            fixable(ValidationOutcome.FAIL_ASPECT,
                    f"соотношение {facts.aspect:.4f} не подходит под "
                    f"{list(profile.aspect_rules)}",
                    "добавить поля до допустимого соотношения (без растягивания)",
                    bool(profile.allow_aspect_pad))

    # --- незнание правил провайдера (G16)
    if unknown and not any(v in _HARD_FAILURES for v in verdicts):
        verdicts.append(ValidationOutcome.FAIL_PROVIDER_RULE_UNKNOWN)
        reasons.append(
            f"правила провайдера не проверены: {', '.join(unknown)}. "
            f"Значение не выдумывается — автоматическая публикация блокируется")

    outcome = max(verdicts or [ValidationOutcome.PASS], key=lambda v: _SEVERITY[v])
    if outcome is not ValidationOutcome.PASS_WITH_TRANSFORM:
        transforms = []
    return MediaValidation(outcome=outcome, profile_ref=profile.ref,
                           reasons=tuple(reasons), transforms=tuple(transforms),
                           unknown_rules=unknown)


def validate_stored_asset(store, asset: MediaAsset, profile: ProviderMediaProfile, *,
                          reprobe: bool = True) -> MediaValidation:
    """Проверить ассет, лежащий в хранилище, — с перепроверкой содержимого.

    Здесь встречаются все три барьера сразу: файл достаётся из хранилища со
    сверкой контрольной суммы (подменённый не пройдёт), измеряется настоящим
    прибором (`FAIL_CORRUPT` берётся отсюда) и сверяется с профилем.

    `ProbeUnavailable` наружу не перехватывается: отсутствие ffprobe — не
    вердикт о файле, и притворяться вердиктом оно не должно. Конвейер
    превращает его в честный `NOT_SUPPORTED`.
    """
    path = store.path_of(asset.storage_ref)          # сверка суммы внутри
    if not reprobe:
        return validate(MediaFacts.from_asset(asset), profile)
    try:
        facts = MediaFacts.from_probe(probe(path))
    except CorruptMedia as exc:
        return MediaValidation(outcome=ValidationOutcome.FAIL_CORRUPT,
                               profile_ref=profile.ref, reasons=(str(exc),))
    except ProbeUnavailable:
        raise
    return validate(facts, profile)


__all__ = ["ASPECT_TOLERANCE", "MediaFacts", "MediaValidation", "UnprobedAsset",
           "ValidationOutcome", "aspect_matches", "parse_ratio", "validate",
           "validate_stored_asset"]
