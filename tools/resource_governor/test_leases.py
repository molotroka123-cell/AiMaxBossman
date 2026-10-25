"""Governor 2.0 lease arbiter (TZ 2.5 T3). Pure fakes: no GPU job, no Ollama, no real process is started or stopped.

Negative controls: unknown P95 refused, stale fencing token refused, paid render never preempted (by background, by the
memory guard, by owner chat), a foreign/pid-reused process is never adopted nor stopped, invariant violated on purpose
is detected by the checker.
"""
from __future__ import annotations

import random
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import leases as L  # noqa: E402


class Clock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


class Mem:
    def __init__(self, gb: float | None = None):
        self.gb = gb

    def __call__(self) -> float | None:
        return self.gb


def ledger(tmp_path, **cfg) -> tuple[L.LeaseLedger, Clock, Mem]:
    clock, mem = Clock(), Mem(cfg.pop("mem", None))
    unload = cfg.pop("unload", None)
    return L.LeaseLedger(tmp_path / "leases.db", cfg, clock=clock, mem=mem, unload=unload), clock, mem


def invariant(led: L.LeaseLedger) -> None:
    active = led.active()
    limit = led.cfg["budget_gb"] - led.cfg["headroom_gb"]
    assert L.committed_gb(active) <= limit + 1e-9, (L.committed_gb(active), [(x.consumer, x.state) for x in active])
    assert len([x for x in active if x.gpu_heavy and x.kind == "compute"]) <= 1


# ---------- registry / measurement ----------

def test_unknown_p95_is_refused_with_reason(tmp_path):
    led, _, _ = ledger(tmp_path)
    r = led.request("z_image", "background", "photo")
    assert r["state"] == "refused" and "P95" in r["reason"] and "token" not in r
    assert led.request("no_such", "background")["state"] == "refused"
    with pytest.raises(L.LeaseError):
        led.request("gemma_chat", "vip")


def test_ten_measurements_with_a_cold_start_replace_the_seed(tmp_path):
    led, _, _ = ledger(tmp_path)
    assert led.profiles()["wangp_vace"]["source"] == "provisional 0/10"
    for i in range(9):
        led.record_measurement("wangp_vace", 40 + i, "cfg-480p-81f", cold=False)
    p = led.profiles()["wangp_vace"]
    assert p["source"] == "provisional 9/10" and p["p95_gb"] == 55.0
    led.record_measurement("wangp_vace", 41.5, "cfg-480p-81f", cold=False)
    assert led.profiles()["wangp_vace"]["source"].startswith("provisional")      # 10 samples but no cold start yet
    led.record_measurement("z_image", 21.0, "cold", cold=True)
    led.record_measurement("wangp_vace", 47.0, "cfg-480p-81f", cold=True)
    p = led.profiles()["wangp_vace"]
    assert p["source"] == "measured" and p["p95_gb"] == 48.0 and p["samples"] == 11
    assert L.nearest_rank_p95([float(x) for x in range(1, 21)]) == 19.0


def test_strict_mode_refuses_provisional_profiles(tmp_path):
    led, _, _ = ledger(tmp_path, strict_measured=True)
    assert led.request("ltx25_sdcli", "paid_render")["state"] == "refused"


# ---------- grant rule ----------

def test_gemma_plus_one_heavy_job_fits_but_not_two_heavy(tmp_path):
    led, _, _ = ledger(tmp_path)
    chat = led.request("gemma_chat", "owner_interactive", "chat")
    jeff = led.request("gemma_chat", "jeff_answer", "jeff")                 # same model: counted once
    ltx = led.request("ltx25_sdcli", "paid_render", "studio-1")
    assert [chat["state"], jeff["state"], ltx["state"]] == ["granted", "granted", "granted"]
    assert led.state()["committed_gb"] == 108.0
    second = led.request("wangp_vace", "paid_render", "studio-2")
    assert second["state"] == "queued" and "одна тяжёлая GPU-задача" in second["reason"]
    invariant(led)


def test_live_memory_check_and_idle_unload_hook(tmp_path):
    calls = []
    mem = Mem(40.0)

    def unload(keep, timeout):
        calls.append(set(keep))
        mem.gb = 70.0                                                        # an idle 30 GB model went away
        return {"unloaded": ["qwen3.6-35b:latest"], "failed": [], "confirmed": True}
    led = L.LeaseLedger(tmp_path / "l.db", {}, clock=Clock(), mem=mem, unload=unload)
    led.request("gemma_chat", "owner_interactive", "chat")
    r = led.request("wangp_vace", "paid_render", "studio")                   # 40 - 55 < 8 -> unload first
    assert calls and L.name_matches("gemma4-31b-uncensored:latest", calls[0])  # the leased chat model is kept
    assert r["state"] == "granted"


def test_unload_that_frees_nothing_leaves_request_queued_with_reason(tmp_path):
    led = L.LeaseLedger(tmp_path / "l.db", {}, clock=Clock(), mem=Mem(20.0),
                        unload=lambda keep, t: {"unloaded": [], "failed": ["x"], "confirmed": False})
    r = led.request("wangp_vace", "paid_render", "studio")
    assert r["state"] == "queued" and "мало свободной памяти" in r["reason"]


# ---------- priorities / preemption ----------

def test_owner_interactive_wins_over_background_incident_20261025(tmp_path):
    """25.10: owner chat on Gemma slowed down while a background WanGP held 38 GB next to it -> WanGP must yield."""
    led, clock, mem = ledger(tmp_path, mem=80.0)
    bg = led.request("wangp_animate", "background", "self-improve clip")
    assert bg["state"] == "granted"
    clock.t += 5
    chat = led.request("gemma_chat", "owner_interactive", "owner chat")
    assert chat["state"] == "granted"                                         # chat is not blocked
    got = led.get(bg["id"])
    assert got["state"] == "preempting" and got["stop"] and "чат владельца" in got["reason"]
    assert led.heartbeat(bg["id"], bg["token"])["stop"] is True               # the worker learns to stop
    assert not led.check_fence(bg["id"], bg["token"])                         # and cannot (re)start heavy work
    again = led.request("wangp_animate", "background", "retry")               # new background GPU work waits
    assert again["state"] == "queued" and "владелец сейчас в чате" in again["reason"]
    led.release(bg["id"], bg["token"])
    led.release(chat["id"], chat["token"])
    assert led.get(again["id"])["state"] == "granted"                         # promoted once the owner is done
    invariant(led)


def test_priority_preemption_frees_memory_for_higher_priority(tmp_path):
    led, clock, _ = ledger(tmp_path, budget_gb=100.0, headroom_gb=8.0, interactive_preempts_background_gpu=False)
    ev = led.request("wangp_vace", "eval", "bench")
    clock.t += 1
    chat = led.request("gemma_chat", "owner_interactive", "chat")              # 55 + 38 > 92 -> eval yields
    assert chat["state"] == "queued" and "ждёт остановки" in chat["reason"]
    assert led.get(ev["id"])["state"] == "preempting"
    invariant(led)                                                             # victim still counted until it stops
    led.release(ev["id"], ev["token"])
    assert led.get(chat["id"])["state"] == "granted"


def test_paid_render_is_never_preempted_for_background_or_owner(tmp_path):
    led, clock, _ = ledger(tmp_path, budget_gb=100.0)
    paid = led.request("ltx25_sdcli", "paid_render", "studio-order-7")
    clock.t += 1
    bg = led.request("wangp_vace", "background", "bg")
    assert bg["state"] == "queued" and "платный рендер Studio автоматически не останавливается" in bg["reason"]
    chat = led.request("gemma_chat", "owner_interactive", "chat")              # 70 + 38 > 92 even for the owner
    assert chat["state"] == "queued"
    assert led.get(paid["id"])["state"] == "granted"
    for _ in range(3):                                                         # nor by the memory guard
        out = led.tick(available_gb=1.0)
        assert out["guard"]["lease_id"] is None
    assert led.get(paid["id"])["state"] == "granted"


def test_queue_order_priority_then_aging_then_fifo():
    cfg = dict(L.LEASE_DEFAULTS)
    mk = lambda i, pr, t: L.Lease(str(i), "wangp_vace", pr, "compute", 55, True, "queued", t)  # noqa: E731
    q = [mk(1, "eval", 0), mk(2, "background", 50), mk(3, "background", 10), mk(4, "paid_render", 100)]
    assert [x.id for x in L.queue_order(q, 100, cfg)] == ["4", "3", "2", "1"]
    old_bg = mk(5, "background", -10 * 86400)                                  # aging is capped below the next class
    assert [x.id for x in L.queue_order([old_bg, mk(6, "paid_render", 0)], 0, cfg)] == ["6", "5"]


# ---------- guard / ttl / fencing ----------

def test_memory_guard_stops_lowest_priority_and_logs_reason(tmp_path):
    led, clock, _ = ledger(tmp_path, interactive_preempts_background_gpu=False)
    chat = led.request("gemma_chat", "owner_interactive", "chat")
    clock.t += 1
    ev = led.request("wangp_vace", "eval", "bench")
    out = led.tick(available_gb=2.5)
    assert out["guard"]["lease_id"] == ev["id"] and "предохранитель памяти" in out["guard"]["reason"]
    assert led.get(chat["id"])["state"] == "granted"
    assert any(e["event"] == "preempted" and "предохранитель" in e["reason"] for e in led.state()["events"])


def test_guard_terminates_only_a_process_holding_the_governor_token(tmp_path):
    led, clock, _ = ledger(tmp_path)
    mine = led.request("wangp_vace", "background", "bg", pid=4242, proc_create_time=111.0)
    stopped = []
    probe = {4242: {"create_time": 111.0, "env_token": L.lease_env_token(mine["id"], mine["token"])}}
    out = led.tick(available_gb=1.0, stop=lambda lease: stopped.append(lease.pid) or True, probe=probe.get)
    assert out["guard"]["terminated"] is True and stopped == [4242]
    led2, _, _ = ledger(tmp_path / "b")
    foreign = led2.request("wangp_vace", "background", "bg", pid=4343, proc_create_time=5.0)
    out = led2.tick(available_gb=1.0, stop=lambda lease: stopped.append(lease.pid) or True,
                    probe={4343: {"create_time": 5.0, "env_token": None}}.get)
    assert out["guard"]["terminated"] is False and stopped == [4242]           # no token -> never touched
    assert led2.get(foreign["id"])["state"] == "preempting"


def test_ttl_expiry_and_fencing(tmp_path):
    led, clock, _ = ledger(tmp_path)
    a = led.request("wangp_vace", "background", "bg", ttl_s=60)
    assert led.check_fence(a["id"], a["token"])
    assert not led.check_fence(a["id"], a["token"] + 1)
    with pytest.raises(L.LeaseError) as e:
        led.heartbeat(a["id"], 999)
    assert e.value.code == "stale_token"
    with pytest.raises(L.LeaseError):
        led.release(a["id"], "nope")
    clock.t += 61
    assert a["id"] in led.tick(available_gb=100)["expired"]
    assert not led.check_fence(a["id"], a["token"])                           # late worker with old token refused
    b = led.request("wangp_vace", "background", "bg2")
    assert b["state"] == "granted" and b["token"] > a["token"]


def test_idempotency_key_returns_the_same_lease(tmp_path):
    led, _, _ = ledger(tmp_path)
    a = led.request("ltx25_sdcli", "paid_render", "order-1", idempotency_key="order-1/seg-3")
    b = led.request("ltx25_sdcli", "paid_render", "order-1", idempotency_key="order-1/seg-3")
    assert a["id"] == b["id"] and b["idempotent_replay"] and len(led.active()) == 1


# ---------- restart recovery ----------

def test_restart_recovery_readopts_only_token_holders(tmp_path):
    led, clock, _ = ledger(tmp_path)
    ok = led.request("gemma_chat", "owner_interactive", "chat", pid=10, proc_create_time=1.0)
    dead = led.request("ltx25_sdcli", "paid_render", "order", pid=11, proc_create_time=2.0)
    reused = led.request("gemma_chat", "jeff_answer", "jeff", pid=12, proc_create_time=3.0)
    nopid = led.request("gemma_chat", "jeff_answer", "backend")
    procs = {10: {"create_time": 1.0, "env_token": L.lease_env_token(ok["id"], ok["token"])},
             12: {"create_time": 99.0, "env_token": None}}                    # same pid, another process
    led2 = L.LeaseLedger(tmp_path / "leases.db", {}, clock=clock)            # "restart": a new ledger object
    out = led2.recover(procs.get)
    assert out["readopted"] == [ok["id"]] and out["expired"] == [dead["id"]]
    assert out["released_foreign"] == [reused["id"]] and nopid["id"] in out["kept_no_pid"]
    assert led2.get(dead["id"])["state"] == "expired"                         # paid render not repeated, just closed
    assert led2.check_fence(ok["id"], ok["token"]) and not led2.check_fence(reused["id"], reused["token"])
    assert led2.state()["epoch"] == 1


# ---------- property / concurrency ----------

CONSUMERS = ["gemma_chat", "wangp_animate", "wangp_vace", "ltx25_sdcli"]


@pytest.mark.parametrize("seed", range(4))
def test_property_sum_of_active_peaks_never_exceeds_limit(tmp_path, seed):
    """1000 random sequences in total (4 x 250) of request/release/heartbeat/guard/expiry/recovery."""
    rnd = random.Random(seed)
    for seq in range(250):
        led, clock, mem = ledger(tmp_path / f"s{seed}-{seq}", budget_gb=rnd.choice([96.0, 120.0]))
        held: list[dict] = []
        for _ in range(rnd.randint(5, 25)):
            op = rnd.random()
            mem.gb = rnd.choice([None, 2.0, 30.0, 70.0, 110.0])
            clock.t += rnd.choice([1, 5, 400])
            if op < 0.5:
                r = led.request(rnd.choice(CONSUMERS), rnd.choice(list(L.PRIORITIES)), "p",
                                idempotency_key=rnd.choice([None, f"k{rnd.randint(0, 5)}"]), ttl_s=300)
                held.append(r)
            elif op < 0.7 and held:
                r = held.pop(rnd.randrange(len(held)))
                led.release(r["id"], r.get("token"), force=rnd.random() < 0.3)
            elif op < 0.8 and held:
                r = rnd.choice(held)
                try:
                    led.heartbeat(r["id"], r.get("token"))
                except L.LeaseError:
                    pass
            elif op < 0.95:
                led.tick(available_gb=mem.gb)
            else:
                other = L.LeaseLedger(led.path, led.cfg, clock=clock)
                other.recover(lambda pid: None)
                other.close()
            invariant(led)
            assert all(x.priority != "paid_render" or x.state == "granted" for x in led.active())
        led.close()


def test_invariant_checker_catches_a_violation(tmp_path):
    """Negative control: write an over-budget pair straight into the DB; the checker must fail."""
    led, _, _ = ledger(tmp_path)
    led.request("ltx25_sdcli", "paid_render", "a")
    with sqlite3.connect(led.path) as c:
        c.execute("UPDATE leases SET peak_gb = 200")
    with pytest.raises(AssertionError):
        invariant(led)


def test_fifty_parallel_requests_never_double_grant(tmp_path):
    led, _, _ = ledger(tmp_path)
    results, errors = [], []
    barrier = threading.Barrier(50)

    def worker(i: int) -> None:
        try:
            barrier.wait()
            results.append(led.request(CONSUMERS[1 + i % 3], "background", f"w{i}"))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors and len(results) == 50
    assert sum(r["state"] == "granted" for r in results) == 1
    invariant(led)


def test_ollama_unloader_confirms_via_api_ps():
    loaded = {"models": [{"name": "gemma4-31b-uncensored:latest", "size": 38e9}, {"name": "qwen3.6-35b:latest",
                                                                                   "size": 30e9}]}
    posts = []

    def http(method, url, body=None, timeout=5.0):
        if method == "POST":
            posts.append(body)
            loaded["models"] = [m for m in loaded["models"] if m["name"] != body["model"]]
            return {}
        return loaded
    un = L.OllamaUnloader("http://x", http=http, sleep=lambda s: None)
    res = un({"gemma4-31b*"}, 1.0)
    assert res == {"unloaded": ["qwen3.6-35b:latest"], "failed": [], "confirmed": True}
    assert posts == [{"model": "qwen3.6-35b:latest", "keep_alive": 0}]


def test_ollama_unloader_reports_unconfirmed_when_model_stays():
    t = [0.0]

    def http(method, url, body=None, timeout=5.0):
        return {} if method == "POST" else {"models": [{"name": "stuck:latest", "size": 1e9}]}

    def clock():
        t[0] += 1.0
        return t[0]
    res = L.OllamaUnloader("http://x", http=http, sleep=lambda s: None, clock=clock)(set(), 3.0)
    assert res["confirmed"] is False and res["failed"] == ["stuck:latest"]


def test_background_request_never_unloads_the_owner_chat_model(tmp_path):
    seen = []
    led = L.LeaseLedger(tmp_path / "l.db", {"keep_models": ["pinned:latest"]}, clock=Clock(), mem=Mem(30.0),
                        unload=lambda keep, t: seen.append(set(keep)) or {"unloaded": [], "confirmed": True})
    led.request("wangp_vace", "background", "bg")                            # Gemma is NOT leased right now
    assert seen and all(L.name_matches(n, seen[0]) for n in
                        ("gemma4-31b-uncensored-ctx32k:latest", "bossman-uncensored-chat:latest", "pinned:latest"))
    assert not L.name_matches("qwen3.6-35b:latest", seen[0])


def test_already_loaded_chat_model_is_not_asked_for_twice_live_probe_20261025(tmp_path):
    """Live probe 25.10: Gemma (34.7 GB) loaded in Ollama, 22.6 GB free -> the owner chat lease must be granted."""
    loaded = [{"name": "gemma4-31b-uncensored-ctx32k:latest", "size_gb": 34.72}]           # the name seen live
    led = L.LeaseLedger(tmp_path / "l.db", {}, clock=Clock(), mem=Mem(22.6), loaded=lambda: loaded)
    assert led.request("gemma_chat", "owner_interactive", "chat")["state"] == "granted"
    led2 = L.LeaseLedger(tmp_path / "m.db", {}, clock=Clock(), mem=Mem(22.6), loaded=lambda: [])
    assert led2.request("gemma_chat", "owner_interactive", "chat")["state"] == "queued"   # negative control
    assert led.request("wangp_vace", "paid_render", "studio")["state"] == "queued"       # heavy job still waits


def test_preemption_picks_only_the_victims_that_are_needed(tmp_path):
    led, clock, _ = ledger(tmp_path, interactive_preempts_background_gpu=False)
    heavy = led.request("wangp_vace", "background", "bg-heavy")
    clock.t += 1
    small = led.request("gemma_chat", "eval", "eval-chat")                    # lowest priority, but not in the way
    clock.t += 1
    paid = led.request("ltx25_sdcli", "paid_render", "studio")               # needs the single GPU slot
    assert paid["state"] == "queued"
    assert led.get(heavy["id"])["state"] == "preempting" and led.get(small["id"])["state"] == "granted"


# ---------- swarm review findings (Nemotron, 25.10) ----------

def test_queued_owner_chat_also_holds_back_new_background_gpu(tmp_path):
    led, clock, _ = ledger(tmp_path, budget_gb=100.0)
    paid = led.request("ltx25_sdcli", "paid_render", "studio")              # 70 GB, never preempted
    chat = led.request("gemma_chat", "owner_interactive", "chat")           # 70 + 38 > 92 -> queued
    assert paid["state"] == "granted" and chat["state"] == "queued"
    led.release(paid["id"], paid["token"])                                  # the chat is promoted first
    assert led.get(chat["id"])["state"] == "granted"
    led2, _, _ = ledger(tmp_path / "b", budget_gb=100.0)
    p2 = led2.request("ltx25_sdcli", "paid_render", "studio")
    led2.request("gemma_chat", "owner_interactive", "chat")
    bg = led2.request("wangp_vace", "background", "bg")
    assert bg["state"] == "queued" and "владелец сейчас в чате" in bg["reason"]
    assert led2.get(p2["id"])["state"] == "granted"


def test_concurrent_same_idempotency_key_creates_one_lease(tmp_path):
    led, _, _ = ledger(tmp_path)
    out, errors = [], []
    barrier = threading.Barrier(20)

    def worker() -> None:
        try:
            barrier.wait()
            out.append(led.request("ltx25_sdcli", "paid_render", "o", idempotency_key="order-1/seg-1"))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    ts = [threading.Thread(target=worker) for _ in range(20)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors and len({r["id"] for r in out}) == 1 and len(led.active()) == 1


def test_recovery_keeps_counting_a_live_process_whose_env_is_unreadable(tmp_path):
    led, clock, _ = ledger(tmp_path)
    a = led.request("ltx25_sdcli", "paid_render", "order", pid=50, proc_create_time=7.0)
    led2 = L.LeaseLedger(tmp_path / "leases.db", {}, clock=clock)
    out = led2.recover({50: {"create_time": 7.0, "env_token": None, "env_readable": False}}.get)
    got = led2.get(a["id"])
    assert out["unverified"] == [a["id"]] and got["state"] == "granted" and not got["stop"]   # paid render not told to stop
    assert L.committed_gb(led2.active()) == 70.0                            # memory still counted
    clock.t += 400                                                           # no heartbeat from its holder -> expires
    led2.tick(available_gb=100.0)
    assert led2.get(a["id"])["state"] == "expired"


def test_name_matches_patterns():
    assert L.name_matches("Gemma4-31B-uncensored-ctx32k:latest", ["gemma4-31b*"])
    assert L.name_matches("pinned:latest", ["pinned"]) and not L.name_matches("pinned:q4", ["pinned"])
    assert not L.name_matches("gemma3:latest", ["gemma4-31b*"])
