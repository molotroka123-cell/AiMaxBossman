"""Agent spec: `<connector>:<name>[@<model>][?key=value&key=value]` (pure, unit-tested)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl

CONNECTORS = ("mock", "local", "claude", "codex")
MAX_AGENTS = 8
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}$")
_KEY = re.compile(r"^[a-z_]{1,24}$")
RAVE_ID = re.compile(r"^rv-[a-z0-9]{8}$")


class SpecError(ValueError):
    pass


@dataclass(frozen=True)
class AgentSpec:
    connector: str
    name: str
    model: str | None = None
    params: dict[str, str] = field(default_factory=dict)

    @property
    def raw(self) -> str:
        out = f"{self.connector}:{self.name}"
        if self.model:
            out += f"@{self.model}"
        if self.params:
            out += "?" + "&".join(f"{k}={v}" for k, v in sorted(self.params.items()))
        return out

    def to_dict(self) -> dict:
        return {"connector": self.connector, "name": self.name, "model": self.model,
                "params": dict(self.params), "raw": self.raw}


def parse_agent(raw: str) -> AgentSpec:
    text = str(raw or "").strip()
    if ":" not in text:
        raise SpecError(f"агент {text!r}: нужен вид <коннектор>:<имя>, коннекторы: {', '.join(CONNECTORS)}")
    connector, _, rest = text.partition(":")
    connector = connector.strip().lower()
    if connector not in CONNECTORS:
        raise SpecError(f"неизвестный коннектор {connector!r}; доступны: {', '.join(CONNECTORS)}")
    rest, _, query = rest.partition("?")
    name, _, model = rest.partition("@")
    name, model = name.strip(), model.strip() or None
    if not _NAME.match(name):
        raise SpecError(f"имя агента {name!r}: буквы, цифры, _ и -, до 32 символов")
    if model is not None and not _MODEL.match(model):
        raise SpecError(f"модель {model!r} содержит недопустимые символы")
    params: dict[str, str] = {}
    for key, value in parse_qsl(query, keep_blank_values=True):
        if not _KEY.match(key):
            raise SpecError(f"параметр {key!r} агента {name}: только a-z и _")
        params[key] = value[:200]
    return AgentSpec(connector, name, model, params)


def parse_agents(items: list[str]) -> list[AgentSpec]:
    """Comma lists and repeated flags both work: ["mock:a,mock:b", "local:q"]."""
    specs: list[AgentSpec] = []
    for item in items:
        for piece in str(item or "").split(","):
            if piece.strip():
                specs.append(parse_agent(piece))
    if not specs:
        raise SpecError("нужен хотя бы один агент: --agents mock:a,mock:b")
    if len(specs) > MAX_AGENTS:
        raise SpecError(f"не больше {MAX_AGENTS} агентов в одном рейве")
    names = [s.name for s in specs]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise SpecError(f"имена агентов должны различаться: {', '.join(dup)}")
    return specs


def int_param(spec: AgentSpec, key: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(spec.params.get(key, default))
    except (TypeError, ValueError):
        raise SpecError(f"{spec.name}: {key} должен быть целым") from None
    return max(lo, min(hi, value))


def float_param(spec: AgentSpec, key: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(spec.params.get(key, default))
    except (TypeError, ValueError):
        raise SpecError(f"{spec.name}: {key} должен быть числом") from None
    return max(lo, min(hi, value))
