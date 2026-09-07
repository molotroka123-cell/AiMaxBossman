"""P0-1: у канареечной двери появился ПРОИЗВОДСТВЕННЫЙ вызывающий.

Живая широкая активация в этом дереве ровно одна:
`bcc.v2.skill_evaluation._apply_promotion` переключает `skills.current_version_id`,
а колонки когорты в схеме нет — значит кандидат достаётся ВСЕМУ парку сразу.
До этой правки путь шёл мимо `authorize_broad_activation`: гейт был написан,
привязан и покрыт тестами, но production-вызывающих у него было ноль.

Здесь проверяется именно ВЫЗЫВАЮЩИЙ, а не примитив. Для каждого способа
предъявить негодную улику или негодную личность парк обязан остаться на прежней
версии: подделка, чужой член когорты, чужая ревизия, прошлый прогон, просроченная
улика, чужой процесс, молчащий член, упавший член, обход двери мимо
`_apply_promotion` и перезапуск без долговечного вердикта.

И один положительный сквозной прогон: канарейка -> долговечная улика ->
перезапуск -> активация -> внесённое падение -> отказ -> настоящий откат ->
перезапуск -> состояние отката на месте.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
import sqlalchemy as sa

from bcc.db import (skill_evaluations as evals_t, skill_versions as versions_t,
                    skills as skills_t, task_runs as runs_t, tasks as tasks_t, utcnow)
from bcc.v2 import skill_evaluation as ev

from bossman_shared.objective_activation import ACTIVATED, OPEN, BroadActivationGate
from bossman_shared.objective_canary import CanaryEvidenceLedger, CanaryPlan
from bossman_shared.objective_store import ObjectiveStore


# ------------------------------------------------------------------ фикстуры

async def _skill(env, *, tools=None, perms=None, fingerprint="cand"):
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
            required_tools=tools if tools is not None else ["terminal.run"],
            permissions={"declared": perms if perms is not None else ["terminal"],
                         "fingerprint": fingerprint},
            created_at=utcnow()))).inserted_primary_key[0])
        await s.execute(sa.update(skills_t).where(skills_t.c.id == sid).values(
            current_version_id=base))
        await s.commit()
    return sid, base, cand


async def _runs(env, version_id: int, *, completed: int, failed: int) -> list[int]:
    """История прогонов версии. Возвращает id прогонов в порядке появления."""
    started = utcnow()
    ids: list[int] = []
    async with env.svc.db.session() as s:
        for i, status in enumerate(["completed"] * completed + ["failed"] * failed):
            tid = int((await s.execute(sa.insert(tasks_t).values(
                title=f"прогон {version_id}-{i}", prompt="x", status=status,
                skill_version_id=version_id, meta={"skill": "website-audit"},
                created_at=started, updated_at=started))).inserted_primary_key[0])
            ids.append(int((await s.execute(sa.insert(runs_t).values(
                task_id=tid, attempt=1, status=status, started_at=started,
                finished_at=started + timedelta(seconds=2)))).inserted_primary_key[0]))
        await s.commit()
    return ids


async def _current(env, skill_id: int) -> int:
    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(skills_t.c.current_version_id)
                               .where(skills_t.c.id == skill_id))).first()
    return int(row[0])


async def _fail_run(env, run_id: int) -> None:
    """Внести падение в УЖЕ существующий прогон: состав окна не меняется,
    поэтому прогон канарейки остаётся тем же, а его исход — другим."""
    async with env.svc.db.session() as s:
        await s.execute(sa.update(runs_t).where(runs_t.c.id == run_id).values(status="failed"))
        tid = (await s.execute(sa.select(runs_t.c.task_id)
                               .where(runs_t.c.id == run_id))).first()[0]
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == tid).values(status="failed"))
        await s.commit()


async def _plan_for(env, evrow):
    """Тот же план, что построит производственный путь: те же факты — тот же прогон."""
    cand = await ev._version_row(env.svc, int(evrow["candidate_version_id"]))
    window = await ev.canary_window(env.svc, int(evrow["candidate_version_id"]))
    gate = ev._gate(env.svc)
    plan = gate.plan(subject=f"skill:{int(evrow['skill_id'])}",
                     revision=ev.candidate_revision(cand),
                     members=[m for m, _ in window],
                     started_at=ev._epoch(evrow["created_at"] or utcnow()) - 1.0,
                     run_nonce=f"skill-eval:{int(evrow['id'])}")
    return gate, plan, dict(window)


async def _promotable(env, *, candidate_completed=9, candidate_failed=1):
    """Пара версий, у которой ЦИФРЫ дают PROMOTE. Дверь ещё ничего не решала."""
    sid, base, cand = await _skill(env)
    await _runs(env, base, completed=5, failed=5)                   # 0.50
    cand_runs = await _runs(env, cand, completed=candidate_completed,
                            failed=candidate_failed)
    row = await ev.open_evaluation(env.svc, skill_id=sid, baseline_version_id=base,
                                   candidate_version_id=cand)
    return sid, base, cand, cand_runs, row


def _bound_evidence(plan: CanaryPlan, member: str, *, at: float, key: bytes, **overrides) -> str:
    """Улика, выпущенная ТЕМ ЖЕ ключом, но привязанная к другому плану:
    отличается ровно одна привязка, поэтому проверяется именно она."""
    fields = {"revision_digest": plan.revision_digest, "cohort": plan.cohort,
              "population": plan.population, "started_at": plan.started_at,
              "process_identity": plan.process_identity,
              "cohort_digest": plan.cohort_digest, "run_id": plan.run_id}
    fields.update(overrides)
    return CanaryEvidenceLedger(key).issue(CanaryPlan(**fields), member, issued_at=at)


async def _denied(env, sid, base, row, *, expect: str):
    """Прогнать производственный путь и убедиться, что парк не переключился."""
    result = await ev.refresh(env.svc, int(row["id"]))
    assert result["verdict"] == ev.HUMAN_REVIEW, result["reason"]
    assert result["applied"] is False
    assert expect in result["reason"], result["reason"]
    assert await _current(env, sid) == base
    canary = result["metrics"]["canary"]
    assert canary is not None and canary["allowed"] is False
    return result


# ------------------------------------------------- дверь вообще на пути

async def test_the_promotion_path_now_goes_through_the_canary_door(env):
    """Положительный контроль: разрешённая активация ЗАПИСЫВАЕТ канареечный прогон."""
    sid, base, cand, _, row = await _promotable(env)
    result = await ev.refresh(env.svc, int(row["id"]))
    assert result["verdict"] == ev.PROMOTE and result["applied"] is True
    assert await _current(env, sid) == cand

    canary = result["metrics"]["canary"]
    assert canary["allowed"] is True and canary["state"] == "PASSED"
    # Долговечный след: прогон закрыт РОВНО как активированный, отчёты на месте.
    store = ObjectiveStore(ev._canary_store_path(env.svc))
    run = store.canary_run(canary["run_id"])
    assert run["state"] == ACTIVATED and run["process_identity"] == ev.PROMOTION_IDENTITY
    reports = store.canary_reports(canary["run_id"])
    assert {r["objective_id"] for r in reports} == set(run["cohort"])
    assert all(r["healthy"] and r["evidence_ref"].startswith("cev1.") for r in reports)


# ------------------------------------------------------- ОТРИЦАТЕЛЬНЫЕ

async def test_forged_evidence_denies_promotion(env):
    """Выдуманная строка не разрешается ни в какую привязку."""
    sid, base, cand, _, row = await _promotable(env)
    gate, plan, _ = await _plan_for(env, row)
    gate.open(plan, candidate="x")
    for member in plan.cohort:
        gate.report_raw(plan.run_id, member, healthy=True, at=plan.started_at + 1.0,
                        evidence_ref="cev1.Zm9yZ2Vk." + "0" * 64)
    result = await _denied(env, sid, base, row, expect="canary_evidence_unattested")
    assert {why for _, why in result["metrics"]["canary"]["unattested"]} == {"unresolved"}


async def test_evidence_for_another_member_denies_promotion(env):
    """Улика соседа не является уликой об этом члене когорты."""
    sid, base, cand, _, row = await _promotable(env)
    gate, plan, _ = await _plan_for(env, row)
    gate.open(plan, candidate="x")
    donor, victim = plan.cohort[0], plan.cohort[1]
    stolen = gate.issue_evidence(plan.run_id, donor, at=plan.started_at + 1.0)
    gate.report_raw(plan.run_id, victim, healthy=True, evidence_ref=stolen,
                    at=plan.started_at + 1.0)
    result = await _denied(env, sid, base, row, expect="canary_evidence_unattested")
    assert [victim, "wrong_objective"] in result["metrics"]["canary"]["unattested"]


async def test_evidence_for_another_revision_denies_promotion(env):
    """Улика о другой версии того же скилла эту версию не открывает."""
    sid, base, cand, _, row = await _promotable(env)
    gate, plan, _ = await _plan_for(env, row)
    gate.open(plan, candidate="x")
    key = ev._evidence_key(env.svc)
    for member in plan.cohort:
        gate.report_raw(plan.run_id, member, healthy=True, at=plan.started_at + 1.0,
                        evidence_ref=_bound_evidence(plan, member, at=plan.started_at + 1.0,
                                                     key=key, revision_digest="f" * 64))
    result = await _denied(env, sid, base, row, expect="canary_evidence_unattested")
    assert {why for _, why in result["metrics"]["canary"]["unattested"]} == {"wrong_revision"}


async def test_evidence_from_a_previous_run_denies_promotion(env):
    """Тот же скилл, та же версия, ДРУГОЙ прогон — улика не переносится."""
    sid, base, cand, _, row = await _promotable(env)
    gate, plan, _ = await _plan_for(env, row)
    gate.open(plan, candidate="x")
    key = ev._evidence_key(env.svc)
    for member in plan.cohort:
        gate.report_raw(plan.run_id, member, healthy=True, at=plan.started_at + 1.0,
                        evidence_ref=_bound_evidence(plan, member, at=plan.started_at + 1.0,
                                                     key=key, run_id="e" * 64))
    result = await _denied(env, sid, base, row, expect="canary_evidence_unattested")
    assert {why for _, why in result["metrics"]["canary"]["unattested"]} == {"wrong_run"}


async def test_stale_evidence_denies_promotion(env):
    """Улика, выпущенная до начала прогона, — не улика этого прогона."""
    sid, base, cand, _, row = await _promotable(env)
    gate, plan, _ = await _plan_for(env, row)
    gate.open(plan, candidate="x")
    ledger = CanaryEvidenceLedger(ev._evidence_key(env.svc))
    for member in plan.cohort:
        gate.report_raw(plan.run_id, member, healthy=True, at=plan.started_at + 1.0,
                        evidence_ref=ledger.issue(plan, member,
                                                  issued_at=plan.started_at - 900.0))
    result = await _denied(env, sid, base, row, expect="canary_evidence_unattested")
    assert {why for _, why in result["metrics"]["canary"]["unattested"]} == {"stale"}


async def test_evidence_from_another_process_denies_promotion(env):
    """Улика чужого процесса не открывает парк."""
    sid, base, cand, _, row = await _promotable(env)
    gate, plan, _ = await _plan_for(env, row)
    gate.open(plan, candidate="x")
    key = ev._evidence_key(env.svc)
    for member in plan.cohort:
        gate.report_raw(plan.run_id, member, healthy=True, at=plan.started_at + 1.0,
                        evidence_ref=_bound_evidence(plan, member, at=plan.started_at + 1.0,
                                                     key=key, process_identity="someone-else"))
    result = await _denied(env, sid, base, row, expect="canary_evidence_unattested")
    assert {why for _, why in result["metrics"]["canary"]["unattested"]} == {"wrong_process"}


async def test_another_service_identity_cannot_decide_this_run(env, monkeypatch):
    """Чужая служба не дорешивает чужой прогон, даже с настоящими уликами."""
    sid, base, cand, _, row = await _promotable(env)
    gate, plan, _ = await _plan_for(env, row)
    gate.open(plan, candidate="x")
    for member in plan.cohort:
        gate.report_healthy(plan.run_id, member, at=plan.started_at + 1.0)
    monkeypatch.setattr(ev, "PROMOTION_IDENTITY", "attacker.elsewhere")
    result = await ev.refresh(env.svc, int(row["id"]))
    # Прогон той же личностью не найден, а найденный — чужой: обе двери закрыты.
    assert result["verdict"] == ev.HUMAN_REVIEW and result["applied"] is False
    assert await _current(env, sid) == base
    assert ObjectiveStore(ev._canary_store_path(env.svc)).canary_run(
        plan.run_id)["state"] == OPEN


async def test_a_silent_cohort_member_denies_promotion(env, monkeypatch):
    """Молчание — не успех. В бою это член когорты, чей прогон ещё не завершился."""
    sid, base, cand, _, row = await _promotable(env)
    real = ev.canary_window

    async def _with_a_run_in_flight(svc, version_id):
        window = await real(svc, version_id)
        return [(m, (None if i == 0 else h)) for i, (m, h) in enumerate(window)]

    monkeypatch.setattr(ev, "canary_window", _with_a_run_in_flight)
    result = await _denied(env, sid, base, row, expect="canary_incomplete")
    assert result["metrics"]["canary"]["state"] == "PENDING"
    assert result["metrics"]["canary"]["silent"]


async def test_an_unhealthy_cohort_member_denies_promotion(env):
    """Одного падения в когорте достаточно, хотя ЦИФРЫ дают PROMOTE."""
    sid, base, cand, cand_runs, row = await _promotable(env, candidate_completed=10,
                                                        candidate_failed=0)
    _, plan, _ = await _plan_for(env, row)
    broken = int(plan.cohort[0].split(":")[1])
    await _fail_run(env, broken)                 # состав окна тот же, исход другой
    after, _, _ = await _plan_for(env, row)
    result = await _denied(env, sid, base, row, expect="canary_failed")
    assert result["metrics"]["canary"]["state"] == "FAILED"
    assert result["metrics"]["canary"]["unhealthy"] == [f"run:{broken}"]
    # И цифры при этом действительно были за продвижение.
    assert result["metrics"]["delta_success_rate"] >= ev.IMPROVE_DELTA


async def test_registering_a_version_no_longer_promotes_it(env):
    """ОБХОД ДВЕРИ: регистрация отпечатка достигала того же состояния, что и
    `_apply_promotion`, минуя измерение, проверку расширения прав, событие и
    строку сравнения. Теперь регистрация не переключает текущую версию."""
    from bcc.features.skills import _persist_skill_version

    class _Contract:
        id = "website-audit"
        name = "аудит сайта"
        version = "2"
        fingerprint = "brand-new-fingerprint"
        input_schema: dict = {}
        output_schema: dict = {}
        # Кандидат, который просит СВЕРХ baseline: раньше он становился текущим
        # просто от регистрации, а `widened_capabilities` не спрашивали вовсе.
        required_tools = ["terminal.run", "browser.open"]
        process = "x"
        permissions = ["terminal", "browser"]

    sid, base, _ = await _skill(env)
    vid = await _persist_skill_version(env.svc, _Contract(), "")
    assert vid and await _current(env, sid) == base

    # У скилла без текущей версии первая регистрация её проставляет: там нет
    # ни baseline, ни парка, который что-то теряет.
    async with env.svc.db.session() as s:
        await s.execute(sa.update(skills_t).where(skills_t.c.id == sid).values(
            current_version_id=None))
        await s.commit()

    class _Another(_Contract):
        fingerprint = "first-of-its-kind"

    first = await _persist_skill_version(env.svc, _Another(), "")
    assert await _current(env, sid) == first


async def test_applying_without_a_grant_is_refused(env):
    """Дверь нельзя обойти и изнутри: `_apply_promotion` без разрешения падает."""
    from bossman_shared.objective_activation import (ActivationError, BroadActivationGrant,
                                                     REASON_BAD_GRANT, REASON_NO_GRANT)
    sid, base, cand, _, row = await _promotable(env)
    gate, plan, _ = await _plan_for(env, row)
    gate.open(plan, candidate="x")

    with pytest.raises(ActivationError, match=REASON_NO_GRANT):
        await ev._apply_promotion(env.svc, sid, cand, gate=gate, run_id=plan.run_id, grant=None)
    forged = BroadActivationGrant(run_id=plan.run_id, revision_digest=plan.revision_digest,
                                  cohort_digest=plan.cohort_digest,
                                  process_identity=ev.PROMOTION_IDENTITY,
                                  granted_at=ev._epoch(utcnow()), reason="canary_passed",
                                  token="0" * 64)
    with pytest.raises(ActivationError, match=REASON_BAD_GRANT):
        await ev._apply_promotion(env.svc, sid, cand, gate=gate, run_id=plan.run_id,
                                  grant=forged)
    with pytest.raises(ActivationError):
        await ev._apply_promotion(env.svc, sid, cand, gate=None, run_id="", grant=None)
    assert await _current(env, sid) == base
    assert ObjectiveStore(ev._canary_store_path(env.svc)).canary_run(
        plan.run_id)["state"] == OPEN


async def test_a_human_approval_does_not_bypass_the_canary(env):
    """Одобрение владельца решает СПОРНОЕ, но не делает непроверенное проверенным."""
    sid, base, cand, _, _ = await _promotable(env, candidate_completed=6, candidate_failed=4)
    async with env.svc.db.session() as s:                       # шум: 0.50 -> 0.60
        pass
    row = await ev.open_evaluation(env.svc, skill_id=sid, baseline_version_id=base,
                                   candidate_version_id=cand)
    result = await ev.refresh(env.svc, int(row["id"]))
    assert result["verdict"] == ev.HUMAN_REVIEW and result["applied"] is False

    _, plan, _ = await _plan_for(env, row)
    broken = int(plan.cohort[0].split(":")[1])
    await _fail_run(env, broken)
    with pytest.raises(ValueError, match="канареечная дверь закрыта"):
        await ev.apply_human_decision(env.svc, int(row["id"]), approve=True, by="владелец")
    assert await _current(env, sid) == base


async def test_a_restart_without_the_durable_run_denies(env, monkeypatch, tmp_path):
    """Перезапуск без долговечного вердикта ничего не активирует.

    Улики, отчёты и решение живут в хранилище; процесс, который их не видит,
    обязан отказать, а не «поверить, что всё было хорошо»."""
    sid, base, cand, _, row = await _promotable(env)
    gate, plan, _ = await _plan_for(env, row)
    gate.open(plan, candidate="x")
    for member in plan.cohort:
        gate.report_healthy(plan.run_id, member, at=plan.started_at + 1.0)

    # Перезапуск, потерявший долговечное состояние: тот же процесс, пустой стор.
    monkeypatch.setenv(ev.CANARY_STORE_ENV, str(tmp_path / "lost" / "canary.sqlite3"))
    result = await ev.refresh(env.svc, int(row["id"]))
    # Прогон заводится заново, но отчётов нет: молчание, а не унаследованный успех.
    assert result["verdict"] == ev.HUMAN_REVIEW and result["applied"] is False
    assert "canary_incomplete" in result["reason"]
    assert await _current(env, sid) == base


async def test_a_restart_without_the_evidence_key_denies(env, monkeypatch):
    """Новый процесс без ключа улик не доверяет им «на слово»."""
    sid, base, cand, _, row = await _promotable(env)
    gate, plan, _ = await _plan_for(env, row)
    gate.open(plan, candidate="x")
    for member in plan.cohort:
        gate.report_healthy(plan.run_id, member, at=plan.started_at + 1.0)
    monkeypatch.setattr(ev, "_evidence_key", lambda svc: b"a-completely-different-key!!")
    await _denied(env, sid, base, row, expect="canary_evidence_unattested")


# ------------------------------------------------------------ ПОЛОЖИТЕЛЬНЫЙ

async def test_the_real_sequence_canary_restart_activation_failure_rollback_restart(env):
    """Канарейка -> долговечная улика -> ПЕРЕЗАПУСК -> активация -> внесённое
    падение -> отказ -> НАСТОЯЩИЙ откат -> ПЕРЕЗАПУСК -> откат на месте.

    «Перезапуск» настоящий: каждая проверка берёт НОВУЮ ручку хранилища и новый
    объект двери, читающие только долговечное состояние.
    """
    sid, base, cand = await _skill(env)
    await _runs(env, base, completed=5, failed=5)                    # v1: 0.50
    await _runs(env, cand, completed=9, failed=1)                    # v2: 0.90

    first = await ev.open_evaluation(env.svc, skill_id=sid, baseline_version_id=base,
                                     candidate_version_id=cand)

    # --- Канарейка: отчёты когорты ложатся в ХРАНИЛИЩЕ до всякой активации.
    gate, plan, _ = await _plan_for(env, first)
    gate.open(plan, candidate="x")
    for member in plan.cohort:
        gate.report_healthy(plan.run_id, member, at=plan.started_at + 1.0)
    assert gate.state(plan.run_id) == OPEN
    del gate

    # --- ПЕРЕЗАПУСК: новая ручка стора и новая дверь видят те же улики.
    fresh = BroadActivationGate(ObjectiveStore(ev._canary_store_path(env.svc)),
                                evidence_key=ev._evidence_key(env.svc),
                                process_identity=ev.PROMOTION_IDENTITY,
                                owner_id="owner")
    assert len(fresh.reported_members(plan.run_id)) == len(plan.cohort)

    # --- Активация разрешена и произошла ровно один раз.
    promoted = await ev.refresh(env.svc, int(first["id"]))
    assert promoted["verdict"] == ev.PROMOTE and promoted["applied"] is True
    assert await _current(env, sid) == cand
    assert ObjectiveStore(ev._canary_store_path(env.svc)).canary_run(
        plan.run_id)["state"] == ACTIVATED

    # --- Внесённое падение: v2 разваливается, а третья версия просится на парк
    #     с падением ВНУТРИ своей канареечной когорты.
    await _runs(env, cand, completed=0, failed=20)                   # v2: 9/30 = 0.30
    async with env.svc.db.session() as s:
        third = int((await s.execute(sa.insert(versions_t).values(
            skill_id=sid, version=3, required_tools=["terminal.run"],
            permissions={"declared": ["terminal"], "fingerprint": "third"},
            created_at=utcnow()))).inserted_primary_key[0])
        await s.commit()
    await _runs(env, third, completed=10, failed=0)                  # цифры за продвижение
    second = await ev.open_evaluation(env.svc, skill_id=sid, baseline_version_id=cand,
                                      candidate_version_id=third)
    _, third_plan, _ = await _plan_for(env, second)
    await _fail_run(env, int(third_plan.cohort[0].split(":")[1]))

    denied = await ev.refresh(env.svc, int(second["id"]))
    assert denied["verdict"] == ev.HUMAN_REVIEW and denied["applied"] is False
    assert "canary_failed" in denied["reason"]
    assert await _current(env, sid) == cand                          # парк не тронут

    # --- НАСТОЯЩИЙ откат: возврат на v1 идёт через ТУ ЖЕ дверь, а не мимо неё.
    back = await ev.open_evaluation(env.svc, skill_id=sid, baseline_version_id=cand,
                                    candidate_version_id=base)
    rolled = await ev.refresh(env.svc, int(back["id"]))
    assert rolled["verdict"] == ev.PROMOTE and rolled["applied"] is True
    assert await _current(env, sid) == base

    # --- ПЕРЕЗАПУСК: откат и оба вердикта пережили его как долговечные факты.
    store = ObjectiveStore(ev._canary_store_path(env.svc))
    assert await _current(env, sid) == base
    assert store.canary_run(plan.run_id)["state"] == ACTIVATED
    assert store.canary_run(third_plan.run_id)["state"] == OPEN      # так и не активирован
    assert any(r["healthy"] is False for r in store.canary_reports(third_plan.run_id))
    assert store.canary_run(rolled["metrics"]["canary"]["run_id"])["state"] == ACTIVATED

    # Повторно активировать израсходованный прогон нельзя даже после перезапуска.
    again = ev._gate(env.svc).authorize(rolled["metrics"]["canary"]["run_id"],
                                        now=ev._epoch(utcnow()))
    assert again.allowed is False and again.reason == "canary_run_not_open"
