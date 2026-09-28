"""RC19 audit regressions for Jeff routing and the shared free-cloud budget.

Each test failed on 4fe5a184 (repros from the Jeff/Telegram audit):
- one "local busy" capacity reading removed the local model from the shared
  catalog for good, so every later cloud 429 answered «лимит облака»;
- a cloud failure that was not a 429 (timeout, 5xx, network) never tried the
  verified local model, and a slow cloud used up the whole turn deadline;
- the Telegram bot and the Jeff window (two processes) lost and reset the
  shared budget file, erasing a provider daily-limit pause;
- a spent daily budget wrote a "cloud paused" log line on every turn.

No network: adapters, capacity probes and time budgets are faked.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import subprocess
import sys
import time
from pathlib import Path

from bcc.pit import runtime as rt
from bcc.pit.cloud_budget import CloudBudget
from bcc.providers import ChatResult, ProviderError

from .test_pit_rc19_jeff import RateLimited, _person, _started, _with_local
from .test_pit_runtime import FakeAdapter, make_runtime, message

ROOT = Path(__file__).resolve().parents[1]


def _cloud_slot(runtime):
    runtime.store.put("chat_route_counter", 1)      # the cloud slot of the 70/30 mix


# -- P1-1: a transient capacity miss is per turn, not forever ----------------------------
def test_one_busy_capacity_reading_does_not_drop_local_for_later_turns(tmp_path):
    runtime = make_runtime(tmp_path, adapter=RateLimited())
    _started(runtime)
    local = _with_local(runtime)
    probes = {"n": 0}

    async def busy_once():
        probes["n"] += 1
        return probes["n"] != 1

    runtime.capacity_guard.local_allowed = busy_once
    answers = []
    for i in range(3):
        _cloud_slot(runtime)
        answers.append(asyncio.run(runtime.handle(_person(runtime), message("привет", message_id=200 + i))))
    assert answers[0] == rt.CLOUD_PAUSED_RU            # local really was busy then
    assert answers[1:] == ["Локальный ответ.", "Локальный ответ."]
    assert len(local.calls) == 2
    assert "jeff-local:latest" in runtime.catalog


def test_local_missing_from_catalog_is_looked_for_again(tmp_path):
    """Busy at catalog build time: the Jeff window has no poll loop to refresh it."""
    runtime = make_runtime(tmp_path, adapter=RateLimited())
    _started(runtime)
    local = _with_local(runtime)
    local.pricing = {"jeff-local:latest": {"prompt": 0.0, "completion": 0.0}}
    runtime.settings = dataclasses.replace(runtime.settings, local_url="http://127.0.0.1:11434/v1",
                                           local_models=("jeff-local:latest",))
    runtime.catalog.pop("jeff-local:latest")
    runtime.catalog_checked_at = time.monotonic() - rt.LOCAL_RECHECK_SECONDS - 1
    _cloud_slot(runtime)
    answer = asyncio.run(runtime.handle(_person(runtime), message("привет", message_id=210)))
    assert answer == "Локальный ответ."
    assert "jeff-local:latest" in runtime.catalog


# -- P1-2: every cloud failure ends on the local model, within the deadline -------------
def test_cloud_network_failure_falls_back_to_local(tmp_path):
    class Down(FakeAdapter):
        async def chat(self, model, messages, **kw):
            raise ProviderError("сервер провайдера ответил 502", kind="http")

    runtime = make_runtime(tmp_path, adapter=Down())
    _started(runtime)
    local = _with_local(runtime)
    answer = asyncio.run(runtime.handle(_person(runtime), message("привет", message_id=220)))
    assert answer == "Локальный ответ."
    assert len(local.calls) == 1


def test_slow_cloud_leaves_time_for_the_local_fallback(tmp_path):
    class Hanging(FakeAdapter):
        async def chat(self, model, messages, **kw):
            await asyncio.sleep(kw["timeout"] + 30)

    runtime = make_runtime(tmp_path, adapter=Hanging())
    _started(runtime)
    local = _with_local(runtime)
    runtime.settings = dataclasses.replace(runtime.settings, chat_deadline_seconds=10)
    started = time.monotonic()
    answer = asyncio.run(runtime.handle(_person(runtime), message("привет", message_id=230)))
    assert answer == "Локальный ответ."
    assert len(local.calls) == 1
    assert time.monotonic() - started < 10.5


def test_cloud_refusal_still_does_not_reach_local_without_owner_opt_in(tmp_path):
    runtime = make_runtime(tmp_path, adapter=FakeAdapter("I can't help with that request."))
    _started(runtime)
    local = _with_local(runtime)
    assert runtime.settings.local_fallback_on_cloud_refusal is False
    answer = asyncio.run(runtime.handle(_person(runtime), message("помоги", message_id=240)))
    assert local.calls == []
    assert answer != "Локальный ответ."


# -- P1-3: the budget file is shared by two processes ----------------------------------
_SPENDER = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from bcc.pit.cloud_budget import CloudBudget
budget = CloudBudget(Path(sys.argv[2]), 100000)
failed = 0
for _ in range(int(sys.argv[3])):
    if budget.spend() is False:
        failed += 1
    budget.blocked()
print(failed)
"""


def test_two_processes_keep_the_count_and_the_provider_pause(tmp_path):
    budget = CloudBudget(tmp_path, 100000)
    until = time.time() + 3600
    budget.stop("provider_daily_limit", until=until)
    per_process = 150
    procs = [subprocess.Popen([sys.executable, "-c", _SPENDER, str(ROOT), str(tmp_path), str(per_process)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for _ in range(2)]
    outputs = [proc.communicate(timeout=120) for proc in procs]
    assert all(proc.returncode == 0 for proc in procs), [err for _, err in outputs]
    assert [out.strip() for out, _ in outputs] == ["0", "0"]
    status = budget.status()
    assert status["used_today"] == 2 * per_process
    assert status["cooldown_until"] is not None
    assert budget.blocked() == "provider_daily_limit"


def test_unreadable_budget_fails_closed_and_is_never_reset(tmp_path, monkeypatch):
    budget = CloudBudget(tmp_path, 100000)
    budget.stop("provider_daily_limit", until=time.time() + 3600)
    budget.spend()
    before = budget.path.read_text(encoding="utf-8")

    def locked_by_other_process(self, *args, **kwargs):
        raise PermissionError(13, "Отказано в доступе")

    monkeypatch.setattr(Path, "read_text", locked_by_other_process)
    assert budget.blocked() == "budget_unreadable"
    assert budget.model_blocked("free/model:free") is True
    assert budget.spend() is False
    assert budget.cool_model("free/model:free") is False
    monkeypatch.undo()
    assert budget.path.read_text(encoding="utf-8") == before
    assert budget.status()["used_today"] == 1


# -- P2-5: a spent budget is logged once ------------------------------------------------
def test_spent_daily_budget_is_logged_once_not_every_turn(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.cloud.daily_budget = 0
    for _ in range(3):
        assert runtime._cloud_blocked() == "daily_budget"
    log = runtime.home / "logs" / "cloud_budget.jsonl"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [row["reason"] for row in rows] == ["daily_budget"]
