"""Инструменты — модули с декларацией: имя, права (read/write/exec/send),
нужно ли подтверждение по умолчанию, схема аргументов, лимит результата.

Агент получает только инструменты из своего agent.yaml. Новый инструмент = один файл.
Обрезка происходит в коде инструмента, не по просьбе модели (10.4): каждый результат —
в лимите, с признаком truncated и способом дочитать.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..context import estimate_tokens


@dataclass
class ToolResult:
    content: str                 # уже в лимите своего инструмента
    one_line: str = ""           # «<инструмент>: <итог>» — для схлопывания старой истории
    truncated: bool = False
    more: str = ""               # как получить остальное (обязателен при truncated)
    error: bool = False

    def render(self) -> str:
        out = self.content
        if self.truncated:
            out += f"\n[truncated: да; дочитать: {self.more}]"
        return out


@dataclass
class ToolContext:
    agent: str
    run_id: int | None = None
    workdir: Path = Path(".")     # корень, за который fs.* не выходит
    journal: Path | None = None   # journal.md текущего проекта/агента
    notes_dir: Path | None = None


Handler = Callable[[dict, ToolContext], Awaitable[ToolResult]]


@dataclass
class ToolDef:
    name: str
    description: str
    rights: str                   # read | write | exec | send
    handler: Handler
    params: dict = field(default_factory=dict)   # JSON-схема properties
    required: list[str] = field(default_factory=list)
    confirm_default: bool = False # действие необратимо → подтверждение, если агент не переопределил
    token_limit: int = 4000

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name.replace(".", "_"),  # OpenAI tools не любят точки
                "description": self.description,
                "parameters": {"type": "object", "properties": self.params,
                               "required": self.required},
            },
        }


REGISTRY: dict[str, ToolDef] = {}


def register(tool: ToolDef) -> ToolDef:
    REGISTRY[tool.name] = tool
    return tool


def by_api_name(api_name: str) -> ToolDef | None:
    for t in REGISTRY.values():
        if t.name.replace(".", "_") == api_name:
            return t
    return None


def clip(text: str, limit_tokens: int) -> tuple[str, bool]:
    """Обрезает текст под лимит токенов; True — если обрезали."""
    if estimate_tokens(text) <= limit_tokens:
        return text, False
    return text[: limit_tokens * 3], True


def compact_json(data: Any) -> str:
    """JSON — компактный, без отступов (10.4); двоичное — никогда."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def tool_line(name: str, tool: ToolDef) -> str:
    """Одна строка на инструмент в системном промпте (бюджет блока system)."""
    return f"- {name} [{tool.rights}]: {tool.description}"


# Регистрация всех модулей с инструментами.
from . import files, shell, gitops, journal, net, media, office, browser  # noqa: E402,F401
