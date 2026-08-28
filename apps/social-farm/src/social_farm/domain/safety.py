"""Классы безопасности действий и дефолтное решение для каждого.

Дефолт — это не «пока не настроили». Это то, что произойдёт, если настройку
забыли, потеряли или испортили. Поэтому он выбран так, чтобы забытая настройка
была неудобной, а не опасной.

Отдельно стоит SECURITY: пароль, второй фактор и владение аккаунтом выведены
из обычной автоматизации совсем. Это не «строгий дефолт, который можно
ослабить» — это запрет, который политика не снимает.
"""
from __future__ import annotations

from enum import Enum


class SafetyClass(str, Enum):
    READ = "READ"
    REVERSIBLE_WRITE = "REVERSIBLE_WRITE"
    PUBLIC_PUBLISH = "PUBLIC_PUBLISH"
    DIRECT_COMMUNICATION = "DIRECT_COMMUNICATION"
    MODERATION = "MODERATION"
    DESTRUCTIVE = "DESTRUCTIVE"
    SECURITY = "SECURITY"
    RELATIONSHIP = "RELATIONSHIP"


# Решение по умолчанию для класса, если правила политики ничего не сказали.
DEFAULT_DECISION: dict[SafetyClass, str] = {
    SafetyClass.READ: "AUTO",
    SafetyClass.REVERSIBLE_WRITE: "ASK",
    SafetyClass.PUBLIC_PUBLISH: "ASK",
    SafetyClass.DIRECT_COMMUNICATION: "ASK",
    SafetyClass.MODERATION: "ASK",
    SafetyClass.DESTRUCTIVE: "ASK",
    SafetyClass.SECURITY: "DENY",
    # «relationships.follow: DENY, пока правило аккаунта явно не разрешит» —
    # массовые подписки это ровно тот случай, где дефолт решает всё.
    SafetyClass.RELATIONSHIP: "DENY",
}

# Классы, чей запрет политика снять НЕ может. Правило с AUTO на такой
# возможности — это ошибка конфигурации, а не разрешение.
UNOVERRIDABLE: frozenset[SafetyClass] = frozenset({SafetyClass.SECURITY})


# Каталог нормализованных возможностей: имя → класс безопасности.
# «Each normalized capability declares safety class» (docs/43).
# Имена нейтральны к провайдеру: BOSSMAN и интерфейс не должны знать про
# конкретные endpoint'ы Instagram.
CAPABILITY_SAFETY: dict[str, SafetyClass] = {
    # чтение
    "account.read": SafetyClass.READ,
    "media.read": SafetyClass.READ,
    "comments.read": SafetyClass.READ,
    "messages.read": SafetyClass.READ,
    "mentions.read": SafetyClass.READ,
    "insights.read": SafetyClass.READ,
    "relationships.read": SafetyClass.READ,
    # черновики и расписание — обратимо
    "content.draft": SafetyClass.REVERSIBLE_WRITE,
    "content.schedule": SafetyClass.REVERSIBLE_WRITE,
    "content.unschedule": SafetyClass.REVERSIBLE_WRITE,
    # публикация наружу
    "media.publish.image": SafetyClass.PUBLIC_PUBLISH,
    "media.publish.carousel": SafetyClass.PUBLIC_PUBLISH,
    "media.publish.reel": SafetyClass.PUBLIC_PUBLISH,
    "media.publish.story": SafetyClass.PUBLIC_PUBLISH,
    "comments.reply": SafetyClass.PUBLIC_PUBLISH,
    # переписка с человеком
    "messages.reply": SafetyClass.DIRECT_COMMUNICATION,
    "messages.send": SafetyClass.DIRECT_COMMUNICATION,
    # модерация
    "comments.hide": SafetyClass.MODERATION,
    "comments.unhide": SafetyClass.MODERATION,
    "comments.delete": SafetyClass.DESTRUCTIVE,
    # разрушающее
    "media.delete": SafetyClass.DESTRUCTIVE,
    "media.archive": SafetyClass.DESTRUCTIVE,
    "account.profile.update": SafetyClass.DESTRUCTIVE,
    # безопасность аккаунта — вне автоматизации
    "account.password.change": SafetyClass.SECURITY,
    "account.mfa.change": SafetyClass.SECURITY,
    "account.ownership.transfer": SafetyClass.SECURITY,
    "account.disconnect": SafetyClass.SECURITY,
    # отношения и вовлечение
    "relationships.follow": SafetyClass.RELATIONSHIP,
    "relationships.unfollow": SafetyClass.RELATIONSHIP,
    "relationships.block": SafetyClass.MODERATION,
    "engagement.like": SafetyClass.RELATIONSHIP,
}


def safety_of(capability: str) -> SafetyClass:
    """Класс неизвестной возможности — самый строгий из применимых.

    Возможность, которой нет в каталоге, — это возможность, которую никто не
    классифицировал. Считать её безопасной значит доверять пробелу в знании.
    """
    known = CAPABILITY_SAFETY.get(capability)
    if known is not None:
        return known
    return SafetyClass.DESTRUCTIVE


def default_decision(capability: str) -> str:
    return DEFAULT_DECISION[safety_of(capability)]


def is_unoverridable(capability: str) -> bool:
    return safety_of(capability) in UNOVERRIDABLE


__all__ = ["CAPABILITY_SAFETY", "DEFAULT_DECISION", "SafetyClass", "UNOVERRIDABLE",
           "default_decision", "is_unoverridable", "safety_of"]
