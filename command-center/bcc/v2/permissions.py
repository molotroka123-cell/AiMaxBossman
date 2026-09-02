"""Политика разрешений V2 (ordered ACL, последнее совпадение побеждает).

F-018: `PermissionPolicy.safe_default()` — единственный источник deny-листа
чувствительных файлов (`*.env`, `*id_rsa*`, `*wallet*`). До F-018 у него было
ноль импортёров; теперь `bcc/v2/code_index.py::CodeIndex.iter_files` спрашивает
`denies_read()` и не индексирует такие пути.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Literal

Effect = Literal["auto", "ask", "deny"]

@dataclass(frozen=True, slots=True)
class PermissionRule:
    action: str
    resource: str = "*"
    effect: Effect = "ask"

    def matches(self, action: str, resource: str) -> bool:
        return fnmatch.fnmatch(action, self.action) and fnmatch.fnmatch(resource, self.resource)

@dataclass(slots=True)
class PermissionPolicy:
    """Ordered policy. Last matching rule wins, like a firewall ACL."""
    rules: list[PermissionRule] = field(default_factory=list)
    default: Effect = "ask"

    def decide(self, action: str, resource: str = "*") -> Effect:
        decision: Effect = self.default
        for rule in self.rules:
            if rule.matches(action, resource):
                decision = rule.effect
        return decision

    def denies_read(self, path: str | PurePath) -> bool:
        """True, если `filesystem.read` на этот путь запрещён политикой.
        Проверяются и относительный путь (posix), и голое имя файла: правила
        вида `*id_rsa*` должны срабатывать независимо от глубины каталога."""
        rel = PurePath(path).as_posix()
        name = PurePath(path).name
        return (self.decide("filesystem.read", rel) == "deny"
                or self.decide("filesystem.read", name) == "deny")

    @classmethod
    def safe_default(cls) -> "PermissionPolicy":
        return cls(
            default="ask",
            rules=[
                PermissionRule("read", "*", "auto"),
                PermissionRule("filesystem.read", "*", "auto"),
                PermissionRule("filesystem.read", "*.env", "deny"),
                PermissionRule("filesystem.read", "*id_rsa*", "deny"),
                PermissionRule("filesystem.read", "*wallet*", "deny"),
                PermissionRule("terminal.run", "git status*", "auto"),
                PermissionRule("terminal.run", "git diff*", "auto"),
                PermissionRule("terminal.run", "git log*", "auto"),
                PermissionRule("terminal.run", "pytest*", "auto"),
                PermissionRule("terminal.run", "npm test*", "auto"),
                PermissionRule("terminal.run", "git push*", "ask"),
                PermissionRule("terminal.admin", "*", "deny"),
                PermissionRule("browser.payment", "*", "deny"),
                PermissionRule("browser.wallet", "*", "deny"),
                PermissionRule("browser.bank_transfer", "*", "deny"),
            ],
        )
