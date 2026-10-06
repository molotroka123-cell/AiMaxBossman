"""Командная строка: значение параметра пути не может увести запрос на чужой маршрут.

`_fill_path` экранирует «/», но значения «.» и «..» проходили как есть, а httpx
схлопывает такие сегменты (RFC 3986 remove_dot_segments) ещё до отправки:
«tasks.action . .» уходил POST-ом на /api/tasks, «tasks.get .» читал список
всех задач — владельцу показано одно намерение, исполнялся другой маршрут.
"""
from __future__ import annotations

import httpx
import pytest

from bcc.features import command_bar as cb

from .test_command_bar import _flag_on, _make_task  # noqa: F401 — флаг командной строки включён


def _sent_path(cap: cb.Capability, args: dict) -> str:
    """Путь, который httpx реально отправит (после нормализации URL)."""
    return httpx.Request(cap.method, "http://127.0.0.1" + cb._fill_path(cap, args)).url.path


@pytest.mark.parametrize("value", [".", "..", "%2E", "%2e%2E"])
def test_dot_values_stay_one_segment(value):
    cap = cb.Capability(id="tasks.action", method="POST", path="/api/tasks/{task_id}/{action}",
                        title="t", group="tasks", path_params=("task_id", "action"), mutates=True)
    sent = _sent_path(cap, {"task_id": value, "action": value})
    assert sent.startswith("/api/tasks/") and sent.count("/") == 4, sent


async def test_no_capability_can_be_redirected_by_dot_segments(env):
    caps = cb.catalog_for(env.app)
    escaped = []
    for cap in caps.values():
        if not cap.path_params:
            continue
        for value in (".", ".."):
            sent = _sent_path(cap, {p: value for p in cap.path_params})
            if sent.count("/") != cap.path.count("/"):
                escaped.append(f"{cap.id} [{value}] -> {sent}")
    assert escaped == [], escaped[:10]


async def test_dot_task_id_is_not_answered_by_the_task_list(env):
    """«tasks.get .» обязан спросить задачу «.» (и получить отказ), а не прочитать
    GET /api/tasks — список всех задач под видом одной."""
    from .conftest import wait_for
    await _make_task(env)
    res = await env.client.post("/api/command-bar/run", json={"text": "tasks.get ."})
    assert res.status_code == 200, res.text
    task_id = res.json()["task"]["id"]

    async def finished():
        row = (await env.client.get(f"/api/command-bar/tasks/{task_id}")).json()["task"]
        return row if row["state"] in ("done", "failed", "stopped") else None

    row = await wait_for(finished, timeout=15.0)
    result = row.get("result") or {}
    assert not isinstance(result.get("body"), list), "ответил маршрут списка задач"
    assert result.get("status") != 200, row
