"""Агент = папка: agent.yaml (модель, политика облака, инструменты, права),
prompt.md (кто он), memory.md (его собственные заметки, правки видны в git)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import settings

CLOUD_POLICIES = ("never", "ask", "allowed")

# ComputerUse (ЭТАП 1): общая способность управлять браузером выдаётся КАЖДОМУ
# агенту по умолчанию — это не набор интеграций под сайты, а одна возможность.
# Агент отказывается от неё одной строкой `computer_use: false` в agent.yaml.
#
# confirm=None — как объявлено в самом инструменте; confirm=True — действие
# всегда идёт через approval рантайма (runner паркует задачу в waiting_approval).
# Пара confirmed_* существует именно для этого: обычный click/press не должен
# незаметно подтверждать платёж или отправку — для этого есть confirmed_click.
BASE_COMPUTER_USE_TOOLS: tuple[tuple[str, bool | None], ...] = (
    ("browser.open", None), ("browser.observe", None), ("browser.frames", None),
    ("browser.tabs", None), ("browser.tab_switch", None), ("browser.tab_close", None),
    ("browser.extract", None), ("browser.screenshot", None), ("browser.vision", None),
    ("browser.wait", None), ("browser.click", None), ("browser.confirmed_click", True),
    ("browser.type", None), ("browser.press", None), ("browser.confirmed_press", True),
    ("browser.select", None), ("browser.scroll", None), ("browser.hover", None),
    ("browser.upload", None), ("browser.download", None), ("browser.checkpoint", None),
    ("browser.close", None),
)


@dataclass
class ToolGrant:
    name: str
    confirm: bool | None = None  # None = как объявлено в самом инструменте


def _merge_computer_use_tools(grants: list[ToolGrant], enabled: bool) -> list[ToolGrant]:
    """Домешать браузерные гранты к тем, что агент объявил сам.

    Ручная настройка агента побеждает: если агент уже назвал `browser.click`
    (например, снял с него confirm или, наоборот, поставил), его запись
    остаётся, дефолт не перетирает её. Так владелец может ужесточить или
    ослабить конкретный инструмент, не теряя остальную способность.
    """
    if not enabled:
        return grants
    existing = {g.name for g in grants}
    merged = list(grants)
    for name, confirm in BASE_COMPUTER_USE_TOOLS:
        if name not in existing:
            merged.append(ToolGrant(name, confirm=confirm))
    return merged


@dataclass
class AgentSpec:
    name: str
    title: str
    model: str                    # только алиас из LiteLLM, никогда имя файла
    cloud_policy: str = "never"
    tools: list[ToolGrant] = field(default_factory=list)
    max_steps: int = 30
    max_tokens: int = 200_000
    timeout_min: int = 20
    schedule: str | None = None
    api_key: str = ""             # LiteLLM-ключ агента (из окружения LITELLM_KEY_<NAME>)
    path: Path | None = None

    def grant(self, tool_name: str) -> ToolGrant | None:
        for g in self.tools:
            if g.name == tool_name:
                return g
        return None

    @property
    def prompt(self) -> str:
        p = (self.path / "prompt.md") if self.path else None
        return p.read_text() if p and p.exists() else ""

    @property
    def memory(self) -> str:
        p = (self.path / "memory.md") if self.path else None
        return p.read_text() if p and p.exists() else ""


def _parse_tools(raw: list) -> list[ToolGrant]:
    # в yaml инструмент — строка ("gmail.read") или пара ("gmail.send: confirm")
    grants: list[ToolGrant] = []
    for item in raw or []:
        if isinstance(item, str):
            grants.append(ToolGrant(item))
        elif isinstance(item, dict):
            for name, mode in item.items():
                grants.append(ToolGrant(name, confirm=(str(mode).strip() == "confirm")))
    return grants


def load_agent(path: Path) -> AgentSpec:
    cfg = yaml.safe_load((path / "agent.yaml").read_text())
    limits = cfg.get("limits") or {}
    policy = cfg.get("cloud_policy", "never")
    if policy not in CLOUD_POLICIES:
        raise ValueError(f"{path.name}: неизвестная cloud_policy '{policy}'")
    import os
    key_env = f"LITELLM_KEY_{cfg['name'].upper().replace('-', '_')}"
    # ComputerUse включён по умолчанию; агент отключает его `computer_use: false`.
    computer_use = cfg.get("computer_use", True) is not False
    tools = _merge_computer_use_tools(_parse_tools(cfg.get("tools")), computer_use)
    return AgentSpec(
        name=cfg["name"],
        title=cfg.get("title", cfg["name"]),
        model=cfg["model"],
        cloud_policy=policy,
        tools=tools,
        max_steps=int(limits.get("max_steps", 30)),
        max_tokens=int(limits.get("max_tokens", 200_000)),
        timeout_min=int(limits.get("timeout_min", 20)),
        schedule=cfg.get("schedule"),
        api_key=os.environ.get(key_env, ""),
        path=path,
    )


def load_all(agents_dir: Path | None = None) -> dict[str, AgentSpec]:
    root = agents_dir or settings.agents_dir
    agents: dict[str, AgentSpec] = {}
    if root.exists():
        for d in sorted(root.iterdir()):
            if (d / "agent.yaml").exists():
                spec = load_agent(d)
                agents[spec.name] = spec
    return agents


def set_cloud_policy(name: str, policy: str, agents_dir: Path | None = None) -> AgentSpec:
    """PATCH /agents/{name}: переключатель never/ask/allowed пишется прямо в agent.yaml —
    папка агента в git, изменение видно как diff."""
    if policy not in CLOUD_POLICIES:
        raise ValueError(f"cloud_policy должна быть одной из {CLOUD_POLICIES}")
    root = agents_dir or settings.agents_dir
    path = root / name / "agent.yaml"
    cfg = yaml.safe_load(path.read_text())
    cfg["cloud_policy"] = policy
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
    return load_agent(root / name)
