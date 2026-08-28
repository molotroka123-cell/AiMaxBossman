"""Распознавание проверок, которые обязан проходить человек.

Капча, неожиданный контроль безопасности, второй фактор, незнакомое модальное
окно — всё это распознаётся, но НЕ проходится автоматикой. Обхода здесь нет и
не будет: капча — это осознанно поставленный владельцем площадки контроль
доступа, и её прохождение автоматом было бы ровно тем, что приложение обещало
не делать.

Распознавание нужно ради противоположного: чтобы не биться о страницу вслепую
до истечения таймаута. Правильное поведение — узнать проверку, остановиться,
перевести сессию в `TAKEOVER_REQUIRED` и позвать человека.

Читать страницу при этом можно и нужно: иначе владелец не увидит, из-за чего
его позвали, а работа не сможет объяснить причину остановки.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ChallengeKind(str, Enum):
    NONE = "NONE"
    CAPTCHA = "CAPTCHA"
    SECURITY_CHECKPOINT = "SECURITY_CHECKPOINT"
    TWO_FACTOR = "TWO_FACTOR"
    UNKNOWN_MODAL = "UNKNOWN_MODAL"


# Признаки в разметке. Список известных проверяющих сервисов — не для того,
# чтобы что-то с ними делать, а чтобы назвать человеку то, что он увидит.
MARKUP_MARKERS: tuple[tuple[str, ChallengeKind, str], ...] = (
    ("recaptcha", ChallengeKind.CAPTCHA, "Google reCAPTCHA"),
    ("g-recaptcha", ChallengeKind.CAPTCHA, "Google reCAPTCHA"),
    ("hcaptcha", ChallengeKind.CAPTCHA, "hCaptcha"),
    ("h-captcha", ChallengeKind.CAPTCHA, "hCaptcha"),
    ("cf-turnstile", ChallengeKind.CAPTCHA, "Cloudflare Turnstile"),
    ("challenges.cloudflare.com", ChallengeKind.CAPTCHA, "Cloudflare Challenge"),
    ("funcaptcha", ChallengeKind.CAPTCHA, "Arkose FunCaptcha"),
    ("arkoselabs", ChallengeKind.CAPTCHA, "Arkose FunCaptcha"),
    ("geetest", ChallengeKind.CAPTCHA, "GeeTest"),
    ("/challenge/", ChallengeKind.SECURITY_CHECKPOINT, "контроль безопасности площадки"),
    ("checkpoint_required", ChallengeKind.SECURITY_CHECKPOINT, "контроль безопасности"),
    ("two_factor", ChallengeKind.TWO_FACTOR, "второй фактор"),
    ("two-factor", ChallengeKind.TWO_FACTOR, "второй фактор"),
)

# Текстовые признаки — последняя линия: самописная проверка без известного
# сервиса. Многоязычно, потому что интерфейс аккаунта может быть на любом языке.
TEXT_MARKERS: tuple[tuple[str, ChallengeKind, str], ...] = (
    ("i'm not a robot", ChallengeKind.CAPTCHA, "проверка «я не робот»"),
    ("i am not a robot", ChallengeKind.CAPTCHA, "проверка «я не робот»"),
    ("я не робот", ChallengeKind.CAPTCHA, "проверка «я не робот»"),
    ("verify you are human", ChallengeKind.CAPTCHA, "проверка «вы человек»"),
    ("подтвердите, что вы человек", ChallengeKind.CAPTCHA, "проверка «вы человек»"),
    ("enter the characters", ChallengeKind.CAPTCHA, "ввод символов с картинки"),
    ("введите символы", ChallengeKind.CAPTCHA, "ввод символов с картинки"),
    ("confirm your identity", ChallengeKind.SECURITY_CHECKPOINT,
     "подтверждение личности"),
    ("suspicious login", ChallengeKind.SECURITY_CHECKPOINT, "подозрительный вход"),
    ("подозрительный вход", ChallengeKind.SECURITY_CHECKPOINT, "подозрительный вход"),
    ("we detected an unusual login", ChallengeKind.SECURITY_CHECKPOINT,
     "необычный вход"),
    ("help us confirm it's you", ChallengeKind.SECURITY_CHECKPOINT,
     "подтверждение владельца"),
    ("enter the code we sent", ChallengeKind.TWO_FACTOR, "код из сообщения"),
    ("введите код", ChallengeKind.TWO_FACTOR, "код подтверждения"),
    ("security code", ChallengeKind.TWO_FACTOR, "код безопасности"),
    ("authentication code", ChallengeKind.TWO_FACTOR, "код аутентификации"),
    ("two-factor authentication", ChallengeKind.TWO_FACTOR, "двухфакторная проверка"),
)


@dataclass(frozen=True, slots=True)
class Challenge:
    """Найденная проверка. Никуда не ведёт, кроме как к человеку."""

    kind: ChallengeKind = ChallengeKind.NONE
    provider: str = ""
    matched: str = ""
    evidence: str = ""

    @property
    def present(self) -> bool:
        return self.kind is not ChallengeKind.NONE

    def to_dict(self) -> dict[str, Any]:
        return {"present": self.present, "kind": self.kind.value,
                "provider": self.provider, "matched": self.matched,
                "evidence": self.evidence}

    def describe(self) -> str:
        if not self.present:
            return "проверок на странице не обнаружено"
        return (f"на странице {self.provider or self.kind.value}; действие остановлено, "
                f"нужен человек. Автоматически такие проверки не проходятся")


NO_CHALLENGE = Challenge()


def detect_challenge(markup: str = "", text: str = "", url: str = "") -> Challenge:
    """Есть ли на странице проверка и какая. Ничего с ней не делает."""
    haystack = f"{(markup or '').lower()} {(url or '').lower()}"
    for marker, kind, provider in MARKUP_MARKERS:
        if marker in haystack:
            return Challenge(kind=kind, provider=provider, matched=marker,
                             evidence="разметка страницы")
    low_text = (text or "").lower()
    for marker, kind, provider in TEXT_MARKERS:
        if marker in low_text:
            return Challenge(kind=kind, provider=provider, matched=marker,
                             evidence="текст страницы")
    return NO_CHALLENGE


__all__ = ["MARKUP_MARKERS", "NO_CHALLENGE", "TEXT_MARKERS", "Challenge", "ChallengeKind",
           "detect_challenge"]
