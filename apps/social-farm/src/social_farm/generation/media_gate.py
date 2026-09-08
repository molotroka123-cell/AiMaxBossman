"""Проверка того, что провайдер действительно вернул медиа.

Самый частый «файл» на выходе браузерной загрузки — это не видео. Это страница
ошибки, отданная под именем `scene.mp4`, оборванная закачка или пустышка на
двести байт. Все три спокойно пройдут проверку «файл существует и не пуст», и
все три доедут до сборки ролика, где выяснится, что ролик собрать не из чего.

Поэтому здесь спрашивают не имя файла, а сам файл. Расширение результата
выводится из ИЗМЕРЕННОГО типа, а не из того, как назвал файл провайдер: HTML,
названный `.mp4`, обязан быть отвергнут именно потому, что назван `.mp4`.

Отдельно про «измерить нечем». Это не вердикт о файле — файл может быть
прекрасен. Но принять неизмеренное значит записать в свидетельство работы то,
чего никто не проверял, и `UNMEASURED` поэтому такой же отказ, как повреждение.
Разница видна владельцу: в одном случае надо переделать генерацию, в другом —
поставить ffmpeg.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ..media.asset import AssetType
from ..media.probe import CorruptMedia, ProbeResult, ProbeUnavailable, probe
from .higgsfield_browser_contracts import MediaKind

# Столько байт хватает, чтобы узнать страницу ошибки, и мало, чтобы что-то
# утащить: читается только начало файла.
SNIFF_BYTES = 512

# Начала файлов, которые точно не являются медиа. Проверяется до пробы: проба
# на HTML даст «формат не разбирается», а сказать надо конкретнее — иначе
# владелец получит «поставьте ffmpeg» там, где провайдер вернул ошибку.
_NOT_MEDIA_PREFIXES: tuple[tuple[bytes, str], ...] = (
    (b"<!doctype html", "страница HTML"),
    (b"<html", "страница HTML"),
    (b"<?xml", "документ XML"),
    (b"{", "ответ JSON"),
    (b"[", "ответ JSON"),
)

# Расширение по ИЗМЕРЕННОМУ типу. Формата, которого здесь нет, у нас нет и
# способа принять: список совпадает с тем, что умеет собрать студия.
_EXTENSION_BY_MIME: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/x-matroska": ".mkv",
}

_EXPECTED_TYPE: dict[MediaKind, AssetType] = {
    MediaKind.IMAGE: AssetType.IMAGE,
    MediaKind.VIDEO: AssetType.VIDEO,
}


class MediaRejection(str, Enum):
    """Почему файл не принят. Каждая причина ведёт к разному действию."""

    MISSING = "MISSING"
    EMPTY = "EMPTY"
    TOO_SMALL = "TOO_SMALL"
    NOT_MEDIA = "NOT_MEDIA"
    CORRUPT = "CORRUPT"
    UNMEASURED = "UNMEASURED"
    WRONG_KIND = "WRONG_KIND"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"


@dataclass(frozen=True, slots=True)
class MediaVerdict:
    """Что известно о файле после проверки."""

    accepted: bool
    reason: str = ""
    rejection: MediaRejection | None = None
    probe: ProbeResult | None = None
    extension: str = ""

    @property
    def owner_action_required(self) -> bool:
        """Владельцу надо что-то сделать на машине, а не переделать генерацию."""
        return self.rejection is MediaRejection.UNMEASURED

    def to_dict(self) -> dict[str, str]:
        out = {"accepted": str(self.accepted).lower(), "reason": self.reason}
        if self.rejection is not None:
            out["rejection"] = self.rejection.value
        if self.probe is not None:
            out["mime"] = self.probe.mime
            out["prober"] = self.probe.prober
            out["bytes"] = str(self.probe.bytes)
            if self.probe.width and self.probe.height:
                out["dimensions"] = f"{self.probe.width}x{self.probe.height}"
            if self.probe.duration_seconds is not None:
                out["duration_s"] = f"{self.probe.duration_seconds:.3f}"
        return out


def _reject(rejection: MediaRejection, reason: str,
            probe_result: ProbeResult | None = None) -> MediaVerdict:
    return MediaVerdict(accepted=False, reason=reason, rejection=rejection,
                        probe=probe_result)


def inspect_generated_media(path: str | Path, *, media_kind: MediaKind,
                            min_bytes: int = 1024) -> MediaVerdict:
    """Измерить скачанный файл и решить, медиа ли это вообще."""
    target = Path(path)
    if not target.is_file():
        return _reject(MediaRejection.MISSING,
                       f"файла {target.name} нет на месте")
    size = target.stat().st_size
    if size == 0:
        return _reject(MediaRejection.EMPTY, f"файл {target.name} пуст")
    if size < min_bytes:
        return _reject(
            MediaRejection.TOO_SMALL,
            f"файл {target.name} размером {size} Б меньше порога {min_bytes} Б: "
            f"столько весит сообщение об ошибке, а не кадр")

    with target.open("rb") as handle:
        head = handle.read(SNIFF_BYTES).lstrip()
    lowered = head[:64].lower()
    for prefix, what in _NOT_MEDIA_PREFIXES:
        if lowered.startswith(prefix):
            return _reject(
                MediaRejection.NOT_MEDIA,
                f"провайдер вернул {what} под именем {target.name}, а не медиа")

    try:
        measured = probe(target)
    except CorruptMedia as broken:
        return _reject(MediaRejection.CORRUPT,
                       f"файл {target.name} не дочитывается как заявленный "
                       f"формат: {broken}")
    except ProbeUnavailable as unmeasured:
        return _reject(
            MediaRejection.UNMEASURED,
            f"файл {target.name} нечем измерить: {unmeasured}. Непроверенный "
            f"файл в рабочую область не принимается")

    expected = _EXPECTED_TYPE[media_kind]
    if measured.type is not expected:
        return _reject(
            MediaRejection.WRONG_KIND,
            f"работа просила {expected.value}, а измерено {measured.type.value}",
            measured)
    extension = _EXTENSION_BY_MIME.get(measured.mime, "")
    if not extension:
        return _reject(
            MediaRejection.UNSUPPORTED_FORMAT,
            f"формат {measured.mime} не входит в перечень принимаемых "
            f"({', '.join(sorted(_EXTENSION_BY_MIME))})",
            measured)
    return MediaVerdict(accepted=True, reason=f"измерено {measured.prober}",
                        probe=measured, extension=extension)


__all__ = ["SNIFF_BYTES", "MediaRejection", "MediaVerdict",
           "inspect_generated_media"]
