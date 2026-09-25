from __future__ import annotations

import re
from dataclasses import dataclass


_EDIT_PREFIX = re.compile(
    r"^(?:/photoedit|/editphoto|отредактируй|измени|убери|удали|добавь|замени|"
    r"сделай\s+фон|ретуш|edit|remove|add|replace)\b",
    re.I,
)


@dataclass(frozen=True, slots=True)
class PhotoIntent:
    kind: str  # analyze | edit
    prompt: str


def photo_intent(text: str, *, has_photo: bool) -> PhotoIntent:
    """Small deterministic classifier; Jev may refine ordinary analysis later."""
    value = " ".join(str(text or "").strip().split())
    if value.lower().startswith(("/photoedit", "/editphoto")):
        prompt = value.split(" ", 1)[1].strip() if " " in value else ""
        return PhotoIntent("edit", prompt)
    if has_photo and _EDIT_PREFIX.search(value):
        return PhotoIntent("edit", value)
    return PhotoIntent("analyze", value)


def edit_help() -> str:
    return (
        "Пришли фото с подписью, что изменить, или после фото напиши "
        "/photoedit <что изменить>."
    )
