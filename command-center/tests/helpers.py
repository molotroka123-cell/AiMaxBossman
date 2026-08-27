"""Короткие помощники для тестов: создать провайдера, модель, агента и задачу через API."""
from __future__ import annotations

import httpx


async def make_stack(client: httpx.AsyncClient, *, max_steps: int = 1,
                     max_retries: int = 2, prompt: str = "посчитай 2+2") -> dict:
    """Провайдер → модель → агент → задача (run_now). Возвращает id всех сущностей."""
    provider = (await client.post("/api/providers", json={
        "name": "локальный", "kind": "openai_compat",
        "base_url": "http://127.0.0.1:8080/v1", "api_key": "sk-test-abcd"})).json()
    model = (await client.post("/api/models", json={
        "provider_id": provider["id"], "name": "local-7b", "alias": "local-7b"})).json()
    agent = (await client.post("/api/agents", json={
        "name": "аналитик", "system_prompt": "отвечай коротко",
        "model_id": model["id"], "max_steps": max_steps})).json()
    task = (await client.post("/api/tasks", json={
        "title": "проверка", "prompt": prompt, "agent_id": agent["id"],
        "run_now": True, "max_retries": max_retries})).json()["task"]
    return {"provider": provider, "model": model, "agent": agent, "task": task}
