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
    # Каналы связи (мост OpenClaw): чтение состояния и отправка живым людям.
    # Отправка вынесена в отдельное право, а не подведена под email.send:
    # это другой набор получателей и другой способ ошибиться.
    "channel.read", "channel.send",
    # Computer Use (tools_computer): смотреть на экран владельца и действовать на
    # нём — оба опасные: снимок экрана тоже выносит чужие данные в контекст модели.
    "computer.observe", "computer.control",
    # cli.delegate in bypass mode: the external coder CLI runs without its own permission prompts
    # inside a candidate worktree (owner 23.10). Dangerous: granted explicitly or approved per call.
    "cli.bypass",
    # photo.edit (tools_photo_edit): queue a LOCAL Direct Gen edit of a chat reference photo (owner 23.10).
    # Not dangerous (local queue, owner STOP and the pult's fixed content rules still apply); without the grant → ASK.
    "media.generate",
    # obs_lite.record: records the owner's screen to a file - the same data exposure as computer.observe.
    "desktop.control",
)

DANGEROUS = frozenset({
    "filesystem.write", "terminal.run", "git.write", "browser.control",
    "model.unload", "email.send", "deploy.preview", "deploy.production",
    "invoice.create", "settings.write", "channel.send",
    "computer.observe", "computer.control", "cli.bypass", "desktop.control",
})


def is_valid(perm: str) -> bool:
    return perm in ALL_PERMISSIONS


def is_dangerous(perm: str) -> bool:
    """Fail-closed (GLM security review 24.10): a non-empty permission this module does not know is dangerous.

    A tool whose permission string was never added here (``desktop.control`` until 24.10) or carries a typo
    used to read as «not dangerous» = no approval. An empty string still means «permission-free tool»."""
    return perm in DANGEROUS or (bool(perm) and perm not in ALL_PERMISSIONS)


def agent_allowed(agent: dict, perm: str) -> bool:
    """Право есть, только если оно явно выдано агенту (permissions: {perm: true}
    или список строк). Опасные права по умолчанию выключены.

    Only a real ``True`` grants: ``{"terminal.run": "false"}`` from a hand-edited config used to pass ``bool()``."""
    granted = agent.get("permissions") or {}
    if isinstance(granted, list):
        return perm in granted
    if not isinstance(granted, dict):
        return False
    return granted.get(perm) is True


def needs_approval(agent: dict, perm: str) -> bool:
    """Опасное действие без явного права — только через approval."""
    return is_dangerous(perm) and not agent_allowed(agent, perm)
