#!/usr/bin/env python3
"""Детерминированный локальный MCP-сервер для тестов Lane D.

Настоящий сервер на официальном SDK (`mcp.server.fastmcp.FastMCP`, mcp==1.27),
поднимается по stdio отдельным процессом — никаких моков протокола.

Инструменты:
  * `echo(text)`          — возвращает `эхо: <text>` (AUTO-кейс);
  * `write_note(text)`    — «опасная» запись, считает вызовы в файл счётчика
                            (ASK-кейс + доказательство «ровно один раз»);
  * `secret(text)`        — существует, но агенту не выдаётся (контекст-гигиена);
  * `boom()`              — убивает процесс сервера (кейс падения сервера).

Счётчик вызовов пишется в файл из env `MCP_ECHO_COUNTER`, чтобы тест мог
проверить реальное число исполнений, не заглядывая внутрь процесса.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# SDK 1.27: класс называется FastMCP (mcp.server.mcpserver.MCPServer не существует —
# вся MCP-связка падала именно на этом импорте каскадом из 13 тестов).
# Параметра `version` у конструктора в 1.27 больше нет — версия уходит в протоколе.
server = FastMCP(name="echo-fixture")


def _bump(tool: str) -> int:
    path = os.environ.get("MCP_ECHO_COUNTER")
    if not path:
        return 0
    f = Path(path)
    lines = f.read_text(encoding="utf-8").splitlines() if f.exists() else []
    lines.append(tool)
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sum(1 for line in lines if line == tool)


@server.tool(description="Возвращает переданный текст с префиксом.")
def echo(text: str) -> str:
    _bump("echo")
    return f"эхо: {text}"


@server.tool(description="Записывает заметку (небезопасная операция).")
def write_note(text: str) -> str:
    n = _bump("write_note")
    return f"записано #{n}: {text}"


@server.tool(description="Секретный инструмент, который агенту не выдают.")
def secret(text: str = "") -> str:
    _bump("secret")
    return f"секрет: {text}"


@server.tool(description="Роняет процесс сервера (для проверки health).")
def boom() -> str:
    _bump("boom")
    sys.stdout.flush()
    os._exit(9)
    return "недостижимо"


if __name__ == "__main__":
    server.run("stdio")
