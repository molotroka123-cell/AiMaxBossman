"""Opt-in V4 anchor wiring uses the existing organization database only."""
import pytest
from .conftest import make_settings, start_app


@pytest.mark.parametrize("fleet", [False, True])
async def test_anchor_option_reaches_real_organization_and_fleet_bridge(tmp_path, monkeypatch, fleet):
    for name in ("BOSSMAN_V3_ENABLED", "BOSSMAN_V3_ORGANIZATION", "BOSSMAN_V4_JOURNAL_ANCHOR"):
        monkeypatch.setenv(name, "1")
    monkeypatch.setenv("BOSSMAN_V3_FLEET", "1" if fleet else "0")
    app, svc = await start_app(make_settings(tmp_path), start_workers=False)
    try:
        org = svc.organization
        witness = org.runtime.execution.journal_anchor
        assert witness is not None
        if fleet:
            local = org.fleet.transport._runtimes[org.node_id]
            assert local.journal_anchor is witness
        with org.store._connect() as con:
            assert con.execute("SELECT count(*) FROM org_journal_heads").fetchone()[0] == 0
        assert not list(org.root.glob("*anchor*.sqlite")), "must not create another database"
    finally:
        await svc.stop()


async def test_anchor_option_stays_disabled_by_default(tmp_path, monkeypatch):
    for name in ("BOSSMAN_V3_ENABLED", "BOSSMAN_V3_ORGANIZATION"):
        monkeypatch.setenv(name, "1")
    monkeypatch.delenv("BOSSMAN_V4_JOURNAL_ANCHOR", raising=False)
    monkeypatch.setenv("BOSSMAN_V3_FLEET", "0")
    app, svc = await start_app(make_settings(tmp_path), start_workers=False)
    try:
        assert svc.organization.runtime.execution.journal_anchor is None
    finally:
        await svc.stop()
