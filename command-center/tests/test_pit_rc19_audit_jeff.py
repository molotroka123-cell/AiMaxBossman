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


# -- P2-4: first-run signup cannot create two accounts ------------------------------------
def test_simultaneous_first_signups_create_exactly_one_account(tmp_path):
    import httpx

    from bcc.pit import web
    from .test_pit_runtime import make_settings

    settings = make_settings(tmp_path)
    runtime = web.WebParticipantRuntime(settings, tmp_path / "pit-v1.7" / "web")
    runtime.adapter = FakeAdapter("Привет.")
    app = web.create_app(settings, port=8850, runtime=runtime)
    headers = {"X-Jeff-Request": "1"}

    async def race():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8850") as client:
            return await asyncio.gather(*[
                client.post("/api/jeff/signup", headers=headers,
                            json={"username": name, "password": "correct horse 1"})  # ci-secret-scan: allow — throwaway test passphrase
                for name in ("alice", "mallory")])

    responses = asyncio.run(race())
    assert sorted(r.status_code for r in responses) == [200, 403]
    assert web.WebAccounts(tmp_path / "pit-v1.7" / "web").count() == 1


# -- P2-3: a broken local ASR model is "unavailable", not an HTTP 500 ------------------------
def test_asr_model_load_failure_is_a_stable_voice_code(monkeypatch, tmp_path):
    from bcc.pit import speech

    monkeypatch.setattr(speech.whisper, "_validated_wav", lambda audio: (audio, 1.0))
    monkeypatch.setattr(speech.whisper, "_model_directory", lambda: tmp_path)

    def broken(model_path):
        raise RuntimeError("Unable to open file 'model.bin' in model")   # ctranslate2 style

    monkeypatch.setattr(speech, "_recogniser", broken)
    try:
        speech.transcribe_wav(b"RIFF")
    except speech.SpeechError as exc:
        assert str(exc) == "VOICE_STT_UNAVAILABLE"
    else:
        raise AssertionError("expected SpeechError")


# -- poller lock: unlocked before the handle closes ----------------------------------------
def test_token_poller_lock_is_unlocked_before_close(monkeypatch, tmp_path):
    import os

    from bcc.pit import bot_guard

    monkeypatch.setenv("BOSSMAN_TELEGRAM_POLLER_LOCK_DIR", str(tmp_path))
    modes = []
    if os.name == "nt":
        import msvcrt
        real = msvcrt.locking
        monkeypatch.setattr(msvcrt, "locking", lambda fd, mode, n: (modes.append(mode), real(fd, mode, n))[1])
        unlock = msvcrt.LK_UNLCK
    else:
        import fcntl
        real = fcntl.flock
        monkeypatch.setattr(fcntl, "flock", lambda f, op: (modes.append(op), real(f, op))[1])
        unlock = fcntl.LOCK_UN
    with bot_guard.token_poller_lock("123456:fixture-token"):
        pass
    assert modes[-1] == unlock
    with bot_guard.token_poller_lock("123456:fixture-token"):   # immediately free again
        pass


# -- rc19 core audit: a corrupt counter must not hand out a fresh budget ------------------
def test_corrupt_budget_file_is_set_aside_and_today_counts_as_spent(tmp_path):
    budget = CloudBudget(tmp_path, 100)
    budget.spend()
    budget.path.write_text("{\"date\": \"20", encoding="utf-8")        # torn / hand-edited
    assert budget.blocked() == "daily_budget"                          # was "" (fresh 100)
    assert budget.try_spend() == "daily_budget"
    aside = list(tmp_path.glob("cloud_budget.corrupt-*.json"))
    assert len(aside) == 1 and aside[0].read_text(encoding="utf-8") == "{\"date\": \"20"
    # the exhausted state is persisted: a second reader does not start clean either
    assert CloudBudget(tmp_path, 100).blocked() == "daily_budget"
    assert CloudBudget(tmp_path, 100).status()["last_stop_reason"] == "budget_corrupt"


def test_check_and_spend_are_one_step(tmp_path):
    budget = CloudBudget(tmp_path, 2)
    assert budget.try_spend() == "" and budget.try_spend() == ""
    assert budget.try_spend() == "daily_budget"
    assert budget.status()["used_today"] == 2                          # the refused one not counted
    budget.stop("provider_daily_limit", until=time.time() + 3600)
    assert CloudBudget(tmp_path, 100).try_spend() == "provider_daily_limit"


def test_a_stale_budget_check_does_not_send_past_the_budget(tmp_path, monkeypatch):
    """Two surfaces share one counter: the other one spent the last request after
    this turn's check. Before, the turn still sent (check and spend were separate)."""
    import dataclasses as _dc
    from .test_pit_runtime import make_settings
    settings = _dc.replace(make_settings(tmp_path), cloud_daily_request_budget=1)
    adapter = FakeAdapter("Ок.")
    runtime = make_runtime(tmp_path, adapter=adapter, settings=settings)
    _started(runtime)
    runtime.cloud.spend()                                               # the other surface
    monkeypatch.setattr(runtime.cloud, "blocked", lambda: "")           # its stale check
    answer = asyncio.run(runtime.handle(_person(runtime), message("вопрос", message_id=310)))
    assert adapter.calls == []
    assert answer == rt.CLOUD_PAUSED_RU
    assert runtime.cloud_budget_status()["used_today"] == 1
