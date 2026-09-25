from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TelegramToolPolicy:
    """Structural tool perimeter shown to a Telegram model.

    Denied tools are removed before schema construction; this is intentionally
    stronger than asking the model not to call them.
    """

    allowed_prefixes: tuple[str, ...] = (
        "web.search",
        "web.read",
        "browser.read",
        "calculator",
        "vision.analyze_own",
        "file.analyze_upload",
        "code.reason",
    )
    denied_prefixes: tuple[str, ...] = (
        "computer",
        "shell",
        "terminal",
        "owner",
        "secrets",
        "payments",
        "trading",
        "admin",
        "geo",
        "geolocation",
        "location",
        "device",
        "persona",
        "files",
        "filesystem",
    )

    def allows(self, tool_name: str) -> bool:
        name = str(tool_name or "").strip()
        if not name:
            return False
        if any(name == p or name.startswith(p + ".") for p in self.denied_prefixes):
            return False
        return any(name == p or name.startswith(p + ".") for p in self.allowed_prefixes)

    def filter_tool_names(self, tool_names: list[str] | tuple[str, ...]) -> list[str]:
        return [name for name in tool_names if self.allows(name)]


def assert_telegram_tool_perimeter(tool_names: list[str]) -> None:
    policy = TelegramToolPolicy()
    leaked = [name for name in tool_names if not policy.allows(name)]
    if leaked:
        raise ValueError("Telegram tool registry contains forbidden tools: " + ", ".join(leaked))
