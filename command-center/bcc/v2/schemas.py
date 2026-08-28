from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Effect = Literal["auto", "ask", "deny"]

class OpenRouterSyncIn(BaseModel):
    force: bool = False

class OpenRouterPinIn(BaseModel):
    remote_id: str
    alias: str | None = None

class CapabilityVerifyIn(BaseModel):
    capabilities: list[str] = Field(default_factory=lambda: ["chat", "tools", "structured_output"])

class BrowserProfileIn(BaseModel):
    name: str
    agent_id: int | None = None
    policy: dict[str, Any] = Field(default_factory=dict)

class BrowserSessionIn(BaseModel):
    profile_id: int | None = None
    agent_id: int | None = None
    task_id: int | None = None
    headless: bool = True

class BrowserNavigateIn(BaseModel):
    url: str

class BrowserActionIn(BaseModel):
    action: str
    selector: str = ""
    value: str = ""

class TerminalSessionIn(BaseModel):
    agent_id: int | None = None
    task_id: int | None = None
    mode: Literal["sandbox", "project_host", "system_admin"] = "sandbox"
    cwd: str
    command: str
    network: bool = False

class TerminalStdinIn(BaseModel):
    text: str

class SkillImportIn(BaseModel):
    source_path: str
    overwrite: bool = False

class MCPServerIn(BaseModel):
    name: str
    transport: Literal["stdio", "http"]
    command: list[str] = Field(default_factory=list)
    url: str = ""
    cwd: str = ""
    env_keys: list[str] = Field(default_factory=list)
    enabled: bool = True

class MissionIn(BaseModel):
    name: str
    goal: str
    success_criteria: dict[str, Any] = Field(default_factory=dict)
    stop_conditions: dict[str, Any] = Field(default_factory=dict)
    max_workers: int = 1
    cloud_budget_usd: float = 0.0
    starts_at: datetime | None = None
    ends_at: datetime | None = None

class KPIIn(BaseModel):
    key: str
    label: str
    unit: str = ""
    aggregation: Literal["sum", "set", "max"] = "sum"
    target: float | None = None
