"""Projector endpoints: NOT_RUN before the first refresh, refresh persists and the GET returns the same snapshot."""
import asyncio
import json
from types import SimpleNamespace

from bcc.features import projector as TL


class _Req:
    def __init__(self, data_dir):
        self.app = SimpleNamespace(state=SimpleNamespace(svc=SimpleNamespace(settings=SimpleNamespace(data_dir=data_dir))))


def test_projector_not_run_then_refresh_roundtrip(tmp_path, monkeypatch):
    req = _Req(tmp_path)
    first = asyncio.run(TL.projector_latest(req))
    assert first.get("status") in ("NOT_RUN", None) or first.get("evidence_class") == "DEAD_OR_UNWIRED"
    if first.get("evidence_class") == "DEAD_OR_UNWIRED":
        return  # build without bossman-core: honest UNWIRED, nothing else to check

    def fake_refresh(state_dir, fetch=None, *, pump_only=False):
        state_dir.mkdir(parents=True, exist_ok=True)
        run = {"fetched_at": "2026-11-02T04:00:00+00:00", "status": "LIVE", "tokens": [], "paper": {"evidence_class": "SIMULATED"}}
        (state_dir / "latest.json").write_text(json.dumps(run), encoding="utf-8")
        return run

    monkeypatch.setattr(TL, "_load", lambda dotted, sym: fake_refresh if sym == "refresh" else None)
    out = asyncio.run(TL.projector_refresh(req))
    assert out["status"] == "LIVE" and out["available"] is True
    got = asyncio.run(TL.projector_latest(req))
    assert got["fetched_at"] == "2026-11-02T04:00:00+00:00" and got["paper"]["evidence_class"] == "SIMULATED"
