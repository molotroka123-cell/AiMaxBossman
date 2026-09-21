"""AP-ALL (owner audit 2026-09-21, P2): `?status=all` возвращал [] при живых строках.

Контракт: all/пусто = все состояния; список через запятую; неизвестное — 422.
Проверяется ПОСЛЕ рестарта сервиса на той же базе, и попутно — что anti-replay
и привязка к preview не ослабли.
"""
from __future__ import annotations

from .conftest import client_for, make_settings, start_app


async def _seed(client) -> dict[str, int]:
    ids = {}
    for name in ("pending", "approved", "rejected", "consumed", "revoked"):
        row = (await client.post("/api/approvals", json={"kind": "browser",
                                                         "preview": f"AP-ALL {name}"})).json()
        ids[name] = row["id"]
    await client.post(f"/api/approvals/{ids['approved']}", json={"approve": True})
    await client.post(f"/api/approvals/{ids['rejected']}", json={"approve": False})
    await client.post(f"/api/approvals/{ids['consumed']}", json={"approve": True})
    await client.post(f"/api/approvals/{ids['revoked']}", json={"approve": True})
    await client.post(f"/api/approvals/{ids['revoked']}/revoke", json={})
    return ids


async def test_status_all_returns_every_state_across_restart(tmp_path):
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    try:
        async with client_for(app, svc) as client:
            ids = await _seed(client)
            assert await svc.approvals.consume(ids["consumed"], kind="browser",
                                               preview="AP-ALL consumed")
    finally:
        await svc.stop()

    # рестарт: новый процесс-эквивалент на той же базе
    app, svc = await start_app(settings, start_workers=False)
    try:
        async with client_for(app, svc) as client:
            def by_id(rows):
                return {r["id"]: r["status"] for r in rows}

            default = by_id((await client.get("/api/approvals")).json())
            pending = by_id((await client.get("/api/approvals?status=pending")).json())
            everything = by_id((await client.get("/api/approvals?status=all")).json())
            empty = by_id((await client.get("/api/approvals?status=")).json())
            pair = by_id((await client.get("/api/approvals?status=approved,rejected")).json())

            assert default == pending == {ids["pending"]: "pending"}
            assert everything == empty == {ids[k]: k for k in ids}, everything
            assert pair == {ids["approved"]: "approved", ids["rejected"]: "rejected"}
            assert by_id((await client.get("/api/approvals?status=ALL")).json()) == everything

            bad = await client.get("/api/approvals?status=bogus")
            assert bad.status_code == 422
            assert "bogus" in bad.json()["error"]["message"]

            # anti-replay и привязка к preview после рестарта не ослабли
            assert not await svc.approvals.consume(ids["consumed"], kind="browser",
                                                   preview="AP-ALL consumed")
            assert not await svc.approvals.consume(ids["approved"], kind="browser",
                                                   preview="другое действие")
            assert await svc.approvals.consume(ids["approved"], kind="browser",
                                               preview="AP-ALL approved")
            assert not await svc.approvals.consume(ids["approved"], kind="browser",
                                                   preview="AP-ALL approved")
            assert not await svc.approvals.consume(ids["revoked"], kind="browser",
                                                   preview="AP-ALL revoked")
    finally:
        await svc.stop()
