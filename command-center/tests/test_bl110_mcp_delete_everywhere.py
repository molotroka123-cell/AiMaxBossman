"""BL-110 — удаление MCP-коннектора должно действовать ВЕЗДЕ, а не в витрине.

Что ломалось. `DELETE /api/mcp/servers/{id}` (кнопка «Удалить» на странице
скиллов, `ui/pages/skills.js:298`) удалял одну строку `mcp_servers`. Строки
`mcp_tools` уходили каскадом, список в интерфейсе пустел — и на этом всё
заканчивалось. В живом процессе оставалось:

  * `mcp:<сервер>:<инструмент>` в `bcc.tools.REGISTRY` — модель продолжала
    видеть инструмент удалённого коннектора в своих схемах и могла его вызвать;
  * решение владельца в `mcp.policy`, в том числе AUTO, — и одноимённый новый
    коннектор молча унаследовал бы его вместо «спроси»;
  * запущенный процесс сервера.

Пара тестов: законное удаление убирает всё (позитив) и повторное добавление
одноимённого коннектора начинает с «спроси», а не с унаследованного AUTO
(негативный контроль — иначе «удалили» проходило бы вырожденно).
"""
from __future__ import annotations

import sys

import sqlalchemy as sa

from bcc.features.tools_mcp import restore_registry
from bcc.tools import REGISTRY, allowed_tools_for
from bcc.v2.tables import mcp_tools as mcp_tools_t

CANONICAL = "mcp:pochta:read_mail"


async def _installed_connector(env) -> int:
    """Коннектор, заведённый штатными ручками продукта, с инструментом в каталоге."""
    created = await env.client.post("/api/mcp/servers", json={
        "name": "pochta", "transport": "stdio",
        "command": [sys.executable, "srv.py"]})
    assert created.status_code == 200, created.text
    server_id = int(created.json()["id"])

    # То, что записывает discovery (`refresh_server`) после tools/list сервера.
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(mcp_tools_t).values(
            server_id=server_id, name="read_mail", description="читает почту",
            input_schema={"properties": {}}, enabled=True))
        await s.commit()

    policy = await env.client.post("/api/mcp/policy",
                                   json={"canonical": CANONICAL, "policy": "auto"})
    assert policy.status_code == 200, policy.text

    assert await restore_registry(env.svc) >= 1
    assert REGISTRY.get(CANONICAL) is not None, "мерить нечего: инструмент не поднялся"
    return server_id


async def test_deleted_connector_leaves_nothing_behind(env):
    server_id = await _installed_connector(env)
    agent = {"id": 1, "tools": ["mcp:*"], "permissions": {}}
    visible = [s["function"]["name"] for s in
               REGISTRY.schemas_for(allowed_tools_for({"id": 1, "meta": {}}, agent))]
    assert "mcp_pochta_read_mail" in visible, "до удаления инструмент обязан быть виден"

    gone = await env.client.delete(f"/api/mcp/servers/{server_id}")
    assert gone.status_code == 200, gone.text
    assert gone.json()["removed_tools"] == 1
    assert gone.json()["removed_policy"] == 1

    # 1. хранилище
    assert (await env.client.get("/api/mcp/servers")).json() == []
    assert (await env.client.get("/api/mcp/tools")).json() == []
    # 2. каталог модели
    assert REGISTRY.get(CANONICAL) is None
    assert [s["function"]["name"] for s in
            REGISTRY.schemas_for(allowed_tools_for({"id": 1, "meta": {}}, agent))] == []
    # 3. политика владельца
    from bcc.features.tools_mcp import _policy
    assert CANONICAL not in await _policy(env.svc)


async def test_reinstalled_connector_does_not_inherit_the_old_auto(env):
    """Негативный контроль: одноимённый коннектор начинает со «спроси»."""
    server_id = await _installed_connector(env)
    assert REGISTRY.get(CANONICAL).default_effect == "auto"
    assert (await env.client.delete(f"/api/mcp/servers/{server_id}")).status_code == 200

    await _reinstall(env)
    spec = REGISTRY.get(CANONICAL)
    assert spec is not None, "переустановленный коннектор обязан подняться"
    assert spec.default_effect == "ask", (
        "унаследованный AUTO удалённого коннектора: владелец не давал его этому")


async def _reinstall(env) -> None:
    created = await env.client.post("/api/mcp/servers", json={
        "name": "pochta", "transport": "stdio",
        "command": [sys.executable, "srv.py"]})
    assert created.status_code == 200, created.text
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(mcp_tools_t).values(
            server_id=int(created.json()["id"]), name="read_mail",
            description="читает почту", input_schema={"properties": {}}, enabled=True))
        await s.commit()
    await restore_registry(env.svc)
