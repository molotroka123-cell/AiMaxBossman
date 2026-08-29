"""Файловое состояние проекта: plan.yaml + state.json.

state.json — источник истины (9.5): перезагрузка, падение API, «стоп» —
проект продолжается с первой незавершённой задачи; готовое не переделывается.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ..config import settings


@dataclass
class PlanTask:
    id: str
    name: str
    tool: str
    stage: str
    params: dict = field(default_factory=dict)
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    check: str = ""              # критерии проверки для Проверяющего
    est_cost: float = 0.0
    est_minutes: float = 0.0
    is_clip: bool = False        # участвует в превью-гейте и проверке стыков


@dataclass
class Plan:
    title: str
    tasks: list[PlanTask]
    budget_limit: float = 0.0
    preview_gate_after: int = 3  # после первых трёх клипов — стоп и показ (9.5)
    estimate: dict = field(default_factory=dict)


def project_dir(slug: str) -> Path:
    return settings.projects_dir / slug


def load_plan(slug: str) -> Plan:
    raw = yaml.safe_load((project_dir(slug) / "plan.yaml").read_text())
    tasks = [PlanTask(stage=st["name"], **{k: v for k, v in t.items()})
             for st in raw["stages"] for t in st["tasks"]]
    return Plan(title=raw.get("title", slug), tasks=tasks,
                budget_limit=float(raw.get("budget_limit", 0)),
                preview_gate_after=int(raw.get("preview_gate_after", 3)),
                estimate=raw.get("estimate") or {})


def save_plan(slug: str, plan_yaml: str) -> None:
    d = project_dir(slug)
    d.mkdir(parents=True, exist_ok=True)
    (d / "plan.yaml").write_text(plan_yaml)


class State:
    """Обёртка над state.json: каждая правка сразу на диске."""

    def __init__(self, slug: str):
        self.slug = slug
        self.path = project_dir(slug) / "state.json"
        self.data: dict = {"status": "draft", "tasks": {}, "spent": 0.0,
                           "clips_done": 0, "preview_gate_passed": False}
        if self.path.exists():
            self.data = json.loads(self.path.read_text())

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1))

    def task(self, task_id: str) -> dict:
        return self.data["tasks"].setdefault(
            task_id, {"status": "pending", "attempts": 0, "spent": 0.0, "artifacts": []})

    def is_done(self, task_id: str) -> bool:
        return self.task(task_id)["status"] == "done"

    def mark(self, task_id: str, status: str, *, cost: float = 0.0,
             artifacts: list[str] | None = None) -> None:
        t = self.task(task_id)
        t["status"] = status
        t["spent"] = t.get("spent", 0.0) + cost
        if artifacts:
            t["artifacts"] = artifacts
        self.data["spent"] = round(self.data.get("spent", 0.0) + cost, 4)
        self.save()


def journal_append(slug: str, text: str) -> None:
    p = project_dir(slug) / "journal.md"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    with p.open("a") as f:
        f.write(f"- {ts} {text.strip()}\n")


def journal_tail(slug: str, lines: int = 20) -> str:
    p = project_dir(slug) / "journal.md"
    if not p.exists():
        return ""
    return "\n".join(p.read_text().splitlines()[-lines:])
