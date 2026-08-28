from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Any

Transport = Literal["stdio", "http"]

def normalize_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_") or "tool"

def namespaced_tool(server_id: str, tool_name: str) -> str:
    return f"mcp:{normalize_name(server_id)}:{normalize_name(tool_name)}"

@dataclass(slots=True)
class MCPServerSpec:
    id: str
    name: str
    transport: Transport
    command: list[str] = field(default_factory=list)
    url: str = ""
    cwd: str = ""
    env_keys: list[str] = field(default_factory=list)
    enabled: bool = True
    timeout_seconds: int = 30

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.transport == "stdio" and not self.command:
            errors.append("stdio MCP requires command")
        if self.transport == "http" and not self.url:
            errors.append("http MCP requires url")
        if self.transport not in ("stdio", "http"):
            errors.append("invalid MCP transport")
        return errors

@dataclass(slots=True)
class MCPToolView:
    server_id: str
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)

    @property
    def bossman_name(self) -> str:
        return namespaced_tool(self.server_id, self.name)

# Important:
# Protocol execution must use the official MCP SDK in the integration layer.
# Do not hand-roll JSON-RPC framing here. This file intentionally defines the
# canonical BOSSMAN naming/config contract independent of transport SDK details.
