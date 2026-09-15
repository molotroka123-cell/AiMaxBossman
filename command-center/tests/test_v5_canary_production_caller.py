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
    # В бою улику пишет хук `after_run` в момент терминального исхода КАЖДОГО
    # прогона. Помощник вставляет прогоны прямо в базу, минуя движок, поэтому тот
    # же переход воспроизводится здесь. До заведения сравнения это ничего не
    # пишет — именно так отсечка и работает.
    for rid, status in zip(ids, ["completed"] * completed + ["failed"] * failed):
        await ev.record_canary_outcome(env.svc, version_id, rid, status)
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


def _run_at(plan, window, member: str) -> str:
    """Прогон, занимающий место `member` когорты. Места нумеруются с единицы."""
    names = list(window)
    position = int(member.split(":")[1])
    return names[position - 1]


async def _running_run(env, version_id: int) -> int:
    """Прогон кандидата, который ЕЩЁ ИДЁТ: член набора без терминального исхода.

    Он не получает улики, потому что улику пишет только терминальный переход.
    Это и есть молчание в его честном виде — не отозванная улика и не
    испорченная запись, а член, который просто ещё не ответил.
    """
    started = utcnow()
    async with env.svc.db.session() as s:
        tid = int((await s.execute(sa.insert(tasks_t).values(
            title=f"идущий прогон {version_id}", prompt="x", status="running",
            skill_version_id=version_id, meta={"skill": "website-audit"},
            created_at=started, updated_at=started))).inserted_primary_key[0])
        rid = int((await s.execute(sa.insert(runs_t).values(
            task_id=tid, attempt=1, status="running",
            started_at=started))).inserted_primary_key[0])
        await s.commit()
    return rid


async def _plan_for(env, evrow):
    """Тот же план, что построит производственный путь: те же факты — тот же прогон.

    План берётся у САМОГО производственного кода, а не воспроизводится здесь по
    памяти. Собственная копия построения плана однажды уже разошлась с боевой
    (писатель улики и решение строили разные когорты и потому разные прогоны),
    и стенд этого не видел, потому что повторял ошибку вместе с ней.
    """
    cand = await ev._version_row(env.svc, int(evrow["candidate_version_id"]))
    window = await ev.canary_window(env.svc, int(evrow["candidate_version_id"]),
                                    after=ev._cutoff_of(dict(evrow)))
    gate = ev._gate(env.svc)
    plan = ev._cohort_plan(gate, dict(evrow), cand)
    return gate, plan, dict(window)


async def _promotable(env, *, candidate_completed=10, candidate_failed=0):
    """Пара версий, у которой ЦИФРЫ дают PROMOTE. Дверь ещё ничего не решала.

    Порядок здесь — производственный, и он существенен. Сравнение заводится
    ПЕРЕД прогонами кандидата: именно в этот момент замораживается отсечка
    когорты, и только завершившиеся после неё прогоны могут стать канареечной
    уликой. История, набранная раньше, канарейкой не является.

    Кандидат по умолчанию ЧИСТЫЙ (без падений): политика нулевой терпимости
    вместе с вето по известному провалу означает, что кандидат с падением не
    продвигается вообще — поэтому «продвигаемая» пара обязана быть здоровой.
    """
    sid, base, cand = await _skill(env)
    await _runs(env, base, completed=5, failed=5)                   # 0.50
    row = await ev.open_evaluation(env.svc, skill_id=sid, baseline_version_id=base,
                                   candidate_version_id=cand)
    cand_runs = await _runs(env, cand, completed=candidate_completed,
                            failed=candidate_failed)
    return sid, base, cand, cand_runs, row


async def _record_outcomes(env, version_id: int, ids: list[int], *,
                           completed: int, failed: int) -> None:
    """Тот же переход, что и в бою: терминальный исход -> канареечная улика."""
    for rid, status in zip(ids, ["completed"] * completed + ["failed"] * failed):
        await ev.record_canary_outcome(env.svc, version_id, rid, status)


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


async def test_a_silent_cohort_member_denies_promotion(env):
    """Молчание — не успех: член измеренного набора без терминального исхода.

    Прогон здесь по-настоящему не закончен, а не подменён патчем окна: он
    заведён со статусом `running` и потому не оставил улики. Прежняя редакция
    подменяла `canary_window`, то есть проверяла реакцию на выдуманное окно, а
    не на реально молчащего члена.
    """
    sid, base, cand, _, row = await _promotable(env)
    silent_run = await _running_run(env, cand)
    result = await _denied(env, sid, base, row, expect="canary_incomplete")
    assert result["metrics"]["canary"]["state"] == "PENDING"
    assert f"run:{silent_run}" in result["metrics"]["canary"]["silent"]


async def test_an_unhealthy_cohort_member_denies_promotion(env):
    """Одного НАСТОЯЩЕГО падения в измеренном наборе достаточно.

    Прогон падает сам, обычным путём, и его улика пишется один раз на
    терминальном переходе. Терминальный исход неизменяем: доводить прогон до
    `completed` и потом править строку на `failed` — это подделка исхода, а не
    канареечный отказ, и такой стенд проверял бы реакцию на испорченную запись
    вместо реакции на реальный провал.
    """
    sid, base, cand, cand_runs, row = await _promotable(env, candidate_completed=9,
                                                        candidate_failed=1)
    broken = int(cand_runs[-1])                  # последний прогон завершился падением
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
    """Одобрение владельца решает СПОРНОЕ, но не делает непроверенное проверенным.

    Провал здесь НАСТОЯЩИЙ: четыре прогона кандидата завершились неудачей сами,
    по обычному пути. Прежняя редакция доводила прогон до `completed`, а потом
    правила его строку на `failed` прямо в базе — это подделка терминального
    исхода, а не канареечный отказ, и она проверяла реакцию на испорченную
    запись вместо реакции на реальный провал.
    """
    sid, base, cand, _, _ = await _promotable(env, candidate_completed=6, candidate_failed=4)
    row = await ev.open_evaluation(env.svc, skill_id=sid, baseline_version_id=base,
                                   candidate_version_id=cand)
    result = await ev.refresh(env.svc, int(row["id"]))
    assert result["verdict"] == ev.HUMAN_REVIEW and result["applied"] is False

    # Человек одобряет. Отказ двери — записанный исход, а не исключение: владелец
    # обязан увидеть ОСНОВАНИЕ отказа, а не только то, что вызов не прошёл.
    decided = await ev.apply_human_decision(env.svc, int(row["id"]), approve=True, by="владелец")
    assert decided["applied"] is False
    assert decided["verdict"] == ev.HUMAN_REVIEW
    assert "канареечной дверью" in decided["reason"]
    assert decided["decided_by"] == "владелец"
    assert decided["metrics"]["canary"]["allowed"] is False
    # И главное: версия не переключилась.
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

    # Сравнение заводится ДО прогонов кандидата: здесь замораживается отсечка,
    # и только завершившиеся после неё прогоны становятся когортой. Кандидат
    # чист — при нулевой терпимости иначе продвигать нечего.
    first = await ev.open_evaluation(env.svc, skill_id=sid, baseline_version_id=base,
                                     candidate_version_id=cand)
    await _runs(env, cand, completed=10, failed=0)                   # v2: 1.00

    # --- Канарейка: улики уже в ХРАНИЛИЩЕ, и положил их туда НЕ этот стенд.
    #
    # Каждый прогон выше записал свой исход на терминальном переходе — тем же
    # путём, что и в бою. Раньше здесь докладывали здоровье вручную через
    # `gate.report_healthy`; это чеканка улики стендом, то есть ровно то, чего
    # дверь и обязана не принимать на веру. Теперь стенд только ПРОВЕРЯЕТ, что
    # улика появилась сама.
    _, plan, _ = await _plan_for(env, first)
    store_before = ObjectiveStore(ev._canary_store_path(env.svc))
    assert store_before.canary_run(plan.run_id)["state"] == OPEN
    reported_before = {r["objective_id"] for r in store_before.canary_reports(plan.run_id)}
    assert set(plan.cohort) <= reported_before
    assert all(r["healthy"] for r in store_before.canary_reports(plan.run_id))
    del store_before

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
    second = await ev.open_evaluation(env.svc, skill_id=sid, baseline_version_id=cand,
                                      candidate_version_id=third)
    # Падение НАСТОЯЩЕЕ: один прогон третьей версии завершается неудачей сам.
    # Прежняя редакция доводила прогон до `completed` и потом правила строку в
    # базе — теперь такой переход отвергается на уровне БД
    # (см. tests/test_v5_terminal_run_immutability.py), и это правильно:
    # подделанный исход не должен был проходить и раньше.
    # Падение попадает В КОГОРТУ (третьим прогоном), а не в хвост измеренного
    # набора: тогда на терминальном переходе пишется НАСТОЯЩАЯ улика «нездоров»,
    # и отказ опирается на долговечный отчёт, а не только на вето по строке
    # прогона. Оба механизма обязаны работать, но именно этот оставляет след.
    early = await _runs(env, third, completed=2, failed=1)
    broken_run = int(early[-1])
    await _runs(env, third, completed=7, failed=0)                   # цифры за продвижение
    _, third_plan, _ = await _plan_for(env, second)

    denied = await ev.refresh(env.svc, int(second["id"]))
    assert denied["verdict"] == ev.HUMAN_REVIEW and denied["applied"] is False
    assert "canary_failed" in denied["reason"]
    assert denied["metrics"]["canary"]["unhealthy"] == [f"run:{broken_run}"]
    assert await _current(env, sid) == cand                          # парк не тронут

    # --- НАСТОЯЩИЙ откат: возврат на v1 идёт через ТУ ЖЕ дверь, а не мимо неё.
    back = await ev.open_evaluation(env.svc, skill_id=sid, baseline_version_id=cand,
                                    candidate_version_id=base)
    # Откат тоже проходит дверь: прежняя репутация v1 канарейкой не является,
    # поэтому возврат зарабатывает СВОЮ проспективную здоровую когорту.
    await _runs(env, base, completed=10, failed=0)
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


# ---------------------------------------------------- контракт HUMAN_REVIEW
#
# Две РАЗНЫЕ ситуации, которые нельзя мерить одной меркой:
#
#   «неопределённое качество» — цифры в пределах шума, но канареечная когорта
#   чиста: ни одного падения, ни одного молчащего члена. Здесь владелец
#   полномочен: он решает вопрос ВКУСА, беря на себя статистическую
#   неопределённость.
#
#   «известная небезопасность» — в измеренном наборе есть настоящее падение.
#   Здесь владелец не полномочен: он не может объявить упавший прогон здоровым,
#   потому что это вопрос ФАКТА, а не воли.
#
# Раньше эти два случая жили в одном тесте, и когда политика заморозки закрыла
# второй, первый исчез вместе с ним — то есть перестало проверяться, что
# одобрение человека вообще ещё работает. Ниже они разделены явно.


async def test_owner_may_approve_a_noisy_candidate_with_a_clean_canary(env):
    """А. Цифры спорные, канарейка ЧИСТАЯ -> владелец вправе продвинуть.

    Спорность берётся из истории кандидата ДО отсечки, а канареечная когорта —
    только из проспективных прогонов ПОСЛЕ неё, и они все здоровы. Это и есть
    случай, ради которого HUMAN_REVIEW существует.
    """
    sid, base, cand = await _skill(env)
    await _runs(env, base, completed=12, failed=8)                  # 0.60
    # История кандидата ДО отсечки: канареечной властью она не является, но в
    # долю успеха входит и держит кандидата в полосе шума.
    await _runs(env, cand, completed=6, failed=10)

    row = await ev.open_evaluation(env.svc, skill_id=sid, baseline_version_id=base,
                                   candidate_version_id=cand)
    # Проспективная когорта: чистая целиком.
    await _runs(env, cand, completed=ev.CANARY_WINDOW, failed=0)

    result = await ev.refresh(env.svc, int(row["id"]))
    assert result["verdict"] == ev.HUMAN_REVIEW, result["reason"]
    assert result["applied"] is False
    assert await _current(env, sid) == base

    # Дверь при вердикте по цифрам ещё не спрашивалась — её спрашивают в момент
    # решения человека. Поэтому чистоту когорты проверяем у самой двери.
    cand_row = await ev._version_row(env.svc, cand)
    _, _, door = await ev.canary_decision(env.svc, dict(row), cand_row)
    assert door.allowed, door.reason

    decided = await ev.apply_human_decision(env.svc, int(row["id"]),
                                            approve=True, by="владелец")
    assert decided["verdict"] == ev.PROMOTE and decided["applied"] is True
    assert await _current(env, sid) == cand


async def test_the_writer_and_the_decision_build_the_same_canary_run(env):
    """Один план на двоих — и до перезапуска, и после.

    Расхождение здесь было настоящей причиной отказов на здоровых кандидатах:
    писатель улики строил план из ВСЕХ прогонов версии без отсечки, решение — из
    прогонов после отсечки с обрезкой до окна. Разные списки членов дают разный
    `cohort_digest`, а `run_id` выводится из него, поэтому улика уезжала в
    прогон, которого решение не читает. Этот тест закрепляет тождество.
    """
    sid, base, cand, _, row = await _promotable(env)
    cand_row = await ev._version_row(env.svc, cand)

    writer_plan = ev._cohort_plan(ev._gate(env.svc), dict(row), cand_row)
    _, decision_run_id, decision = await ev.canary_decision(env.svc, dict(row), cand_row)

    assert decision_run_id == writer_plan.run_id
    assert decision.allowed, decision.reason

    # ПЕРЕЗАПУСК: новая ручка хранилища и новый объект двери обязаны найти ТОТ ЖЕ
    # прогон, иначе долговечная улика перестаёт быть долговечной.
    fresh_plan = ev._cohort_plan(ev._gate(env.svc), dict(row), cand_row)
    assert fresh_plan.run_id == writer_plan.run_id
    assert fresh_plan.cohort_digest == writer_plan.cohort_digest
    assert fresh_plan.cohort == writer_plan.cohort

    stored = ObjectiveStore(ev._canary_store_path(env.svc)).canary_run(writer_plan.run_id)
    assert stored["cohort_digest"] == writer_plan.cohort_digest
    assert set(stored["cohort"]) == set(writer_plan.cohort)


async def test_a_member_that_finished_early_keeps_its_evidence(env):
    """Член, завершившийся ДО того, как когорта добралась, улику не теряет.

    Места когорты известны с момента заведения сравнения, поэтому прогон,
    ставший терминальным третьим, пишет улику сразу и в ТОТ ЖЕ прогон, который
    потом прочитает решение. Если бы состав определялся только после появления
    пятого участника, улику первых пришлось бы либо терять, либо дописывать
    задним числом — а дописывать задним числом здесь нельзя.
    """
    sid, base, cand = await _skill(env)
    await _runs(env, base, completed=5, failed=5)
    row = await ev.open_evaluation(env.svc, skill_id=sid, baseline_version_id=base,
                                   candidate_version_id=cand)

    early = await _runs(env, cand, completed=2, failed=0)           # когорта ещё неполна
    cand_row = await ev._version_row(env.svc, cand)
    plan = ev._cohort_plan(ev._gate(env.svc), dict(row), cand_row)
    store = ObjectiveStore(ev._canary_store_path(env.svc))
    assert len(store.canary_reports(plan.run_id)) == len(early)     # улика уже лежит

    await _runs(env, cand, completed=8, failed=0)                   # когорта добралась
    result = await ev.refresh(env.svc, int(row["id"]))
    assert result["verdict"] == ev.PROMOTE and result["applied"] is True
    assert await _current(env, sid) == cand
