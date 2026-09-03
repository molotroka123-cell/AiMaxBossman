"""Acquisition levels. Level 0 cannot be granted. Code, not a model overlay."""
from __future__ import annotations

from enum import StrEnum


class Level(StrEnum):
    L0 = "0"
    L1 = "1"
    L2 = "2"


class ActionClass(StrEnum):
    LEAKED_DUMP = "leaked_dump"
    FOREIGN_ACCOUNT = "foreign_account"
    BYPASS_PROTECTION = "bypass_protection"
    BIOMETRICS = "biometrics"
    PRIVATE_STALKING = "private_stalking"
    CLOSED_PROFILE = "closed_profile"
    SCRAPE_UNSPECIFIED_ROBOTS = "scrape_unspecified_robots"
    RATE_ABOVE_POLITE = "rate_above_polite"
    PUBLIC_OFFICIAL_BY_OFFICE = "public_official_by_office"
    REGISTRY_RELATED_PERSONS = "registry_related_persons"
    TTL_EXTENSION = "ttl_extension"
    PAID_API = "paid_api"
    EXPORT_OUTBOUND = "export_outbound"
    HYPOTHESIS_TO_FACT = "hypothesis_to_fact"
    PUBLIC_REGISTRY = "public_registry"
    PUBLIC_PAGE_ALLOW_ROBOTS = "public_page_allow_robots"
    PUBLIC_DATASET = "public_dataset"
    OWN_SYSTEM = "own_system"


LEVEL0 = frozenset({
    ActionClass.LEAKED_DUMP,
    ActionClass.FOREIGN_ACCOUNT,
    ActionClass.BYPASS_PROTECTION,
    ActionClass.BIOMETRICS,
    ActionClass.PRIVATE_STALKING,
    ActionClass.CLOSED_PROFILE,
})

LEVEL1 = frozenset({
    ActionClass.SCRAPE_UNSPECIFIED_ROBOTS,
    ActionClass.RATE_ABOVE_POLITE,
    ActionClass.PUBLIC_OFFICIAL_BY_OFFICE,
    ActionClass.REGISTRY_RELATED_PERSONS,
    ActionClass.TTL_EXTENSION,
    ActionClass.PAID_API,
    ActionClass.EXPORT_OUTBOUND,
    ActionClass.HYPOTHESIS_TO_FACT,
})

LEVEL2 = frozenset({
    ActionClass.PUBLIC_REGISTRY,
    ActionClass.PUBLIC_PAGE_ALLOW_ROBOTS,
    ActionClass.PUBLIC_DATASET,
    ActionClass.OWN_SYSTEM,
})

L0_KEYWORDS = (
    "combo list", "combolist", "слит", "дамп персонал", "leaked db", "leaked dump",
    "stolen cookie", "чужой аккаунт", "session hijack", "bypass captcha",
    "обход капч", "paywall bypass", "anti-bot", "antibot", "face id",
    "распознаван лиц", "деанон", "stalk", "закрыт профил", "closed group scrape",
)


class PolicyDenied(PermissionError):
    def __init__(self, action: ActionClass, reason: str):
        self.action = action
        self.reason = reason
        super().__init__(reason)


def classify_text(text: str) -> ActionClass | None:
    low = (text or "").lower()
    for needle in L0_KEYWORDS:
        if needle in low:
            if any(k in low for k in ("captcha", "капч", "paywall", "anti-bot", "antibot")):
                return ActionClass.BYPASS_PROTECTION
            if any(k in low for k in ("cookie", "аккаунт", "session hijack")):
                return ActionClass.FOREIGN_ACCOUNT
            if any(k in low for k in ("лиц", "face", "biometric")):
                return ActionClass.BIOMETRICS
            if any(k in low for k in ("деанон", "stalk", "слежк")):
                return ActionClass.PRIVATE_STALKING
            if any(k in low for k in ("закрыт", "closed")):
                return ActionClass.CLOSED_PROFILE
            return ActionClass.LEAKED_DUMP
    return None


def level_of(action: ActionClass) -> Level:
    if action in LEVEL0:
        return Level.L0
    if action in LEVEL1:
        return Level.L1
    if action in LEVEL2:
        return Level.L2
    raise ValueError(f"unknown action {action}")


def decide(action: ActionClass, *, grant_ok: bool = False) -> Level:
    """Return the level if allowed. Raise PolicyDenied otherwise."""
    lv = level_of(action)
    if lv is Level.L0:
        raise PolicyDenied(action, "level 0 sealed: consent of a third party is missing and cannot be substituted")
    if lv is Level.L1 and not grant_ok:
        raise PolicyDenied(action, f"level 1 requires a named unexpired grant for {action}")
    return lv
