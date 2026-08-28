"""Отпечаток цели: чем доказывается, что жмём именно то, что видели.

Спека требует проверки устаревшей цели (`09_INSTAGRAM_BROWSER_FALLBACK`,
`55_BROWSER_STATE_MACHINE`), но алгоритма отпечатка не задаёт — это пробел G20.

**Решение: `sha256` от шести частей**

    роль | доступное имя | нормализованный текст | тег |
    порядковый номер среди совпадений | версия пакета селекторов

Почему именно эти шесть:

* *роль, доступное имя, текст, тег* — смысл элемента. Если под тем же местом
  оказался элемент с другим смыслом, это другая цель, чем бы она ни выглядела.
* *порядковый номер среди совпадений* — защита от перестановки одинаковых
  элементов. Три одинаковые кнопки «Удалить» в списке дают три одинаковых
  первых четыре части; без номера удаление второй записи после того, как
  список сдвинулся, выглядело бы совпадением.
* *версия пакета селекторов* — обновление пакета обесценивает все отпечатки,
  снятые старым. Иначе отпечаток, снятый прошлой версией, продолжал бы
  считаться действительным на новой семантике.

Чего в отпечатке НЕТ: значения полей ввода. Значение поля `type=password` сюда
физически не доходит (`dom.py` не отдаёт его наружу), но и обычное значение не
берётся: пользователь мог допечатать символ между снимком и действием, и это не
повод считать кнопку другой.

Координаты и позиция на экране в отпечаток не входят тоже — по ним нельзя
отличить «страница прокрутилась» от «под курсором другая кнопка».
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

FINGERPRINT_ALGORITHM = (
    "sha256(role|accessible_name|normalized_text|tag|ordinal|selector_pack_version)")
FINGERPRINT_LENGTH = 32
_TEXT_LIMIT = 220
_WS = re.compile(r"\s+")


def normalize_text(value: Any) -> str:
    """Схлопнуть пробелы, обрезать, привести к нижнему регистру, ограничить длину.

    Регистр убирается намеренно: провайдеры меняют `Опубликовать` на
    `ОПУБЛИКОВАТЬ` при смене темы оформления, и считать это другой кнопкой
    значит ломать работу на пустом месте. Смысл при этом не меняется.
    """
    text = _WS.sub(" ", str(value or "")).strip()
    return text[:_TEXT_LIMIT].casefold()


@dataclass(frozen=True, slots=True)
class TargetDescriptor:
    """Что мы знаем об элементе. Ровно то, из чего считается отпечаток, плюс
    служебное для действия и аудита."""

    ref: str
    tag: str = ""
    role: str = ""
    accessible_name: str = ""
    text: str = ""
    type: str = ""
    ordinal: int = 0
    secret: bool = False
    filled: bool = False
    disabled: bool = False
    href: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TargetDescriptor":
        return cls(
            ref=str(raw.get("ref") or ""), tag=str(raw.get("tag") or ""),
            role=str(raw.get("role") or ""),
            accessible_name=str(raw.get("accessible_name") or ""),
            text=str(raw.get("text") or ""), type=str(raw.get("type") or ""),
            ordinal=int(raw.get("ordinal") or 0),
            secret=bool(raw.get("secret")), filled=bool(raw.get("filled")),
            disabled=bool(raw.get("disabled")), href=str(raw.get("href") or ""))

    def to_dict(self) -> dict[str, Any]:
        return {"ref": self.ref, "tag": self.tag, "role": self.role,
                "accessible_name": self.accessible_name, "text": self.text,
                "type": self.type, "ordinal": self.ordinal, "secret": self.secret,
                "filled": self.filled, "disabled": self.disabled, "href": self.href}

    def semantic_identity(self) -> str:
        """Человекочитаемая личность цели — то, что идёт в аудит.

        Аудит должен позволять понять, ЧТО было нажато, не храня разметку.
        """
        role = self.role or self.tag or "элемент"
        name = self.accessible_name or normalize_text(self.text) or "без имени"
        return f"{role}[{name}]#{self.ordinal}"


def target_fingerprint(*, role: str, accessible_name: str, text: str, tag: str,
                       ordinal: int, pack_version: str) -> str:
    parts = "\x1f".join((
        normalize_text(role), normalize_text(accessible_name), normalize_text(text),
        normalize_text(tag), str(int(ordinal)), str(pack_version or ""),
    ))
    return hashlib.sha256(parts.encode("utf-8", "replace")).hexdigest()[:FINGERPRINT_LENGTH]


def fingerprint_of(descriptor: TargetDescriptor, pack_version: str) -> str:
    return target_fingerprint(
        role=descriptor.role, accessible_name=descriptor.accessible_name,
        text=descriptor.text, tag=descriptor.tag, ordinal=descriptor.ordinal,
        pack_version=pack_version)


__all__ = ["FINGERPRINT_ALGORITHM", "FINGERPRINT_LENGTH", "TargetDescriptor",
           "fingerprint_of", "normalize_text", "target_fingerprint"]
