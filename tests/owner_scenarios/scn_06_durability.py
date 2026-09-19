"""Владельческие сценарии 18–20: смерть процесса, переигрывание, отчёт владельцу.

Это те три вопроса, на которые владелец не может ответить «посмотри в логи»:
переживает ли работа убийство процесса, не удваивает ли повтор внешний эффект,
и получает ли владелец ОДИН понятный отчёт, а не россыпь строк.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import spine_fixtures as fx  # noqa: E402
from bossman_shared import evidence as _evidence  # noqa: E402
from bossman_shared.objective_admission import (reauthorize_at_effect_boundary)  # noqa: E402
from bossman_shared.objective_reconcile import (bind_evidence,  # noqa: E402
                                                observation_digests,
                                                reconcile_after_mission)
from bossman_shared.objective_recovery import (APPLIED, COMMITTED,  # noqa: E402
                                               IDEMPOTENT, IRREVERSIBLE, NOT_APPLIED,
                                               PARKED, RELEASED, UNKNOWN, recover,
                                               resume_point)
from bossman_shared.objective_store import (DuplicateProposal,  # noqa: E402
                                            DuplicateReservation, ObjectiveStore,
                                            ObjectiveStoreError)
from scenario_runner import INSTALLED_PRODUCT, PRODUCT_CONTRACTS, scenario  # noqa: E402


def _verified_cycle(store, spec, target: Path, *, keys: Path, now: float, tag: str):
    """Полный проход задачи: план → эффект → проверенное наблюдение."""
    proposal, decision, mission = fx.admitted_mission(
        store, spec, target=target, now=now,
        observation_id=f"obs-{tag}-trigger", observed_at=now - 10.0)
    target.write_text(f"зелёная сборка {tag}\n", encoding="utf-8")
    store.settle_reservation(decision.reservation_id, "COMMITTED")
    batch = [fx.observation(spec, green=True, observed_at=now + 2.0,
                            observation_id=f"obs-{tag}-verified")]
    mission_id = mission.to_dict()["mission_id"]
    with fx.evidence_key(keys):
        # SATISFIED принимает только РАЗРЕШИМУЮ подписанную улику продукта:
        # ссылка вида `oev1:<32 hex>`, отчеканенная самим хранилищем из текущих
        # привязок цели. Произвольная строка вроде `file://...` отвергается
        # (`malformed_reference`) — и это правильно: зелёное не должно ставиться
        # прозой.
        # `now` не передаётся намеренно: чеканщик ставит СВОИ часы, и ровно по
        # ним `set_condition` потом меряет свежесть. Улика, отчеканенная на
        # секунду вперёд, отвергается как `stale` — «ещё не наступила».
        ref = store.record_condition_evidence(
            fx.OBJECTIVE_A, condition="SATISFIED", run_id=decision.reservation_id,
            observation_digests=observation_digests(batch))
        payload = bind_evidence(objective_id=fx.OBJECTIVE_A, objective_digest=spec.digest,
                                objective_revision=1, mission_id=mission_id,
                                reservation_id=decision.reservation_id,
                                evidence_ref=ref,
                                effect_at=now + 1.0, digests=observation_digests(batch))
        signed = {**payload, **_evidence.sign_fields(payload, signer="bossman_v3.verifier")}
        result = reconcile_after_mission(
            store, fx.OBJECTIVE_A, mission_verified=True, evidence=signed,
            reobserve=lambda: list(batch), now=now + 3.0,
            expected_version=store.get(fx.OBJECTIVE_A).version,
            mission_id=mission_id, reservation_id=decision.reservation_id)
    return result, proposal, decision


@scenario(id="OS-18", depth=PRODUCT_CONTRACTS)
def os18_killed_mid_task_and_resumes(ctx) -> None:
    """Задача в полёте → процесс убит → перезапуск → явное разрешение → продолжение."""
    path = ctx.path("state", "objectives.sqlite3")
    target = ctx.path("работа", "результат.txt")
    now = fx.real_now()
    store, spec, _ = fx.ready_store(path, spec=fx.make_spec(expires_at=now + 100_000.0))

    verified, stale_proposal, stale_decision = _verified_cycle(
        store, spec, target, keys=ctx.path("keys"), now=now, tag="before")
    ctx.positive("до убийства задача имеет проверенное состояние",
                 verified.condition == "SATISFIED" and bool(verified.evidence_refs),
                 f"condition={verified.condition}")
    evidence_ref = verified.evidence_refs[0]
    usage_before = store.get(fx.OBJECTIVE_A).observations_used

    # Работа В ПОЛЁТЕ в момент смерти процесса: бронь осталась открытой.
    inflight = fx.propose(store, spec, target=target, now=now + 60.0,
                          observation_id="obs-inflight", observed_at=now + 55.0)
    inflight_decision = fx.kernel().admit(store, inflight, now=now + 60.0)
    ctx.positive("в момент смерти процесса работа действительно была в полёте",
                 inflight_decision.admitted
                 and len(store.open_reservations(fx.OBJECTIVE_A)) == 1,
                 f"броней={len(store.open_reservations(fx.OBJECTIVE_A))}")

    del store  # УБИЙСТВО процесса: всё, что жило в памяти, исчезло

    later = now + 200.0
    restarted = ObjectiveStore(path)
    report = recover(restarted, fx.OBJECTIVE_A, now=later,
                     is_effect_applied=lambda reservation: NOT_APPLIED)
    point = resume_point(restarted, fx.OBJECTIVE_A)
    ctx.positive("после перезапуска продолжается ТА ЖЕ задача",
                 point.objective_id == fx.OBJECTIVE_A and point.lifecycle == "ACTIVE"
                 and restarted.get_spec(fx.OBJECTIVE_A).digest == spec.digest)
    ctx.positive("каждая бронь в полёте разрешена ЯВНО, а не забыта",
                 len(report.outcomes) == 1
                 and report.outcomes[0].disposition in (RELEASED, COMMITTED, PARKED),
                 f"исход={report.outcomes[0].disposition} причина={report.outcomes[0].reason}")
    ctx.positive("накопленный расход пережил убийство процесса",
                 point.observations_used >= usage_before and usage_before > 0,
                 f"наблюдений до={usage_before}, после={point.observations_used}")
    ctx.positive("продолжение опирается на последнюю ПРОВЕРЕННУЮ улику",
                 point.last_verified_evidence_ref == evidence_ref)

    ctx.negative("убийство процесса не сохраняет «зелёный» статус само по себе",
                 point.condition == "UNKNOWN" and point.has_verified_state is False,
                 f"condition после перезапуска={point.condition}")
    ctx.negative("план, принятый до смерти, не даёт права действовать после неё",
                 reauthorize_at_effect_boundary(restarted, stale_decision, stale_proposal,
                                                now=later, policy=fx.FakePolicy()) is False)
    ctx.negative("двусмысленность после краха НЕ даёт права переиграть эффект",
                 report.outcomes[0].disposition == PARKED and report.blocked
                 and report.outcomes[0].requires_owner,
                 f"исход={report.outcomes[0].disposition}/{report.outcomes[0].reason}")
    ctx.refused("чистое хранилище не выдумывает продолжение задачи",
                lambda: ObjectiveStore(ctx.path("другое", "objectives.sqlite3")).get(fx.OBJECTIVE_A),
                ObjectiveStoreError)

    # ЧЕСТНЫЙ ИТОГ. Автоматического возобновления задачи, убитой С РАБОТОЙ В
    # ПОЛЁТЕ, на этой ветке НЕТ: `AdmissionKernel.admit` не записывает
    # `effect_class` в полезную нагрузку брони, а `objective_recovery._effect_class`
    # трактует отсутствующий класс как IRREVERSIBLE. Поэтому паркуется ЛЮБАЯ
    # бронь — даже идемпотентная запись файла, — и следующий допуск по этой цели
    # отбивается (`admission_state_changed`), пока владелец не разберёт парковку.
    followup = fx.propose(restarted, spec, target=target, now=later + 5.0,
                          observation_id="obs-after-trigger", observed_at=later - 5.0)
    blocked = fx.kernel().admit(restarted, followup, now=later + 5.0)
    ctx.owner_required(
        "после убийства с работой в полёте задача НЕ возобновляется сама: бронь "
        f"припаркована ({report.outcomes[0].reason}), следующий допуск отбит "
        f"({blocked.reason}). Причина в продукте: AdmissionKernel.admit не пишет "
        "effect_class в бронь, а recovery считает неизвестный класс необратимым. "
        "Возобновление требует решения владельца.")


@scenario(id="OS-19", depth=PRODUCT_CONTRACTS)
def os19_replay_does_not_duplicate_the_effect(ctx) -> None:
    """Переигрывание того же намерения не имеет права дать второй внешний эффект."""
    path = ctx.path("state", "objectives.sqlite3")
    target = ctx.path("работа", "результат.txt")
    now = fx.real_now()
    store, spec, _ = fx.ready_store(path, spec=fx.make_spec(expires_at=now + 100_000.0))
    proposal = fx.propose(store, spec, target=target, now=now, observed_at=now - 10.0)
    decision = fx.kernel().admit(store, proposal, now=now)

    applications: list[str] = []

    def apply_once(decision_) -> None:
        """Внешний эффект под фенсом брони: выполняется только под живой бронью."""
        open_ids = {r["reservation_id"] for r in store.open_reservations(fx.OBJECTIVE_A)}
        if decision_.reservation_id in open_ids:
            applications.append(decision_.reservation_id)
            target.write_text(f"эффект {len(applications)}\n", encoding="utf-8")
            store.settle_reservation(decision_.reservation_id, "COMMITTED")

    apply_once(decision)
    ctx.positive("первое применение дало ровно один внешний эффект",
                 len(applications) == 1 and target.read_text(encoding="utf-8").strip() == "эффект 1",
                 f"применений={len(applications)}")

    apply_once(decision)  # переигрывание того же решения после закрытия брони
    ctx.negative("повтор того же решения НЕ дал второго эффекта",
                 len(applications) == 1,
                 f"применений после повтора={len(applications)}")

    ctx.refused("то же предложение нельзя внести повторно",
                lambda: store.insert_proposal_once(
                    proposal_id=proposal.proposal_id, objective_id=fx.OBJECTIVE_A,
                    objective_digest=spec.digest, objective_revision=1,
                    created_at=now, valid_until=now + 100.0, payload={}),
                (DuplicateProposal, ObjectiveStoreError))
    ctx.refused("ту же бронь нельзя взять дважды",
                lambda: store.reserve_once(
                    reservation_id=decision.reservation_id, objective_id=fx.OBJECTIVE_A,
                    proposal_id=proposal.proposal_id, created_at=now, payload={}),
                (DuplicateReservation, ObjectiveStoreError))
    ctx.refused("закрытую бронь нельзя закрыть ещё раз",
                lambda: store.settle_reservation(decision.reservation_id, "COMMITTED"),
                ObjectiveStoreError)

    # Переигрывание после перезапуска: восстановление тоже не дублирует эффект.
    del store
    restarted = ObjectiveStore(path)
    report = recover(restarted, fx.OBJECTIVE_A, now=now + 10.0,
                     is_effect_applied=lambda r: APPLIED)
    ctx.negative("восстановление после перезапуска не переотправляет эффект",
                 not report.outcomes and len(applications) == 1,
                 f"исходов восстановления={len(report.outcomes)}")
    _ = IDEMPOTENT, IRREVERSIBLE, UNKNOWN


@scenario(id="OS-20", depth=INSTALLED_PRODUCT)
def os20_owner_gets_one_completion_report(ctx) -> None:
    """Один сводный человекочитаемый отчёт о завершении, а не россыпь строк."""
    from bossman.completion import CompletionContract, CompletionGate, FileObligation  # noqa: PLC0415
    from bossman.toolkit import ToolContext  # noqa: PLC0415
    from bossman.toolkit.files import fs_write  # noqa: PLC0415
    import asyncio  # noqa: PLC0415

    workdir = ctx.path("агент", "workdir")
    workdir.mkdir(parents=True, exist_ok=True)
    ctx.reached_installed_product("bossman.completion.CompletionGate установленного bossman-core")
    tool_ctx = ToolContext(agent="bossman-coder", workdir=workdir)
    args = {"path": "отчёт.txt", "content": "работа завершена\n"}
    result = asyncio.run(fs_write(args, tool_ctx))

    gate = CompletionGate(
        CompletionContract(mode="action",
                           files=[FileObligation(path="отчёт.txt", contains="завершена")]),
        workdir)
    gate.record("fs.write", "write", args, error=result.error)
    status, text = gate.finish()
    ctx.positive("продукт выдал ОДИН сводный вердикт о завершении",
                 isinstance(status, str) and isinstance(text, str) and bool(text.strip()),
                 f"status={status}")
    ctx.positive("отчёт читается человеком, а не только машиной",
                 len(text.strip()) >= 10 and any(ch.isalpha() for ch in text),
                 f"текст={text[:100]}")
    ctx.positive("отчёт опирается на независимо перечитанные байты",
                 bool(gate.writes) and not gate.unverified_effect,
                 f"записей={len(gate.writes)}")

    # Отрицательный контроль: невыполненное обязательство не даёт зелёного отчёта.
    broken = CompletionGate(
        CompletionContract(mode="action",
                           files=[FileObligation(path="нет-такого.txt", exists=True)]),
        workdir)
    broken_status, broken_text = broken.finish()
    ctx.negative("невыполненное обязательство не превращается в отчёт об успехе",
                 broken_status != status or "не" in broken_text.lower(),
                 f"status={broken_status} текст={broken_text[:80]}")

    lying = CompletionGate(CompletionContract(mode="action"), workdir)
    lying.record("shell.run", "exec", {"cmd": "rm -rf /"}, error=False)
    lying_status, lying_text = lying.finish()
    ctx.negative("непроверяемое действие помечается как непроверенное",
                 lying.unverified_effect is True,
                 f"status={lying_status} текст={lying_text[:80]}")
