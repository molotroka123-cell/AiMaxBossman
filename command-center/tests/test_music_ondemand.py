"""On-demand ACE-Step start (master prompt 28.10, task 1a): every gate is explicit; nothing starts on its own unless all hold."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from bcc.features import music_ondemand as od
from bcc.features import music_studio as ms


class Fake:
    def __init__(self, *, status="NOT_RUNNING", enforce=True, free=64.0, lease_state="granted", start_ok=True, ready_after=1):
        self.status, self._enforce, self._free, self.lease_state, self.start_ok = status, enforce, free, lease_state, start_ok
        self.ready_after, self.polls, self.calls = ready_after, 0, []

    async def health(self):
        self.polls += 1
        if self.status == "NOT_RUNNING" and "start" in self.calls and self.polls > self.ready_after:
            return {"status": "READY"}
        if self.status == "STARTING" and self.polls > self.ready_after:
            return {"status": "READY"}
        return {"status": self.status, "reason": f"state {self.status}"}

    async def enforce(self):
        self.calls.append("enforce"); return self._enforce

    def free_gb(self):
        self.calls.append("free"); return self._free

    async def lease(self):
        self.calls.append("lease")
        return {"state": self.lease_state, "id": "L1", "token": 7, "reason": "queued behind gemma"} if self.lease_state != "granted" \
            else {"state": "granted", "id": "L1", "token": 7}

    async def release(self, lease):
        self.calls.append("release")

    async def start(self):
        self.calls.append("start")
        return {"ok": self.start_ok, "message": "spawn failed"}


async def _nosleep(_):
    return None


def run(fake):
    return asyncio.run(od.ensure_acestep(None, deps=fake, sleep=_nosleep))


def test_already_running_touches_nothing():
    f = Fake(status="READY")
    assert run(f) == {"state": "running"} and f.calls == []


def test_full_path_leases_then_starts_then_waits_for_ready():
    f = Fake()
    r = run(f)
    assert r["state"] == "started" and r["lease_id"] == "L1"
    assert f.calls[:4] == ["enforce", "free", "lease", "start"] and "release" not in f.calls


def test_enforce_false_never_starts_anything():
    f = Fake(enforce=False)
    r = run(f)
    assert r["state"] == "skipped" and "enforce=false" in r["reason"] and f.calls == ["enforce"]


@pytest.mark.parametrize("free", [8.0, 23.9, None])
def test_not_enough_or_unknown_memory_is_refused_before_any_lease(free):
    f = Fake(free=free)
    r = run(f)
    assert r["state"] == "refused" and "lease" not in f.calls and "start" not in f.calls


@pytest.mark.parametrize("state", ["queued", "refused"])
def test_a_lease_that_is_not_granted_starts_nothing(state):
    f = Fake(lease_state=state)
    r = run(f)
    assert r["state"] == "refused" and r["reason"] == "queued behind gemma" and "start" not in f.calls


def test_failed_start_releases_the_lease():
    f = Fake(start_ok=False)
    r = run(f)
    assert r["state"] == "failed" and r["lease"] == "released" and f.calls[-1] == "release"


def test_never_ready_times_out_and_releases(monkeypatch):
    monkeypatch.setattr(od, "READY_TIMEOUT_S", 0.0)
    f = Fake(ready_after=10**9)
    r = run(f)
    assert r["state"] == "failed" and "не стал готов" in r["reason"] and f.calls[-1] == "release"


def test_not_installed_is_skipped_without_policy_or_lease():
    f = Fake(status="NOT_INSTALLED")
    r = run(f)
    assert r["state"] == "skipped" and f.calls == []


def test_a_start_already_in_progress_is_awaited_without_a_second_lease():
    f = Fake(status="STARTING", ready_after=2)
    r = run(f)
    assert r["state"] == "started" and "lease" not in f.calls and "start" not in f.calls


# --- end to end through POST /generate ------------------------------------------------------------------------------

class _Req:
    def __init__(self):
        self.app = SimpleNamespace(state=SimpleNamespace(svc=SimpleNamespace(bus=SimpleNamespace(emit=self._emit))))
        self.emitted = []

    async def _emit(self, *a, **k):
        self.emitted.append(a)


def _body():
    return ms.MusicIn(prompt="phonk drift")


def test_generate_retries_once_after_an_auto_start(monkeypatch):
    attempts = {"n": 0}

    async def fake_json(client, method, path, **kw):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx.ConnectError("refused")
        return {"task_id": "t-1"}

    async def fake_ensure(request):
        return {"state": "started", "lease_id": "L1"}

    monkeypatch.setattr(ms, "_json", fake_json)
    monkeypatch.setattr("bcc.features.music_ondemand.ensure_acestep", fake_ensure)
    out = asyncio.run(ms.generate(_body(), _Req()))
    assert out["task_id"] == "t-1" and out["auto_start"]["state"] == "started" and attempts["n"] == 2


def test_generate_reports_the_auto_start_refusal_in_the_503(monkeypatch):
    async def fake_json(client, method, path, **kw):
        raise httpx.ConnectError("refused")

    async def fake_ensure(request):
        return {"state": "refused", "reason": "свободно 8.0 ГБ памяти, нужно ≥ 24 ГБ"}

    async def fake_unavailable(request, exc):
        return HTTPException(503, {"message": "ACE-Step не запущен", "code": "MUSIC_NOT_RUNNING", "hint": "запустите"})

    monkeypatch.setattr(ms, "_json", fake_json)
    monkeypatch.setattr(ms, "_unavailable", fake_unavailable)
    monkeypatch.setattr("bcc.features.music_ondemand.ensure_acestep", fake_ensure)
    with pytest.raises(HTTPException) as e:
        asyncio.run(ms.generate(_body(), _Req()))
    assert e.value.status_code == 503 and e.value.detail["auto_start"]["state"] == "refused"


def test_generate_without_failure_never_calls_the_auto_start(monkeypatch):
    async def fake_json(client, method, path, **kw):
        return {"task_id": "t-2"}

    async def boom(request):
        raise AssertionError("auto start must not run when ACE-Step answered")

    monkeypatch.setattr(ms, "_json", fake_json)
    monkeypatch.setattr("bcc.features.music_ondemand.ensure_acestep", boom)
    out = asyncio.run(ms.generate(_body(), _Req()))
    assert out == {"task_id": "t-2", "status": "queued", "provider": ms.PROVIDER}
