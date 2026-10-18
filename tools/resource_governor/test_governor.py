"""Decision-logic tests for the resource governor: fake job lists, fake IO, no network, no real processes.
Run: python -m pytest tools/resource_governor/test_governor.py -q"""
from __future__ import annotations

import copy
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import governor as g  # noqa: E402

NOW = 1_800_000_000.0
FREE = "nvidia/nemotron-3-ultra-550b-a55b:free"
EXP = ("deepseek-v4.1-flash-1017", "deepseek-v4.1-flash-uncensored")
OR_ONLY = {"cloud_free_routes": [{"kind": "openrouter", "models": [
    "venice/uncensored:free", FREE, "google/gemma-4-31b-it:free"]}]}


def iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(t))


def job(model="wan2.2-ti2v-5b", status="completed", age_s=3600.0, ended_ago=None, weights=None):
    w = weights if weights is not None else [
        {"role": "diffusion", "found": True, "file": f"{model}.gguf", "size_bytes": 5_000_000_000},
        {"role": "vae", "found": True, "file": "vae.safetensors", "size_bytes": 1_000_000_000}]
    created = NOW - age_s
    return {"job_id": f"{model}-{age_s}", "model": model, "status": status, "created_at": iso(created),
            "started_at": iso(created + 5),
            "finished_at": iso(NOW - ended_ago) if ended_ago is not None else None,
            "provenance": {"model_id": model, "weights": w}}


class FakeIO:
    def __init__(self):
        self.t = NOW
        self.jobs: list[dict] | None = []
        self.ollama = []
        self.calls: list[tuple] = []
        self.jeff_up = False
        self.hold: str | None = None
        self.catalog = {FREE: ("0", "0"), "paid/model:free": ("0.001", "0"), "google/gemma-4-31b-it:free": (0, 0)}
        self.avail = 60_000_000_000
        self.procs = []
        self.index = {"wan2.2-ti2v-5b.gguf": [("C:/m/wan.gguf", 5_000_000_000)],
                      "vae.safetensors": [("C:/m/vae.safetensors", 1_000_000_000)]}
        self.warm_hook = None
        self.flag = None
        self.flag_provider = None
        self.violation = None
        self.rows = [
            {"name": "deepseek-v4.1-flash-1017", "alias": "explabs:deepseek-v4.1-flash-1017", "price_in": 0.0,
             "price_out": 0.0, "pricing_known": 1, "status": "online"},
            {"name": "deepseek-v4.1-flash-uncensored", "alias": "explabs:deepseek-v4.1-flash-alt", "price_in": 0.0,
             "price_out": 0.0, "pricing_known": 1, "status": "online"},
            {"name": "gemini-4-argon", "alias": "gemini-4-argon", "price_in": 2.0, "price_out": 10.0,
             "pricing_known": 1, "status": "online"},
        ]

    # time / mem
    def now(self): return self.t
    def mem(self): return {"total": 128_000_000_000, "available": self.avail, "cache": 1_000_000_000}

    # backend / ollama
    def fetch_jobs(self): return copy.deepcopy(self.jobs) if self.jobs is not None else None
    def ollama_ps(self): return list(self.ollama)

    def ollama_unload(self, name):
        self.calls.append(("unload", name))
        self.ollama.remove(name)
        return True

    def free_catalog(self): return self.catalog
    def provider_rows(self, provider_id): return self.rows
    def violation_read(self): return self.violation

    def violation_clear(self):
        self.calls.append(("violation_clear",))
        self.violation = None

    # jeff
    def hold_read(self): return self.hold
    def jeff_running(self): return self.jeff_up

    def jeff_stop(self, hold):
        self.calls.append(("jeff_stop", hold))
        self.jeff_up = False
        if hold:
            self.hold = "HOLD@%d" % len(self.calls)
        return True

    def jeff_launch(self):
        self.calls.append(("jeff_launch", self.flag is not None))   # records whether the cloud flag existed at launch
        self.jeff_up = True
        self.hold = None
        return True

    def cloud_flag_write(self, route):
        self.calls.append(("flag_write", tuple(route["models"])))
        self.flag = tuple(route["models"])
        self.flag_provider = route.get("provider_id")

    def cloud_flag_remove(self):
        self.calls.append(("flag_remove",))
        self.flag = None

    # processes
    def list_depri_candidates(self): return list(self.procs)
    def get_ionice(self, pid): return 2

    def boost_sdcli(self):
        self.calls.append(("boost",))
        return []

    def set_priority(self, pid, ct, nice, ionice):
        self.calls.append(("prio", pid, nice, ionice))
        return True

    # warm
    def build_index(self): return self.index
    probe_speed = 5000.0

    def probe_file(self, path, mb):
        self.calls.append(("probe", Path(path).name))
        return self.probe_speed

    def warm_file(self, path, allow, chunk_mb, hint):
        self.calls.append(("warm", Path(path).name))
        if self.warm_hook:
            self.warm_hook()
        ok = allow()
        return {"bytes": 0 if not ok else 5_000_000_000, "secs": 1.0, "mb_s": 5000.0, "aborted": not ok}


def cfg(**over):
    c = g._merge(g.DEFAULT_CONFIG, {"jeff_during_generation": "pause", "dry_run": False, "resume_delay_s": 0, "restore_owner_hold": True, "warm": {"hot_models": []}})
    return g._merge(c, over)


def names(io, kind): return [c for c in io.calls if c[0] == kind]


def run_warm_sync(gov):
    """Start the warm worker (if the decision allows) and wait for it."""
    gov.tick()
    if gov.warm_thread:
        gov.warm_thread.join(5)


# ---------------------------------------------------------------------------------------------- pure helpers
def test_warm_allowed_rules():
    idle = [job(ended_ago=600)]
    assert g.warm_allowed(None, NOW, 0, 60)[0] is False                       # unknown state
    assert g.warm_allowed([job(status="generating")], NOW, 0, 60)[0] is False
    assert g.warm_allowed([job(status="queued")], NOW, 0, 60)[0] is False     # queued counts as active
    assert g.warm_allowed(idle, NOW, NOW - 30, 60)[0] is False                # we saw a live job 30 s ago
    assert g.warm_allowed([job(ended_ago=20)], NOW, 0, 60)[0] is False        # finished 20 s ago
    assert g.warm_allowed(idle, NOW, NOW - 120, 60)[0] is True


def test_rank_models_by_recent_usage_and_plan_shares_files_once():
    jobs = [job("a", age_s=3600), job("a", age_s=7200), job("b", age_s=3600), job("old", age_s=20 * 86400),
            job("c", status="cancelled"), job("nowts", weights=[])]
    ranked = g.rank_models(jobs, NOW)
    assert [m for m, _ in ranked] == ["a", "b"]                                # old/cancelled/no-weights excluded
    idx = {"a.gguf": [("C:/a.gguf", 5_000_000_000)], "b.gguf": [("C:/b.gguf", 5_000_000_000)],
           "vae.safetensors": [("C:/vae", 1_000_000_000)]}
    plan = g.plan_files(jobs, ranked, idx, 2)
    assert [p["model"] for p in plan] == ["a", "b"]
    assert [f["path"] for f in plan[1]["files"]] == ["C:/b.gguf"]              # shared vae only under the first model
    assert g.plan_files(jobs, ranked, idx, 1)[0]["model"] == "a" and len(g.plan_files(jobs, ranked, idx, 1)) == 1
    wrong_size = {"a.gguf": [("C:/a.gguf", 1)]}
    assert g.plan_files(jobs, ranked, wrong_size, 2) == []                     # size must match the journal


def test_budget_ok():
    assert g.budget_ok(60e9, 5e9, 30e9, 0, 40e9)[0] is True
    assert g.budget_ok(34e9, 5e9, 30e9, 0, 40e9)[0] is False                   # would leave < 30 GB
    assert g.budget_ok(90e9, 5e9, 30e9, 38e9, 40e9)[0] is False                # total cap


@pytest.mark.parametrize("name,cmd,cwd,normal,expected", [
    ("python.exe", "python.exe tools/browser_contracts.py --dry-run x", "C:\\Users\\asd\\Bossman\\wt-x", True, True),
    ("python.exe", "python.exe -X utf8 -m bcc --host 127.0.0.1 --port 8801", "C:\\Users\\asd\\Bossman\\wt-x", True, False),
    ("python.exe", "python.exe -X utf8 -m bossmobile serve --port 8840", "C:\\Users\\asd\\Bossman\\x", True, False),
    ("python.exe", "python.exe -m bcc.pit.cli watch --data-dir x", "C:\\Users\\asd\\Bossman\\x", True, False),
    ("python.exe", "python.exe -m bcc.telegram_companion --config c", "C:\\Users\\asd\\Bossman\\x", True, False),
    ("python.exe", "python.exe tools/resource_governor/governor.py run", "C:\\Users\\asd\\Bossman\\x", True, False),
    ("python.exe", "python.exe other.py", "C:\\Users\\asd\\Documents", True, False),         # not Bossman
    ("node.exe", "node Bossman\\x.js", "C:\\Users\\asd\\Bossman", True, False),            # not python
    ("python.exe", "python.exe tools/x.py", "C:\\Users\\asd\\Bossman\\wt", False, False),    # already not Normal
    ("sd-cli.exe", "sd-cli.exe -M vid_gen", "C:\\Users\\asd\\Bossman", True, False),
    ("python.exe", "C:\\Users\\asd\\AppData\\Local\\OpenAI\\Codex\\x\\python.exe Bossman\\a.py", "", True, False),
])
def test_depri_candidates(name, cmd, cwd, normal, expected):
    assert g.is_depri_candidate(name, cmd, cwd, normal) is expected


def test_cloud_free_pick_needs_live_zero_price_free_id_and_not_banned():
    cat = {FREE: ("0", "0"), "paid/m:free": ("0.1", "0"), "liquid/lfm-2.5-2.6b:free": ("0", "0"), "x/y": ("0", "0"),
           "bad/m:free": ("n/a", "0")}
    conf = [FREE, "paid/m:free", "liquid/lfm-2.5-2.6b:free", "x/y", "missing:free", "bad/m:free", FREE]
    assert g.cloud_free_pick(conf, cat) == [FREE]
    assert g.cloud_free_pick(conf, None) == []


# ---------------------------------------------------------------------------------------- guard: negative controls
def test_no_unload_and_no_jeff_action_when_no_job_active():
    io = FakeIO()
    io.ollama = ["gemma4-31b-uncensored:latest", "qwen:latest"]
    io.jobs = [job(ended_ago=600)]
    io.jeff_up = True
    gov = g.Governor(io, cfg(warm={"enabled": False}))
    for _ in range(3):
        gov.tick()
    assert names(io, "unload") == [] and names(io, "jeff_stop") == [] and gov.phase == "IDLE"
    assert io.ollama == ["gemma4-31b-uncensored:latest", "qwen:latest"]


def test_unknown_job_list_does_nothing_destructive():
    io = FakeIO()
    io.jobs = None
    io.ollama = ["m:latest"]
    gov = g.Governor(io, cfg())
    gov.tick()
    assert io.calls == [] and gov.phase == "IDLE"


@pytest.mark.parametrize("status", list(g.LIVE))
def test_every_live_status_activates_the_guard_and_unloads_all_models(status):
    io = FakeIO()
    io.jobs = [job(status=status)]
    io.ollama = ["a:latest", "b:latest"]
    gov = g.Governor(io, cfg())
    gov.tick()
    assert gov.phase == "ACTIVE" and io.ollama == [] and {c[1] for c in names(io, "unload")} == {"a:latest", "b:latest"}
    io.ollama = ["a:latest"]                                      # something reloads it: unloaded again next tick
    gov.tick()
    assert io.ollama == []


def test_guard_ends_when_last_job_ends_and_restores_priorities():
    io = FakeIO()
    io.jobs = [job(status="generating")]
    io.procs = [{"pid": 7, "ct": 1.0, "name": "python.exe", "cmd": "x", "nice": 32}]
    gov = g.Governor(io, cfg(warm={"enabled": False}))
    gov.tick()
    assert ("prio", 7, 16384, 0) in io.calls                      # BELOW_NORMAL + very low I/O
    io.jobs = [job(status="completed", ended_ago=1)]
    gov.tick()
    assert gov.phase == "IDLE" and ("prio", 7, 32, 2) in io.calls  # original values restored
    n = len(io.calls)
    gov.tick()
    assert len(io.calls) == n


def test_backend_unreachable_for_long_while_active_leaves_guard():
    io = FakeIO()
    io.jobs = [job(status="generating")]
    gov = g.Governor(io, cfg(unknown_leave_s=60))
    gov.tick()
    io.jobs = None
    io.t += 10
    gov.tick()
    assert gov.phase == "ACTIVE"
    io.t += 100
    gov.tick()
    assert gov.phase == "IDLE"


def test_dry_run_changes_nothing():
    io = FakeIO()
    io.jobs = [job(status="generating")]
    io.ollama = ["a:latest"]
    io.jeff_up = True
    io.procs = [{"pid": 7, "ct": 1.0, "name": "python.exe", "cmd": "x", "nice": 32}]
    gov = g.Governor(io, cfg(dry_run=True, jeff_during_generation="cloud_free"))   # default routes: Experiential Labs first
    gov.tick()
    io.jobs = [job(ended_ago=1)]
    gov.tick()
    assert io.calls == [] and io.ollama == ["a:latest"] and io.flag is None and gov.phase == "IDLE"


# -------------------------------------------------------------------------------------------------- Jeff: pause
def test_pause_stops_running_jeff_with_hold_and_resumes_it_with_explicit_launch():
    io = FakeIO()
    io.jeff_up = True
    io.jobs = [job(status="generating")]
    gov = g.Governor(io, cfg(warm={"enabled": False}))
    gov.tick()
    assert ("jeff_stop", True) in io.calls and io.jeff_up is False
    gov.tick()
    assert len(names(io, "jeff_stop")) == 1                       # idempotent
    io.jobs = [job(ended_ago=1)]
    gov.tick()
    assert names(io, "jeff_launch") and io.jeff_up is True and io.hold is None


def test_pause_does_not_resume_jeff_the_owner_had_stopped_or_re_held():
    io = FakeIO()
    io.hold = "owner 17.10"                                       # owner's own hold, Jeff down
    io.jobs = [job(status="generating")]
    gov = g.Governor(io, cfg(warm={"enabled": False}))
    gov.tick()
    io.jobs = [job(ended_ago=1)]
    gov.tick()
    assert names(io, "jeff_stop") == [] and names(io, "jeff_launch") == [] and io.hold == "owner 17.10"
    # owner re-holds while we paused a running Jeff
    io2 = FakeIO()
    io2.jeff_up = True
    io2.jobs = [job(status="generating")]
    gov2 = g.Governor(io2, cfg(warm={"enabled": False}))
    gov2.tick()
    io2.hold = "owner changed his mind"
    io2.jobs = [job(ended_ago=1)]
    gov2.tick()
    assert names(io2, "jeff_launch") == []


# ----------------------------------------------------------------------------------------------- Jeff: cloud_free
def test_cloud_free_flag_precedes_launch_heartbeats_and_ends_in_owner_hold():
    io = FakeIO()
    io.hold = "owner hold"                                        # today's real situation: hold-jeff present
    io.jobs = [job(status="loading")]
    gov = g.Governor(io, cfg(jeff_during_generation="cloud_free", warm={"enabled": False}, **OR_ONLY))
    gov.tick()
    kinds = [c[0] for c in io.calls if c[0] in ("flag_write", "jeff_launch", "jeff_stop")]
    assert kinds == ["flag_write", "jeff_launch"]                 # flag first, Jeff was down so no stop
    assert ("jeff_launch", True) in io.calls and io.flag == (FREE, "google/gemma-4-31b-it:free")
    assert "paid/model:free" not in io.flag and "venice/uncensored:free" not in io.flag   # not in the live catalog
    assert io.flag_provider is None
    gov.tick()
    assert len(names(io, "flag_write")) == 2                      # heartbeat
    io.jobs = [job(ended_ago=1)]
    gov.tick()
    assert io.calls[-2] == ("flag_remove",) and io.calls[-1] == ("jeff_stop", True)   # flag removed FIRST, owner hold back
    assert io.flag is None and io.jeff_up is False and io.hold is not None


def test_cloud_free_restarts_a_running_local_jeff_and_relaunches_it_local_afterwards():
    io = FakeIO()
    io.jeff_up = True
    io.jobs = [job(status="generating")]
    gov = g.Governor(io, cfg(jeff_during_generation="cloud_free", warm={"enabled": False}, **OR_ONLY))
    gov.tick()
    seq = [c[0] for c in io.calls if c[0].startswith(("jeff", "flag"))]
    assert seq == ["flag_write", "jeff_stop", "jeff_launch"]
    assert ("jeff_stop", False) in io.calls
    io.jobs = [job(ended_ago=1)]
    gov.tick()
    seq = [c for c in io.calls if c[0].startswith(("jeff", "flag"))][3:]
    assert [c[0] for c in seq] == ["flag_remove", "jeff_stop", "jeff_launch"]
    assert seq[2] == ("jeff_launch", False)                       # relaunched with NO cloud flag -> local-only Jeff


def test_cloud_free_without_verified_model_falls_back_to_pause():
    io = FakeIO()
    io.catalog = None
    io.rows = []
    io.jeff_up = True
    io.jobs = [job(status="generating")]
    gov = g.Governor(io, cfg(jeff_during_generation="cloud_free", warm={"enabled": False}))
    gov.tick()
    assert gov.jeff.mode == "pause" and io.flag is None and ("jeff_stop", True) in io.calls


@pytest.mark.parametrize("mode", ["cloud", "cpu_small"])
def test_paid_cloud_and_cpu_small_are_stubs_that_fall_back_to_pause(mode):
    io = FakeIO()
    io.jeff_up = True
    io.jobs = [job(status="generating")]
    gov = g.Governor(io, cfg(jeff_during_generation=mode, warm={"enabled": False}))
    gov.tick()
    assert gov.jeff.mode == "pause" and io.flag is None and io.calls.count(("jeff_stop", True)) == 1


def test_crash_recovery_reverts_a_persisted_cloud_state(tmp_path):
    io = FakeIO()
    io.hold = "owner hold"
    io.jobs = [job(status="generating")]
    state = tmp_path / "state.json"
    gov = g.Governor(io, cfg(jeff_during_generation="cloud_free", warm={"enabled": False}), state)
    gov.tick()
    assert json.loads(state.read_text())["jeff"]["mode"] == "cloud_free"
    # governor "crashes": a new instance starts, no job is live any more
    io.jobs = [job(ended_ago=30)]
    gov2 = g.Governor(io, cfg(jeff_during_generation="cloud_free", warm={"enabled": False}), state)
    assert gov2.jeff.active
    gov2.tick()
    assert io.flag is None and io.jeff_up is False and io.hold is not None
    assert json.loads(state.read_text())["jeff"] is None


def test_restart_during_a_running_job_adopts_the_state_instead_of_relaunching(tmp_path):
    io = FakeIO()
    io.jobs = [job(status="generating")]
    state = tmp_path / "state.json"
    g.Governor(io, cfg(jeff_during_generation="cloud_free", warm={"enabled": False}), state).tick()
    launches = len(names(io, "jeff_launch"))
    gov2 = g.Governor(io, cfg(jeff_during_generation="cloud_free", warm={"enabled": False}), state)
    gov2.tick()
    assert len(names(io, "jeff_launch")) == launches and gov2.jeff.mode == "cloud_free"


def test_shutdown_while_active_restores_everything():
    io = FakeIO()
    io.jeff_up = True
    io.jobs = [job(status="generating")]
    gov = g.Governor(io, cfg(jeff_during_generation="cloud_free", warm={"enabled": False}))
    gov.tick()
    gov.shutdown()
    assert io.flag is None and io.jeff_up is True


# ---------------------------------------------------------------------------------------------------- warm-up
def test_warm_waits_for_idle_then_reads_ranked_files_and_never_when_a_job_is_live():
    io = FakeIO()
    io.jobs = [job(ended_ago=10)]
    gov = g.Governor(io, cfg())
    run_warm_sync(gov)
    assert names(io, "warm") == []                                # finished 10 s ago: not idle yet
    io.jobs = [job(ended_ago=300)]
    run_warm_sync(gov)
    assert [c[1] for c in names(io, "warm")] == ["wan.gguf", "vae.safetensors"]
    n = len(io.calls)
    run_warm_sync(gov)
    assert len(io.calls) == n                                     # recently warmed: not re-read
    io.jobs = [job(status="queued")]
    io.t += 4000
    run_warm_sync(gov)
    assert len(names(io, "warm")) == 2                            # live job: nothing new


def test_warm_aborts_instantly_when_a_job_appears_mid_read():
    io = FakeIO()
    io.jobs = [job(ended_ago=300)]

    def start_job():
        io.jobs = [job(status="queued")]
        io.t += 5                                                 # outside the 1.5 s cache of the allow() answer
    io.warm_hook = start_job
    gov = g.Governor(io, cfg())
    run_warm_sync(gov)
    warm = names(io, "warm")
    assert len(warm) == 1                                         # the second file was never started
    assert gov.warm_log[-1]["aborted"] is True and gov.warmed == {}


def test_warm_stops_at_the_ram_budget():
    io = FakeIO()
    io.avail = 33_000_000_000                                     # 33 GB - 5 GB < 30 GB
    io.jobs = [job(ended_ago=300)]
    gov = g.Governor(io, cfg())
    run_warm_sync(gov)
    assert names(io, "warm") == []


def test_warm_respects_model_count_and_disabled_flag():
    io = FakeIO()
    io.jobs = [job("m1", ended_ago=300), job("m2", ended_ago=400, age_s=7200)]
    io.index = {"m1.gguf": [("C:/m1", 5_000_000_000)], "m2.gguf": [("C:/m2", 5_000_000_000)],
                "vae.safetensors": [("C:/v", 1_000_000_000)]}
    gov = g.Governor(io, cfg(warm={"max_models": 1}))
    run_warm_sync(gov)
    assert [c[1] for c in names(io, "warm")] == ["m1", "v"]
    io2 = FakeIO()
    io2.jobs = [job(ended_ago=300)]
    run_warm_sync(g.Governor(io2, cfg(warm={"enabled": False})))
    assert names(io2, "warm") == []


def test_dry_run_warm_reads_nothing():
    io = FakeIO()
    io.jobs = [job(ended_ago=300)]
    gov = g.Governor(io, cfg(dry_run=True))
    run_warm_sync(gov)
    assert names(io, "warm") == []


# --------------------------------------------------------------------------------------------- real file reader
def test_reader_reads_every_byte_and_aborts_between_chunks(tmp_path):
    p = tmp_path / "w.bin"
    p.write_bytes(b"x" * (5 * 1024 * 1024 + 123))
    r = g.read_file_low_priority(str(p), lambda: True, chunk_mb=1)
    assert r["bytes"] == p.stat().st_size and r["aborted"] is False
    calls = {"n": 0}

    def allow():
        calls["n"] += 1
        return calls["n"] <= 2
    r = g.read_file_low_priority(str(p), allow, chunk_mb=1)
    assert r["aborted"] is True and r["bytes"] == 2 * 1024 * 1024


def test_config_unknown_jeff_mode_becomes_pause(tmp_path):
    p = tmp_path / "governor.json"
    p.write_text(json.dumps({"jeff_during_generation": "dance", "warm": {"max_models": 3}}))
    c = g.load_config(p)
    assert c["jeff_during_generation"] == "pause" and c["warm"]["max_models"] == 3 and c["warm"]["idle_s"] == 60
    assert g.load_config(tmp_path / "missing.json")["jeff_during_generation"] == "cloud_free"


# --------------------------------------------------------------------------- Experiential Labs route + cost guard
def test_default_route_is_experiential_labs_free_models_never_the_paid_one():
    io = FakeIO()
    io.jobs = [job(status="generating")]
    gov = g.Governor(io, cfg(jeff_during_generation="cloud_free", warm={"enabled": False}))
    gov.tick()
    assert io.flag == EXP and io.flag_provider == 13
    assert "gemini-4-argon" not in io.flag                         # priced 2/10 per M: never a Jeff route


def test_paid_or_offline_or_unknown_price_rows_are_not_picked():
    rows = [dict(name="a", alias="x:a", price_in=0.0, price_out=0.0, pricing_known=1, status="online"),
            dict(name="b", alias="x:b", price_in=0.0, price_out=0.5, pricing_known=1, status="online"),
            dict(name="c", alias="x:c", price_in=0.0, price_out=0.0, pricing_known=0, status="online"),
            dict(name="d", alias="x:d", price_in=0.0, price_out=0.0, pricing_known=1, status="offline"),
            dict(name="e", alias="other", price_in=0.0, price_out=0.0, pricing_known=1, status="online"),
            dict(name="liquid/lfm-x", alias="x:f", price_in=0.0, price_out=0.0, pricing_known=1, status="online")]
    assert g.pick_bcc_provider_models(rows, ["x:a", "x:b", "x:c", "x:d", "x:f"]) == ["a"]
    assert g.pick_bcc_provider_models(None, ["x:a"]) == []


def test_falls_back_to_openrouter_when_the_provider_route_is_unusable():
    io = FakeIO()
    io.rows = None                                                 # provider row missing / key missing
    io.jobs = [job(status="generating")]
    gov = g.Governor(io, cfg(jeff_during_generation="cloud_free", warm={"enabled": False}))
    gov.tick()
    assert io.flag == (FREE, "google/gemma-4-31b-it:free") and io.flag_provider is None


def test_cost_violation_closes_cloud_bans_it_and_falls_back_to_pause_then_restores_at_the_end():
    io = FakeIO()
    io.jeff_up = True
    io.jobs = [job(status="generating")]
    gov = g.Governor(io, cfg(jeff_during_generation="cloud_free", warm={"enabled": False}))
    gov.tick()
    assert gov.jeff.mode == "cloud_free"
    io.violation = {"model": "deepseek-v4.1-flash-1017", "cost": 0.0002}
    gov.tick()
    assert gov.jeff.mode == "pause" and io.flag is None and io.jeff_up is False and io.violation is None
    assert io.calls[-3:][0] == ("flag_remove",) or ("flag_remove",) in io.calls
    gov.tick()
    assert gov.jeff.mode == "pause" and len(names(io, "jeff_stop")) == 2     # stable, no flapping back to cloud
    io.jobs = [job(ended_ago=1)]
    gov.tick()
    assert io.jeff_up is True and io.flag is None                  # Jeff that ran before comes back local at the end
    io.t += 10
    io.jobs = [job(status="generating")]
    gov.tick()                                                     # next generation inside the ban window
    assert gov.jeff.mode == "pause" and io.flag is None


def test_ban_expires():
    io = FakeIO()
    io.jobs = [job(status="generating")]
    gov = g.Governor(io, cfg(jeff_during_generation="cloud_free", warm={"enabled": False}, cloud_ban_s=100))
    gov.tick()
    io.violation = {"model": "m", "cost": 1}
    gov.tick()
    io.jobs = [job(ended_ago=1)]
    gov.tick()
    io.t += 500
    io.jobs = [job(status="generating")]
    gov.tick()
    assert gov.jeff.mode == "cloud_free"


# ------------------------------------------------------------------------- idle-delayed resume, route switch, hot
def test_local_jeff_returns_only_after_the_idle_delay_and_never_during_a_new_job():
    io = FakeIO()
    io.jeff_up = True
    io.jobs = [job(status="generating")]
    gov = g.Governor(io, cfg(resume_delay_s=120, warm={"enabled": False}))
    gov.tick()
    io.jobs = [job(ended_ago=1)]
    gov.tick()
    assert names(io, "jeff_launch") == [] and io.jeff_up is False
    io.t += 60
    gov.tick()
    assert io.jeff_up is False                                    # still inside the delay
    io.jobs = [job(status="queued")]                              # next generation arrives: no local Jeff ever
    gov.tick()
    io.t += 500
    io.jobs = [job(status="queued")]
    gov.tick()
    assert names(io, "jeff_launch") == [] and gov.jeff.mode == "pause"
    io.jobs = [job(ended_ago=1)]
    gov.tick()
    io.t += 130
    gov.tick()
    assert io.jeff_up is True and len(names(io, "jeff_launch")) == 1


def test_adopted_openrouter_cloud_state_is_switched_to_the_better_route(tmp_path):
    io = FakeIO()
    io.jobs = [job(status="generating")]
    state = tmp_path / "s.json"
    state.write_text(json.dumps({"jeff": {"mode": "cloud_free", "owner_hold_before": True, "was_running_before": False,
                                          "models": [FREE], "provider_id": None, "route": "openrouter"}}))
    io.jeff_up = True
    gov = g.Governor(io, cfg(jeff_during_generation="cloud_free", warm={"enabled": False}), state)
    gov.tick()
    assert io.flag == EXP and io.flag_provider == 13 and ("jeff_stop", False) in io.calls
    assert json.loads(state.read_text())["jeff"]["provider_id"] == 13


def test_hot_model_is_probed_and_only_reread_when_cold():
    io = FakeIO()
    io.jobs = [job(ended_ago=300)]
    gov = g.Governor(io, cfg(warm={"hot_models": ["wan2.2-ti2v-5b"], "hot_probe_s": 120, "max_models": 0}))
    run_warm_sync(gov)
    assert len(names(io, "probe")) == 2 and names(io, "warm") == []        # warm: measured, nothing re-read
    io.probe_speed = 40.0                                         # the cache lost them
    io.t += 130
    io.jobs = [job(ended_ago=450)]
    run_warm_sync(gov)
    assert [c[1] for c in names(io, "warm")] == ["wan.gguf", "vae.safetensors"]
    assert gov.hot_state["C:/m/wan.gguf"] == 40.0


def test_old_owner_hold_is_not_reapplied_by_default_jeff_comes_back_after_idle():
    io = FakeIO()
    io.hold = "owner hold"
    io.jobs = [job(status="generating")]
    gov = g.Governor(io, cfg(jeff_during_generation="cloud_free", restore_owner_hold=False, resume_delay_s=120, warm={"enabled": False}))
    gov.tick()
    io.jobs = [job(ended_ago=1)]
    gov.tick()
    assert ("jeff_stop", False) in io.calls and io.hold is None
    io.t += 130
    gov.tick()
    assert io.jeff_up is True and io.flag is None                  # local, no cloud flag


def test_sdcli_is_boosted_while_active_and_never_in_dry_run():
    io = FakeIO()
    io.jobs = [job(status="generating")]
    gov = g.Governor(io, cfg(warm={"enabled": False}))
    gov.tick()
    gov.tick()
    assert len(names(io, "boost")) == 2
    io2 = FakeIO()
    io2.jobs = [job(status="generating")]
    g.Governor(io2, cfg(dry_run=True, warm={"enabled": False})).tick()
    assert names(io2, "boost") == []
