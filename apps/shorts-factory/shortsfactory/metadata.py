"""Title / description / hashtags via the local LLM, with hard post-checks, and the YouTube synthetic-media flag."""
from __future__ import annotations

import re
from typing import Callable

from .llm import parse_json

TITLE_MAX = 100
MAX_TAGS = 6
DISCLOSURE = {"ru": "Содержит синтетический/сгенерированный ИИ контент (голос и/или изображение).",
              "en": "Contains AI-generated / synthetic content (voice and/or imagery)."}
CLICKBAIT = re.compile(r"(100%|гарант\w*|guarantee\w*|shocking|шок\b|you won.?t believe|не поверите)", re.I)

SYSTEM = ("You write metadata for a YouTube Short. Use ONLY facts present in the excerpt; no clickbait promises, no "
          "invented claims, no ALL CAPS. Language: {lang}. Reply ONLY with JSON: "
          '{{"title":"<= 90 chars","description":"2-3 sentences","hashtags":["#tag", ...]}} with 3-5 topical hashtags.')


def needs_disclosure(source: dict, item: dict | None = None) -> bool:
    """YouTube asks creators to disclose realistic altered or synthetic media. We are conservative:
    any AI-generated source or any item marked realistic_synthetic -> true."""
    return bool(source.get("ai_generated") or (item or {}).get("realistic_synthetic") or (item or {}).get("ai_generated"))


def _tag(t: str) -> str | None:
    t = re.sub(r"[^\w]", "", str(t).strip().lstrip("#"), flags=re.U)
    return f"#{t}" if 2 <= len(t) <= 30 else None


def normalize(raw: dict, *, lang: str, synthetic: bool, fallback_text: str, credit: str | None = None) -> dict:
    title = re.sub(r"\s+", " ", str(raw.get("title") or "")).strip().strip('"').replace("<", "").replace(">", "")
    if not title:
        title = (fallback_text.split(".")[0] or "Short").strip()[:80]
    if CLICKBAIT.search(title):
        title = CLICKBAIT.sub("", title).strip(" -:!")
    if title.isupper():
        title = title.capitalize()
    tags = []
    for t in ["#shorts", *(raw.get("hashtags") or [])]:
        t = _tag(t)
        if t and t.lower() not in [x.lower() for x in tags]:
            tags.append(t)
    tags = tags[:MAX_TAGS]
    if len(title) > TITLE_MAX:
        title = title[: TITLE_MAX - 1].rsplit(" ", 1)[0] + "…"
    desc = re.sub(r"[<>]", "", str(raw.get("description") or "")).strip()
    parts = [desc] if desc else []
    if credit:
        parts.append(credit)
    if synthetic:
        parts.append(DISCLOSURE.get(lang, DISCLOSURE["en"]))
    parts.append(" ".join(tags))
    return {"title": title, "description": "\n\n".join(parts)[:4900], "hashtags": tags, "description_core": desc, "credit": credit,
            "tags": [t.lstrip("#") for t in tags], "contains_synthetic_media": bool(synthetic), "language": lang}


def generate(text: str, *, chat: Callable | None, lang: str, synthetic: bool, credit: str | None = None) -> dict:
    status, raw, err = "NOT_CONNECTED", {}, None
    if chat is not None:
        try:
            raw = parse_json(chat([{"role": "system", "content": SYSTEM.format(lang="Russian" if lang == "ru" else "English")},
                                   {"role": "user", "content": text[:3000]}]))
            status = "OK"
        except Exception as exc:  # noqa: BLE001
            status, err = "ERROR", f"{type(exc).__name__}: {str(exc)[:200]}"
    meta = normalize(raw, lang=lang, synthetic=synthetic, fallback_text=text, credit=credit)
    meta.update(llm_status=status, llm_error=err)
    return meta
