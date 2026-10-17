"""Mobile copy of bcc.direct_gen.ref_tags (same grammar), so a bad @-tag is refused before anything is sent.

@-references to attached photos in a prompt ("лицо с @1, одежда с @2"), Higgsfield-style.

The owner tags each attached reference: @1, @2, @3 by position and, optionally, a name such as @лицо.
Before the prompt reaches the engine every tag is replaced by the wording the model itself uses for
that picture, so "@1" means exactly the first ``-r`` image:

* Qwen-Image-Edit-2509 (QwenImageEditPlusPipeline in stable-diffusion.cpp) labels references
  ``Picture 1: <|vision_start|>...`` -> ``@1`` becomes ``Picture 1``.
* Qwen-Image 2.1 edit labels references ``<image1><|vision_start|>...`` -> ``@1`` becomes ``<image1>``.
* a model with one start frame (image-to-video) has no per-image label -> ``@1`` becomes ``the start image``.

Unknown tags are refused (Russian message) instead of being sent as text the model would guess at.
``@@`` is a literal ``@``. Text with no attached-image support is never touched.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

NAME = r"[0-9A-Za-zА-Яа-яЁё_-]{1,24}"
NAME_RE = re.compile(rf"^{NAME}$")
# "@" at the start or after a non-word char (so "mail@host" is not a tag); "@@" escapes a literal "@".
# A number tag stops before "-" ("@1-й" is @1); a name tag may contain digits, "_" and "-".
TAG_RE = re.compile(rf"@@|(?<![0-9A-Za-zА-Яа-яЁё_.@])@(\d{{1,2}}(?![0-9A-Za-zА-Яа-яЁё_])|{NAME})")
START_IMAGE = "the start image"


class RefTagError(ValueError):
    """A tag in the prompt names no attached picture (message is Russian, for the owner)."""


@dataclass(frozen=True)
class RefTags:
    count: int                      # attached pictures, in order (@1 = first)
    names: tuple[str, ...] = ()     # optional names per position ("" = none), without "@"

    def index_of(self, tag: str) -> int | None:
        if tag.isdigit():
            n = int(tag)
            return n if 1 <= n <= self.count else None
        low = tag.casefold()
        for i, name in enumerate(self.names[: self.count], start=1):
            if name and name.casefold() == low:
                return i
        return None


def clean_names(names: Sequence[object] | None, count: int) -> tuple[str, ...]:
    """Validate optional names: word chars only, not all digits (those are positions), unique."""
    out: list[str] = []
    for raw in list(names or [])[:count]:
        name = str(raw or "").strip().lstrip("@")
        if name and (not NAME_RE.match(name) or name.isdigit()):
            raise RefTagError(f"имя отсылки «{name}»: только буквы, цифры, _ и -, до 24 знаков, не одни цифры")
        if name and name.casefold() in (n.casefold() for n in out if n):
            raise RefTagError(f"имя отсылки @{name} повторяется")
        out.append(name)
    return tuple(out + [""] * (count - len(out)))


def tags_in(prompt: str) -> list[str]:
    """Every tag the prompt uses, in order (escapes excluded)."""
    return [m.group(1) for m in TAG_RE.finditer(prompt) if m.group(1)]


def rewrite(prompt: str, refs: RefTags, label: str) -> str:
    """Replace each tag by ``label`` formatted with its position ({n}); refuse unknown tags.

    ``label`` is the model's own wording, e.g. "Picture {n}" or "<image{n}>".
    """
    bad = [t for t in tags_in(prompt) if refs.index_of(t) is None]
    if bad:
        known = ", ".join(known_tags(refs)) or "нет прикреплённых фото"
        uniq = ", ".join(dict.fromkeys("@" + t for t in bad))
        raise RefTagError(f"нет такой отсылки: {uniq}. Можно: {known}. Знак @ как текст: @@")

    def sub(m: re.Match) -> str:
        if not m.group(1):
            return "@"
        return label.format(n=refs.index_of(m.group(1)))

    return TAG_RE.sub(sub, prompt)


def known_tags(refs: RefTags) -> list[str]:
    out = []
    for i in range(1, refs.count + 1):
        name = refs.names[i - 1] if i - 1 < len(refs.names) else ""
        out.append(f"@{i}" + (f" (@{name})" if name else ""))
    return out
