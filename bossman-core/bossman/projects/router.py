"""Маршрутизатор (роль 2): на каждую задачу выбирает инструмент из реестра.

Детерминированный код: сначала жёсткие ограничения (приватность, потолок длины клипа,
разрешение, бюджет, срок), потом качество. Модель зовут только при неоднозначности —
здесь неоднозначность решается просто отказом с объяснением.
"""
from __future__ import annotations

from dataclasses import dataclass

import yaml

from ..config import settings


@dataclass
class Route:
    tool: str
    spec: dict
    reason: str


def load_registry() -> dict:
    return yaml.safe_load(settings.tools_registry.read_text()) or {}


def choose(capability: str, *, private: bool = False, clip_seconds: float = 0.0,
           total_clips: int = 1, budget_left: float | None = None,
           registry: dict | None = None) -> Route:
    reg = registry or load_registry()
    tools: dict = reg.get("tools", {})
    routing: dict = reg.get("routing", {})

    candidates = [(name, spec) for name, spec in tools.items()
                  if capability in (spec.get("can") or [])]
    if not candidates:
        raise LookupError(f"в реестре нет инструмента с умением '{capability}'")

    # жёсткие ограничения
    filtered = []
    for name, spec in candidates:
        limits = spec.get("limits") or {}
        if private and spec.get("where") == "cloud":
            continue  # приватный проект физически не ходит в облако
        if clip_seconds and limits.get("max_clip_s") and clip_seconds > limits["max_clip_s"]:
            continue
        if budget_left is not None and spec.get("where") == "cloud":
            unit = (spec.get("cost") or {}).get("value", 0)
            if clip_seconds and unit * clip_seconds > budget_left:
                continue
        filtered.append((name, spec))
    if not filtered:
        raise LookupError(
            f"'{capability}': все кандидаты отпали по ограничениям "
            f"(private={private}, clip={clip_seconds}s, бюджет={budget_left})")

    # правила по умолчанию: home_always — дома всегда; длинные серии клипов — облако
    home_always = set(routing.get("home_always") or [])
    cloud_when = routing.get("cloud_when") or {}
    prefer_cloud = (capability in (cloud_when.get("capability") or [])
                    and total_clips >= int(cloud_when.get("min_clips", 3))
                    and not private)

    def rank(item: tuple[str, dict]) -> tuple:
        name, spec = item
        at_home = spec.get("where") == "home"
        if capability in home_always:
            place = 0 if at_home else 1
        elif prefer_cloud:
            place = 0 if not at_home else 1
        else:
            place = 0 if at_home else 1          # всё, что можно, — дома
        return (place, -int(spec.get("quality", 0)))

    filtered.sort(key=rank)
    name, spec = filtered[0]
    where = spec.get("where")
    return Route(name, spec,
                 f"{capability} → {name} ({where}; качество {spec.get('quality')}; "
                 f"{'серия клипов — облако' if prefer_cloud and where == 'cloud' else 'дома по умолчанию'})")
