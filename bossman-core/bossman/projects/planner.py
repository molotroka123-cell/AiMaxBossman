"""Планировщик (роль 1): brief.md → plan.yaml.

Сценарий и план уходят пользователю на утверждение ДО любых трат:
статус проекта становится awaiting_approval, запуск — только после POST /approve.
"""
from __future__ import annotations

import yaml

from .. import db
from ..agents import AgentSpec
from ..llm import chat
from .plan import journal_append, load_plan, project_dir, save_plan

PLANNER_PROMPT = """Ты — планировщик проектов Bossman. По brief ниже составь план в YAML.

Формат (строго YAML, без пояснений вокруг):
title: <название>
budget_limit: <число, из brief или 0>
preview_gate_after: 3
stages:
  - name: <этап>
    tasks:
      - id: <t1>
        name: <что сделать>
        tool: <умение из списка: t2v, i2v, frame, reference, tts, subtitles, assemble, qa_clip — маршрутизатор сам выберет инструмент>
        params: {prompt: "...", seconds: 6}
        inputs: []
        outputs: [assets/<файл>]
        check: <критерии проверки результата>
        est_cost: <оценка цены>
        est_minutes: <оценка времени>
        is_clip: true|false
estimate:
  cost: <сумма est_cost>
  hours: <сумма est_minutes / 60>

Правила: клипы ≤ 10–15 секунд; каждый следующий клип стартует с последнего кадра
предыдущего (i2v); раскадровка и стиль-гайд персонажа — отдельные короткие файлы,
которые подаются в каждый промпт генерации дословно.
"""


def make_planner_agent(base: AgentSpec | None = None) -> AgentSpec:
    # локально bossman-smart; сложный сценарий — облако через ask (управляется политикой агента)
    return base or AgentSpec(name="planner", title="Планировщик", model="bossman-smart",
                             cloud_policy="ask")


async def plan_project(slug: str, brief: str, agent: AgentSpec | None = None) -> dict:
    d = project_dir(slug)
    d.mkdir(parents=True, exist_ok=True)
    for sub in ("notes", "assets", "deliverable"):
        (d / sub).mkdir(exist_ok=True)
    (d / "brief.md").write_text(brief)

    planner = make_planner_agent(agent)
    msg = await chat(planner, [
        {"role": "system", "content": PLANNER_PROMPT},
        {"role": "user", "content": brief},
    ], max_tokens=4000)
    text = (msg.get("content") or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    yaml.safe_load(text)  # валидация до записи; кривой YAML — исключение наружу
    save_plan(slug, text)
    plan = load_plan(slug)
    journal_append(slug, f"план составлен: {len(plan.tasks)} задач, "
                         f"оценка {plan.estimate.get('cost', '?')} / {plan.estimate.get('hours', '?')} ч")
    await db.execute(
        """UPDATE projects SET status='awaiting_approval', estimate=$2,
           budget_limit=$3, updated_at=now() WHERE slug=$1""",
        slug, plan.estimate, plan.budget_limit or None)
    return {"tasks": len(plan.tasks), "estimate": plan.estimate}
