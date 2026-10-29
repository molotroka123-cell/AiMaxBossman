"""TZ 2.5 T3: Direct Gen jobs hold a governor memory lease (when the owner's policy enforces it)."""
import asyncio
from types import SimpleNamespace

import pytest

from bcc.direct_gen.service import DirectGenError, _JobGate
from bcc.features import resource_leases as rl


class FakeLedger:
    def __init__(self, states):
        self.states = list(states)            # successive states the lease goes through
        self.calls = []

    def request(self, consumer, priority, holder="", **kw):
        self.calls.append(("request", consumer, priority, kw.get("idempotency_key")))
        return {"id": "a" * 32, "token": 7, "state": self.states.pop(0), "reason": "ждёт память"}

    def get(self, lease_id):
        self.calls.append(("get", lease_id))
        return {"state": self.states.pop(0) if self.states else "granted"}

    def heartbeat(self, lease_id, token):
        self.calls.append(("heartbeat", lease_id, token))
        return {"ok": True}

    def release(self, lease_id, token=None, force=False, reason=""):
        self.calls.append(("release", lease_id, token))
        return {"ok": True}


def _svc(ledger):
    return SimpleNamespace(resource_leases=ledger)


async def _on():
    return True


async def _off():
    return False


@pytest.mark.parametrize("model,consumer", [("flux1-schnell", "sdcli_image"), ("qwen-image-2.1-uc-edit", "sdcli_image"),
                                            ("ltx-video", "ltx25_sdcli"), ("wan2.2-animate-14b", "wangp_animate"),
                                            ("wan21-vace-14b", "wangp_vace"), ("wan2.2-ti2v-5b", "wan_video")])
def test_every_direct_gen_model_maps_to_a_governor_profile(model, consumer):
    assert rl.consumer_for_job({"model": model}) == consumer


def test_seed_profiles_exist_for_every_mapped_consumer():
    mod = rl._load_module()
    for c in ("sdcli_image", "ltx25_sdcli", "wangp_animate", "wangp_vace", "wan_video", "faceswap"):
        assert c in mod.SEED_CONSUMERS and mod.SEED_CONSUMERS[c].p95_peak_gb, c


def test_granted_lease_is_held_for_the_job_and_released_after():
    led = FakeLedger(["granted"])
    job = {"job_id": "j1", "model": "flux1-schnell"}

    async def go():
        async with rl._JobLease(_svc(led), job, enforce=_on):
            assert job["lease"]["state"] == "granted"
    asyncio.run(go())
    assert led.calls[0] == ("request", "sdcli_image", "background", "direct-gen:j1")
    assert led.calls[-1][0] == "release" and job["lease"]["state"] == "released"


def test_queued_lease_waits_with_heartbeats_then_runs():
    led = FakeLedger(["queued", "queued", "granted"])
    job = {"job_id": "j2", "model": "wan2.2-ti2v-5b"}

    async def go():
        async with rl._JobLease(_svc(led), job, enforce=_on, poll_s=0.01):
            return job["lease"]["state"]
    assert asyncio.run(go()) == "granted"
    assert [c[0] for c in led.calls].count("heartbeat") >= 2


@pytest.mark.parametrize("states", [["refused"], ["queued"] * 50])
def test_refused_or_never_granted_lease_fails_the_job_without_running(states):
    led = FakeLedger(states)
    ran = []

    async def go():
        async with rl._JobLease(_svc(led), {"job_id": "j3", "model": "ltx-video"}, enforce=_on, wait_s=0.05, poll_s=0.01):
            ran.append(1)
    with pytest.raises(DirectGenError) as e:
        asyncio.run(go())
    assert e.value.code == "resource_lease_refused" and ran == []


def test_policy_not_enforced_means_no_lease_at_all():
    led = FakeLedger([])

    async def go():
        async with rl._JobLease(_svc(led), {"job_id": "j4", "model": "flux1-schnell"}, enforce=_off):
            pass
    asyncio.run(go())
    assert led.calls == []


def test_gpu_lock_is_not_kept_when_the_lease_is_refused():
    lock = asyncio.Lock()
    led = FakeLedger(["refused"])

    async def go():
        with pytest.raises(DirectGenError):
            async with _JobGate(lock, rl._JobLease(_svc(led), {"job_id": "j5", "model": "flux1-schnell"}, enforce=_on)):
                pass
        assert not lock.locked()
        async with _JobGate(lock, None):            # the next job still gets the GPU
            assert lock.locked()
    asyncio.run(go())


# ------------------------------------------------------------------ T3: memory meter

def _load_measure():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("measure", Path(__file__).resolve().parents[2] / "tools" / "resource_governor" / "measure.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_peak_sampler_counts_only_the_rise_above_the_baseline():
    m = _load_measure()
    readings = iter([40.0, 41.0, 63.5, 52.0] + [45.0] * 100)
    with m.PeakSampler(read=lambda: next(readings), every_s=0.001) as s:
        import time as _t
        _t.sleep(0.05)
    assert s.delta_gb == 23.5


def test_meter_records_every_completed_run_with_config_hash_and_cold_first():
    m = _load_measure()
    posted, n = [], {"i": 0}

    def api(method, path, body=None, **_):
        if path == "/api/direct-gen/jobs" and method == "POST":
            n["i"] += 1
            return {"job_id": f"j{n['i']}"}
        if path == "/api/direct-gen/jobs":
            return {"jobs": [{"job_id": f"j{n['i']}", "status": "failed" if n["i"] == 3 else "completed"}]}
        posted.append(body)
        return {"ok": True}

    class S:
        def __init__(self):
            self.delta_gb = 20.0 + n["i"]
        def __enter__(self):
            return self
        def __exit__(self, *e):
            return False
    res = m.measure(api, "sdcli_image", "flux1-schnell", "768x768", 4, 4, sampler_factory=S)
    assert res["completed"] == 3 and len(posted) == 3                      # the failed run is not a measurement
    assert posted[0]["cold"] is True and all(p["cold"] is False for p in posted[1:])
    assert {p["config_hash"] for p in posted} == {m.config_hash("flux1-schnell", "768x768", 4)}
    assert res["p95_gb"] == max(p["peak_gb"] for p in posted)
