"""V2.2 §7 — петля самообучения: сравнение версий скилла и ОГРАНИЧЕННЫЕ переходы.

Что здесь доказывается, а не декларируется:

  * сравнение привязано к ВЕРСИЯМ скилла, а не к task/run — именно этого не
    хватало Skill Evaluator'у (см. docs/NIGHT_HANDOFF.md §4);
  * рантайм выносит вердикт сам только там, где условия жёсткие: мало данных —
    честное `collecting`, спорная разница — HUMAN_REVIEW с approval;
  * кандидат, просящий права сверх baseline, НЕ получает автоматический PROMOTE
    даже при заметно лучших цифрах;
  * PROMOTE меняет ровно одно поле (`skills.current_version_id`);
  * провал скилла ПРЕДЛАГАЕТ разбор провала, но не запускает его.
"""
from __future__ import annotations

from datetime import timedelta

import sqlalchemy as sa

from bcc.db import (approvals as approvals_t, skill_evaluations as evals_t,
                    skill_versions as versions_t, skills as skills_t,
                    task_runs as runs_t, tasks as tasks_t, utcnow)
from bcc.features.skills import RETRO_SKILL, _after_skill_run
from bcc.v2 import skill_evaluation as ev


# ------------------------------------------------------------------ фикстуры данных

async def _skill_with_versions(env, *, candidate_tools=None, candidate_perms=None):
    """Скилл с двумя версиями: baseline и кандидат."""
    async with env.svc.db.session() as s:
        sid = int((await s.execute(sa.insert(skills_t).values(
            name="аудит сайта", slug="website-audit", description="",
            created_at=utcnow()))).inserted_primary_key[0])
        base = int((await s.execute(sa.insert(versions_t).values(
            skill_id=sid, version=1, required_tools=["terminal.run"],
            permissions={"declared": ["terminal"], "fingerprint": "base"},
            created_at=utcnow()))).inserted_primary_key[0])
        cand = int((await s.execute(sa.insert(versions_t).values(
            skill_id=sid, version=2,
            required_tools=candidate_tools if candidate_tools is not None else ["terminal.run"],
            permissions={"declared": candidate_perms if candidate_perms is not None
                         else ["terminal"], "fingerprint": "cand"},
            created_at=utcnow()))).inserted_primary_key[0])
        await s.execute(sa.update(skills_t).where(skills_t.c.id == sid).values(
            current_version_id=base))
        await s.commit()
    return sid, base, cand


async def _runs(env, version_id: int, *, completed: int, failed: int) -> None:
    """Готовая история запусков версии: одна задача — один run."""
    started = utcnow()
    async with env.svc.db.session() as s:
        for i, status in enumerate(["completed"] * completed + ["failed"] * failed):
            tid = int((await s.execute(sa.insert(tasks_t).values(
                title=f"прогон {version_id}-{i}", prompt="x", status=status,
                skill_version_id=version_id, meta={"skill": "website-audit"},
                created_at=started, updated_at=started))).inserted_primary_key[0])
            await s.execute(sa.insert(runs_t).values(
                task_id=tid, attempt=1, status=status, started_at=started,
                finished_at=started + timedelta(seconds=2)))
        await s.commit()


async def _current_version(env, skill_id: int) -> int:
    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(skills_t.c.current_version_id)
                               .where(skills_t.c.id == skill_id))).first()
    return int(row[0])


# ------------------------------------------------------------------ метрики и границы

async def test_metrics_count_runs_not_tasks_and_ignore_stopped(env):
    _, base, _ = await _skill_with_versions(env)
    await _runs(env, base, completed=3, failed=1)
    async with env.svc.db.session() as s:                       # ручной стоп — не исход
        tid = int((await s.execute(sa.insert(tasks_t).values(
            title="остановлена", prompt="x", status="stopped", skill_version_id=base,
            meta={"skill": "website-audit"}, created_at=utcnow(),
            updated_at=utcnow()))).inserted_primary_key[0])
        await s.execute(sa.insert(runs_t).values(task_id=tid, attempt=1, status="stopped"))
        await s.commit()

    metrics = await ev.version_metrics(env.svc, base)
    assert metrics["runs"] == 4 and metrics["completed"] == 3
    assert metrics["success_rate"] == 0.75
    assert metrics["avg_duration_ms"] == 2000


async def test_not_enough_data_is_collecting_not_a_verdict(env):
    sid, base, cand = await _skill_with_versions(env)
    await _runs(env, base, completed=5, failed=0)
    await _runs(env, cand, completed=2, failed=0)               # меньше MIN_RUNS

    row = await ev.open_evaluation(env.svc, skill_id=sid, baseline_version_id=base,
                                   candidate_version_id=cand)
    result = await ev.refresh(env.svc, int(row["id"]))
    assert result["status"] == "collecting" and result["verdict"] is None
    assert f"{ev.MIN_RUNS}" in result["reason"]
    assert await _current_version(env, sid) == base             # ничего не переключилось


async def test_clear_improvement_promotes_and_switches_current_version(env):
    sid, base, cand = await _skill_with_versions(env)
    await _runs(env, base, completed=5, failed=5)               # 0.50
    await _runs(env, cand, completed=9, failed=1)               # 0.90

    row = await ev.open_evaluation(env.svc, skill_id=sid, baseline_version_id=base,
                                   candidate_version_id=cand)
    result = await ev.refresh(env.svc, int(row["id"]))
    assert result["verdict"] == ev.PROMOTE and result["applied"] is True
    assert result["decided_by"] == "runtime"
    assert result["metrics"]["delta_success_rate"] == 0.4
    assert await _current_version(env, sid) == cand

    # PROMOTE не трогает ничего, кроме текущей версии: сами версии на месте
    async with env.svc.db.session() as s:
        kept = (await s.execute(sa.select(versions_t.c.id)
                                .where(versions_t.c.skill_id == sid))).fetchall()
    assert sorted(int(r[0]) for r in kept) == sorted([base, cand])


async def test_regression_is_rejected_and_current_version_stays(env):
    sid, base, cand = await _skill_with_versions(env)
    await _runs(env, base, completed=9, failed=1)
    await _runs(env, cand, completed=4, failed=6)

    row = await ev.open_evaluation(env.svc, skill_id=sid, baseline_version_id=base,
                                   candidate_version_id=cand)
    result = await ev.refresh(env.svc, int(row["id"]))
    assert result["verdict"] == ev.REJECT and result["applied"] is False
    assert await _current_version(env, sid) == base


async def test_noise_goes_to_human_with_an_approval(env):
    sid, base, cand = await _skill_with_versions(env)
    await _runs(env, base, completed=12, failed=8)              # 0.60
    await _runs(env, cand, completed=13, failed=7)              # 0.65 — в пределах шума

    row = await ev.open_evaluation(env.svc, skill_id=sid, baseline_version_id=base,
                                   candidate_version_id=cand)
    result = await ev.refresh(env.svc, int(row["id"]))
    assert result["verdict"] == ev.HUMAN_REVIEW and result["applied"] is False
    assert result["approval_id"]
    assert await _current_version(env, sid) == base

    async with env.svc.db.session() as s:
        appr = (await s.execute(sa.select(approvals_t)
                                .where(approvals_t.c.id == result["approval_id"]))).first()
    assert appr._mapping["kind"] == "skill_promotion"
    assert appr._mapping["status"] == "pending"

    # применить спорного кандидата может только человек
    decided = await ev.apply_human_decision(env.svc, int(row["id"]), approve=True, by="владелец")
    assert decided["verdict"] == ev.PROMOTE and decided["applied"] is True
    assert decided["decided_by"] == "владелец"
    assert await _current_version(env, sid) == cand


async def test_widened_permissions_never_auto_promote(env):
    """Даже с явно лучшими цифрами: расширение прав — только через человека."""
    sid, base, cand = await _skill_with_versions(
        env, candidate_tools=["terminal.run", "browser.open"],
        candidate_perms=["terminal", "browser"])
    await _runs(env, base, completed=5, failed=5)               # 0.50
    await _runs(env, cand, completed=10, failed=0)              # 1.00

    row = await ev.open_evaluation(env.svc, skill_id=sid, baseline_version_id=base,
                                   candidate_version_id=cand)
    result = await ev.refresh(env.svc, int(row["id"]))
    assert result["verdict"] == ev.HUMAN_REVIEW
    assert result["metrics"]["widened"] == ["permission:browser", "tool:browser.open"]
    assert await _current_version(env, sid) == base

    # отказ человека фиксируется как REJECT и ничего не переключает
    decided = await ev.apply_human_decision(env.svc, int(row["id"]), approve=False, by="владелец")
    assert decided["verdict"] == ev.REJECT and decided["applied"] is False
    assert await _current_version(env, sid) == base


async def test_decided_evaluation_is_not_replayed(env):
    sid, base, cand = await _skill_with_versions(env)
    await _runs(env, base, completed=5, failed=5)
    await _runs(env, cand, completed=9, failed=1)
    row = await ev.open_evaluation(env.svc, skill_id=sid, baseline_version_id=base,
                                   candidate_version_id=cand)
    first = await ev.refresh(env.svc, int(row["id"]))
    assert first["verdict"] == ev.PROMOTE

    # вердикт — событие, а не мнение: новые провалы его не переигрывают
    await _runs(env, cand, completed=0, failed=20)
    again = await ev.refresh(env.svc, int(row["id"]))
    assert again["verdict"] == ev.PROMOTE
    assert again["metrics"] == first["metrics"]


async def test_pair_is_unique_and_self_comparison_refused(env):
    sid, base, cand = await _skill_with_versions(env)
    first = await ev.open_evaluation(env.svc, skill_id=sid, baseline_version_id=base,
                                     candidate_version_id=cand)
    second = await ev.open_evaluation(env.svc, skill_id=sid, baseline_version_id=base,
                                      candidate_version_id=cand)
    assert first["id"] == second["id"]

    import pytest
    with pytest.raises(ValueError):
        await ev.open_evaluation(env.svc, skill_id=sid, baseline_version_id=base,
                                 candidate_version_id=base)


# ------------------------------------------------------------------ переход «провал → разбор»

async def _failed_skill_task(env, slug: str = "website-audit") -> tuple[int, int]:
    async with env.svc.db.session() as s:
        tid = int((await s.execute(sa.insert(tasks_t).values(
            title="ночная миссия", prompt="x", status="failed",
            meta={"skill": slug}, created_at=utcnow(),
            updated_at=utcnow()))).inserted_primary_key[0])
        rid = int((await s.execute(sa.insert(runs_t).values(
            task_id=tid, attempt=1, status="failed",
            error="провайдер вернул 500"))).inserted_primary_key[0])
        await s.commit()
    return tid, rid


async def test_failed_skill_run_proposes_retrospective_but_runs_nothing(env):
    tid, rid = await _failed_skill_task(env)
    queue = env.svc.bus.subscribe()
    before = len((await env.client.get("/api/tasks")).json())

    await _after_skill_run(env.svc, tid, rid, "failed")

    listed = (await env.client.get("/api/skill-retrospectives")).json()
    assert listed["retro_skill"] == RETRO_SKILL
    assert [x["task_id"] for x in listed["pending"]] == [tid]
    assert listed["pending"][0]["error"] == "провайдер вернул 500"

    kinds = []
    while not queue.empty():
        kinds.append(queue.get_nowait()["kind"])
    env.svc.bus.unsubscribe(queue)
    assert "skill.retrospective.proposed" in kinds

    # ключевое: предложено, но НЕ запущено — новых задач не появилось
    assert len((await env.client.get("/api/tasks")).json()) == before


async def test_retrospective_proposal_has_cooldown_and_dismiss(env):
    tid, rid = await _failed_skill_task(env)
    await _after_skill_run(env.svc, tid, rid, "failed")

    tid2, rid2 = await _failed_skill_task(env)                  # тот же скилл, сразу следом
    await _after_skill_run(env.svc, tid2, rid2, "failed")
    listed = (await env.client.get("/api/skill-retrospectives")).json()["pending"]
    assert [x["task_id"] for x in listed] == [tid]              # кулдаун сдержал лавину

    assert (await env.client.post(f"/api/skill-retrospectives/{tid}/dismiss")).status_code == 200
    assert (await env.client.get("/api/skill-retrospectives")).json()["pending"] == []
    assert (await env.client.post(f"/api/skill-retrospectives/{tid}/dismiss")).status_code == 404


async def test_retrospective_of_retrospective_is_not_proposed(env):
    tid, rid = await _failed_skill_task(env, slug=RETRO_SKILL)
    await _after_skill_run(env.svc, tid, rid, "failed")
    assert (await env.client.get("/api/skill-retrospectives")).json()["pending"] == []


async def test_non_skill_task_is_untouched(env):
    async with env.svc.db.session() as s:
        tid = int((await s.execute(sa.insert(tasks_t).values(
            title="обычная задача", prompt="x", status="failed", meta={},
            created_at=utcnow(), updated_at=utcnow()))).inserted_primary_key[0])
        rid = int((await s.execute(sa.insert(runs_t).values(
            task_id=tid, attempt=1, status="failed"))).inserted_primary_key[0])
        await s.commit()
    await _after_skill_run(env.svc, tid, rid, "failed")
    assert (await env.client.get("/api/skill-retrospectives")).json()["pending"] == []


async def test_successful_run_refreshes_evaluation_through_the_hook(env):
    """Тот же хук, что и в бою, доводит сравнение до вердикта без ручного refresh."""
    sid, base, cand = await _skill_with_versions(env)
    await _runs(env, base, completed=5, failed=5)
    await _runs(env, cand, completed=8, failed=1)
    row = await ev.open_evaluation(env.svc, skill_id=sid, baseline_version_id=base,
                                   candidate_version_id=cand)
    assert (await ev.refresh(env.svc, int(row["id"])))["status"] == "decided"

    # новая пара, ещё не набравшая данных, дозревает именно через хук
    async with env.svc.db.session() as s:
        third = int((await s.execute(sa.insert(versions_t).values(
            skill_id=sid, version=3, required_tools=["terminal.run"],
            permissions={"declared": ["terminal"], "fingerprint": "third"},
            created_at=utcnow()))).inserted_primary_key[0])
        await s.commit()
    pair = await ev.open_evaluation(env.svc, skill_id=sid, baseline_version_id=base,
                                    candidate_version_id=third)
    assert (await ev.refresh(env.svc, int(pair["id"])))["status"] == "collecting"

    await _runs(env, third, completed=10, failed=0)
    async with env.svc.db.session() as s:
        last = (await s.execute(sa.select(runs_t.c.id, runs_t.c.task_id)
                                .order_by(runs_t.c.id.desc()).limit(1))).first()
    await _after_skill_run(env.svc, int(last[1]), int(last[0]), "completed")

    async with env.svc.db.session() as s:
        after = (await s.execute(sa.select(evals_t)
                                 .where(evals_t.c.id == pair["id"]))).first()._mapping
    assert after["status"] == "decided" and after["verdict"] == ev.PROMOTE
    assert await _current_version(env, sid) == third
