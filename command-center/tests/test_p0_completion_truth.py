"""P0 — completion truth (owner audit 2026-09-08, tasks 22/44/45).

Owner-live evidence: tasks marked `completed` with `result=""`, and a browser
task marked `completed` after the model answered "complete the CAPTCHA and
press Resume" because the review verified `url_contains=youtube.com` on a
challenge page. These tests reproduce both on the engine and pin the fix:

    COMPLETED = every declared obligation discharged by verified evidence.

A conversational task legitimately completes with text. A task that produced
NO text, NO verified effect and NO executed effectful tool call produced
nothing, and nothing is not a result. A human challenge on the page is a
state the owner resolves, never a success.
"""
from __future__ import annotations

import time

import pytest
import sqlalchemy as sa

from bcc.db import approvals as approvals_t, tasks as tasks_t
from bcc.v2.tables import browser_sessions as bs_t

from .conftest import FakeAdapter
from .helpers import make_stack
from .test_finalize_gate import _run_once, _status


async def _task(env, task_id):
    return (await env.client.get(f"/api/tasks/{task_id}")).json()


# ------------------------------------------------------------- P0-A: empty result

@pytest.mark.parametrize("prompt", [
    "Открой higgsfield сгенерируй в режиме unlimited видео",              # owner task 22
    "Перечисли что ты умеешь делать на компе моём в новом текстовом документе",  # owner task 44
    "посчитай 2+2",                                                       # plain conversational
])
async def test_an_empty_answer_with_no_effect_is_not_completed(env, prompt):
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("")
    stack = await make_stack(env.client, prompt=prompt, max_retries=0)
    await _run_once(env)
    data = await _task(env, stack["task"]["id"])
    assert data["task"]["status"] != "completed", data
    assert (data.get("result") or "") == ""
    assert data["task"]["status"] in ("failed", "waiting_approval", "blocked")


async def test_a_conversational_answer_still_completes(env):
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("4")
    stack = await make_stack(env.client, prompt="посчитай 2+2", max_retries=0)
    await _run_once(env)
    data = await _task(env, stack["task"]["id"])
    assert data["task"]["status"] == "completed" and data["result"] == "4"


async def test_whitespace_only_answer_is_empty(env):
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("  \n\t ")
    stack = await make_stack(env.client, prompt="посчитай 2+2", max_retries=0)
    await _run_once(env)
    assert await _status(env, stack["task"]["id"]) != "completed"


async def test_prose_claiming_a_document_was_written_is_not_completed(env):
    """Owner task 44: the deliverable is a new text document, not a sentence
    about one. `в новом текстовом документе` must carry a file obligation."""
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter(
        "Готово, я создал новый текстовый документ со списком возможностей.")
    stack = await make_stack(env.client, max_retries=0,
                             prompt="Перечисли что ты умеешь делать на компе моём в новом текстовом документе")
    await _run_once(env)
    assert await _status(env, stack["task"]["id"]) != "completed"


# ------------------------------------------------ P0-B: challenge is a state

class _ChallengeManager:
    """A browser manager whose live session sits on a reCAPTCHA page of the
    target domain. `snapshot()` is what the verifier reads."""

    def __init__(self, captcha: bool = True, url: str = "https://www.youtube.com/"):
        self.captcha = captcha
        self.url = url
        self.resumed = 0

    async def snapshot(self, session_id, *, actor="agent", approved=False, **kw):
        return {"session_id": session_id, "url": self.url, "title": "YouTube",
                "text": "", "interactive": [], "takeover": False, "paused": False,
                "captcha": ({"present": True, "provider": "Google reCAPTCHA"}
                            if self.captcha else {"present": False, "provider": ""})}

    async def resume(self, session_id):
        self.resumed += 1
        return {"id": session_id, "url": self.url, "takeover": False, "paused": False}

    async def takeover(self, session_id):
        return {"id": session_id, "url": self.url, "takeover": True, "paused": False}


async def _bind_session(env, task_id, agent_id, *, url="https://www.youtube.com/"):
    async with env.svc.db.session() as s:
        res = await s.execute(sa.insert(bs_t).values(task_id=task_id, agent_id=agent_id,
                                                    status="running", current_url=url))
        await s.commit()
        return int(res.inserted_primary_key[0])


@pytest.fixture
def challenge(monkeypatch):
    from bcc.features import browser as feat
    mgr = _ChallengeManager()
    monkeypatch.setattr(feat, "_mgr", lambda svc: mgr)
    return mgr


EXCUSE = ("На странице YouTube появилась капча (Google reCAPTCHA). Пройдите капчу вручную "
          "и нажмите Resume, после чего я продолжу.")


async def test_captcha_excuse_is_not_completed_even_though_the_domain_matches(env, challenge):
    """Owner task 45, exact shape: review evidence url_contains=youtube.com,
    the live page IS youtube.com, but it is a challenge page and the answer is
    an excuse. Historical verdict: VERIFIED → completed. Must never be."""
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter(EXCUSE)
    stack = await make_stack(env.client, max_retries=0,
                             prompt="Открой в браузере youtube какую то мелодраму на русском")
    await _bind_session(env, stack["task"]["id"], stack["agent"]["id"])
    await _run_once(env)
    data = await _task(env, stack["task"]["id"])
    assert data["task"]["status"] != "completed", data
    meta = data["task"].get("meta") or {}
    history = meta.get("review_history") or []
    assert history and history[-1]["status"] != "VERIFIED", history
    # The state is owner-facing and says what to do — a challenge, not a failure of the model.
    assert data["task"]["status"] in ("waiting_approval", "paused", "blocked")
    assert "challenge" in str(meta.get("reason_code", "")).lower() or \
        any("капч" in str(a.get("preview", "")).lower() or "challenge" in str(a.get("preview", "")).lower()
            for a in await _approvals(env, stack["task"]["id"]))


async def _approvals(env, task_id):
    async with env.svc.db.session() as s:
        return [dict(r._mapping) for r in (await s.execute(
            sa.select(approvals_t).where(approvals_t.c.task_id == task_id))).fetchall()]


async def test_domain_root_is_not_the_goal_when_the_goal_is_to_play_something(env, challenge):
    """`url_contains=youtube.com` on the YouTube front page proves the tab
    opened, not that a video plays. A play/watch goal on a known site derives
    the deeper expectation; the front page alone does not verify it."""
    challenge.captcha = False
    challenge.url = "https://www.youtube.com/"
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("Открыл YouTube, мелодрама играет.")
    stack = await make_stack(env.client, max_retries=0,
                             prompt="Открой в браузере youtube какую то мелодраму на русском")
    await _bind_session(env, stack["task"]["id"], stack["agent"]["id"])
    await _run_once(env)
    assert await _status(env, stack["task"]["id"]) != "completed"


async def test_actual_video_page_after_the_challenge_is_cleared_verifies(env, challenge):
    """Positive control: the owner cleared the challenge, the agent reached a
    watch page. The same task now legitimately completes."""
    challenge.captcha = False
    challenge.url = "https://www.youtube.com/watch?v=abc123"
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("Включил мелодраму.")
    stack = await make_stack(env.client, max_retries=0,
                             prompt="Открой в браузере youtube какую то мелодраму на русском")
    await _bind_session(env, stack["task"]["id"], stack["agent"]["id"])
    await _run_once(env)
    assert await _status(env, stack["task"]["id"]) == "completed"


async def test_a_plain_open_goal_is_satisfied_by_the_domain(env, challenge):
    """`open youtube` asks for the tab, and the tab is what was verified."""
    challenge.captcha = False
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("Открыл YouTube.")
    stack = await make_stack(env.client, max_retries=0, prompt="Открой youtube в браузере")
    await _bind_session(env, stack["task"]["id"], stack["agent"]["id"])
    await _run_once(env)
    assert await _status(env, stack["task"]["id"]) == "completed"


# ------------------------------------------------ Resume: one shot, bound to the task

async def _events(env, kind):
    return [e for e in await env.svc.bus.recent(300) if e.get("kind") == kind]


async def test_owner_resume_continues_the_parked_run_once_and_reaches_the_goal(env, challenge):
    """challenge → paused (owner) → owner clears it and presses Resume on the
    browser session → the SAME run continues → the goal is reached → completed.
    A second Resume finds nothing to resume."""
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter(EXCUSE)
    stack = await make_stack(env.client, max_retries=0, max_steps=2,
                             prompt="Открой в браузере youtube какую то мелодраму на русском")
    task_id = stack["task"]["id"]
    sid = await _bind_session(env, task_id, stack["agent"]["id"])
    await _run_once(env)
    data = await _task(env, task_id)
    assert data["task"]["status"] == "paused", data["task"]["status"]
    assert data["task"]["meta"]["reason_code"] == "WAITING_FOR_OWNER_CHALLENGE"
    assert data["runs"][-1]["status"] == "queued" and data["runs"][-1]["checkpoint"]["note"] == "waiting_for_owner"
    # No approval was created for a challenge: it is not a reviewer decision.
    assert not [a for a in await _approvals(env, task_id) if a["kind"] == "review_escalation"]

    # Owner clears the CAPTCHA in the visible browser; the agent then reaches a watch page.
    challenge.captcha = False
    challenge.url = "https://www.youtube.com/watch?v=melodrama"
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("Включил мелодраму.")
    res = (await env.client.post(f"/api/browser/sessions/{sid}/resume")).json()
    assert res["task_resumed"] is True and res["task_id"] == task_id
    assert await _status(env, task_id) == "queued"
    run_ids = {r["id"] for r in data["runs"]}
    await _run_once(env)
    data = await _task(env, task_id)
    assert data["task"]["status"] == "completed", data
    assert {r["id"] for r in data["runs"]} == run_ids          # continuation, not a new run
    assert "reason_code" not in (data["task"]["meta"] or {})

    # Consumed: a second Resume has nothing to resume and says so.
    res2 = (await env.client.post(f"/api/browser/sessions/{sid}/resume")).json()
    assert res2["task_resumed"] is False
    assert await _status(env, task_id) == "completed"


async def test_resume_with_the_challenge_still_up_parks_again_not_completed(env, challenge):
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter(EXCUSE)
    stack = await make_stack(env.client, max_retries=0, max_steps=2,
                             prompt="Открой в браузере youtube какую то мелодраму на русском")
    task_id = stack["task"]["id"]
    sid = await _bind_session(env, task_id, stack["agent"]["id"])
    await _run_once(env)
    assert await _status(env, task_id) == "paused"
    res = (await env.client.post(f"/api/browser/sessions/{sid}/resume")).json()
    assert res["task_resumed"] is True
    await _run_once(env)
    data = await _task(env, task_id)
    assert data["task"]["status"] == "paused"                  # still the owner's move
    assert data["task"]["meta"]["reason_code"] == "WAITING_FOR_OWNER_CHALLENGE"


async def test_stale_resume_does_not_resurrect_a_stopped_task(env, challenge):
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter(EXCUSE)
    stack = await make_stack(env.client, max_retries=0,
                             prompt="Открой в браузере youtube какую то мелодраму на русском")
    task_id = stack["task"]["id"]
    sid = await _bind_session(env, task_id, stack["agent"]["id"])
    await _run_once(env)
    assert await _status(env, task_id) == "paused"
    assert (await env.client.post(f"/api/tasks/{task_id}/stop")).status_code == 200
    res = (await env.client.post(f"/api/browser/sessions/{sid}/resume")).json()
    assert res["task_resumed"] is False
    assert await _status(env, task_id) == "stopped"


async def test_owner_takeover_then_resume_is_the_same_path(env, challenge):
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter(EXCUSE)
    stack = await make_stack(env.client, max_retries=0, max_steps=2,
                             prompt="Открой в браузере youtube какую то мелодраму на русском")
    task_id = stack["task"]["id"]
    sid = await _bind_session(env, task_id, stack["agent"]["id"])
    await _run_once(env)
    assert (await env.client.post(f"/api/browser/sessions/{sid}/takeover")).status_code == 200
    assert await _status(env, task_id) == "paused"
    challenge.captcha = False
    challenge.url = "https://www.youtube.com/watch?v=x"
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("Готово, играет.")
    assert (await env.client.post(f"/api/browser/sessions/{sid}/resume")).json()["task_resumed"] is True
    await _run_once(env)
    assert await _status(env, task_id) == "completed"


async def test_a_browser_task_without_a_derivable_domain_still_pauses_on_a_challenge(env, challenge):
    """No `review.evidence` can be derived for an unknown site, so the verifier
    never runs; the finalizer's own challenge check must still catch it."""
    challenge.url = "https://example-shop.test/login"
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter(
        "The site shows a CAPTCHA. Please complete it and press Resume.")
    stack = await make_stack(env.client, max_retries=0, prompt="Open my browser and log into the shop")
    task_id = stack["task"]["id"]
    await _bind_session(env, task_id, stack["agent"]["id"], url=challenge.url)
    await _run_once(env)
    data = await _task(env, task_id)
    assert data["task"]["status"] == "paused", data["task"]["status"]
    assert data["task"]["meta"]["reason_code"] == "WAITING_FOR_OWNER_CHALLENGE"
