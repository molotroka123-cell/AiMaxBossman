"""Strip personal contact data from anything the run saves.

Emails and phone numbers are replaced with «[скрыто]». We cannot tell a private person's phone from a business
line reliably, and the research does not need either, so both are removed from saved quotes and logs.
"""
from __future__ import annotations

import re

REDACTED = "[скрыто]"
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Candidate phone runs: optional +, digits with spaces/dashes/dots/brackets. Kept only if >= 9 digits, so prices
# («15 000 ₽») and dates («24.10.2026») survive.
_PHONE = re.compile(r"(?<![\w])\+?\d[\d\s\-().]{7,}\d(?![\w])")
_TG = re.compile(r"(?<![\w@])@[A-Za-z][A-Za-z0-9_]{4,31}\b")


def _phone_sub(m: re.Match) -> str:
    digits = sum(ch.isdigit() for ch in m.group(0))
    return REDACTED if digits >= 9 else m.group(0)


def strip_pii(text: str) -> str:
    if not text:
        return text or ""
    text = _EMAIL.sub(REDACTED, text)
    text = _PHONE.sub(_phone_sub, text)
    text = _TG.sub(REDACTED, text)
    return text
