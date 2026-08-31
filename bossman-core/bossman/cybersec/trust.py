"""Provenance / trust levels: откуда пришёл текст или предложение.

Ключевой инвариант CyberSec V1: САМОПРОВОЗГЛАШЁННЫЙ авторитет ничего не значит.
Уровень доверия задаётся КАНАЛОМ доставки, а не содержимым сообщения. Текст
с веб-страницы не становится «политикой владельца» оттого, что так написано.
"""
from __future__ import annotations

from enum import IntEnum


class TrustLevel(IntEnum):
    UNTRUSTED = 0        # веб-страница, репозиторий, память, вывод инструмента
    EXTERNAL = 1         # внешний, но идентифицированный источник
    TRUSTED_REPO = 2     # содержимое своего репозитория
    VERIFIED_TOOL = 3    # результат верифицированного инструмента
    SIGNED_INTERNAL = 4  # подписанный внутренний артефакт
    OWNER_POLICY = 5     # владелец через аутентифицированный канал


#: Минимальный уровень доверия для чувствительных операций.
REQUIRED: dict[str, TrustLevel] = {
    "change_policy": TrustLevel.OWNER_POLICY,
    "grant_scope": TrustLevel.OWNER_POLICY,
    "approve_action": TrustLevel.OWNER_POLICY,
    "promote_skill": TrustLevel.OWNER_POLICY,
    "read_secret": TrustLevel.OWNER_POLICY,
    "write_memory_durable": TrustLevel.VERIFIED_TOOL,
    "execute_tool": TrustLevel.VERIFIED_TOOL,
}


def has_authority(source: TrustLevel, operation: str) -> bool:
    """True только если КАНАЛ источника достаточно доверен для операции.

    Неизвестная операция → deny-by-default (не угадываем).
    """
    required = REQUIRED.get(operation)
    if required is None:
        return False
    return int(source) >= int(required)
