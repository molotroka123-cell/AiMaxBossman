"""Permission model V2 (контракты §4): строковые константы и проверки.

Опасное действие без явного права у агента идёт через очередь подтверждений —
это архитектурное правило, фичи обязаны им пользоваться, а не изобретать своё.
"""
from __future__ import annotations

ALL_PERMISSIONS = (
    "filesystem.read", "filesystem.write",
    "terminal.read", "terminal.run",
    "git.read", "git.write",
    "browser.read", "browser.control",
    "model.load", "model.unload",
    "email.draft", "email.send",
    "deploy.preview", "deploy.production",
    "invoice.create", "payment.read",
    "settings.write",
)

DANGEROUS = frozenset({
    "filesystem.write", "terminal.run", "git.write", "browser.control",
    "model.unload", "email.send", "deploy.preview", "deploy.production",
    "invoice.create", "settings.write",
})


def is_valid(perm: str) -> bool:
    return perm in ALL_PERMISSIONS


def is_dangerous(perm: str) -> bool:
    return perm in DANGEROUS


def agent_allowed(agent: dict, perm: str) -> bool:
    """Право есть, только если оно явно выдано агенту (permissions: {perm: true}
    или список строк). Опасные права по умолчанию выключены."""
    granted = agent.get("permissions") or {}
    if isinstance(granted, list):
        return perm in granted
    return bool(granted.get(perm))


def needs_approval(agent: dict, perm: str) -> bool:
    """Опасное действие без явного права — только через approval."""
    return is_dangerous(perm) and not agent_allowed(agent, perm)
