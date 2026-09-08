"""Измеренное продвижение цели против НАСТОЯЩЕГО одноразового журнала.

`tests/test_v5_promotion.py` живёт в корневом наборе, который обязан собираться
без ядра (AT-05), поэтому там журнал — подделка, соблюдающая порт. Здесь берётся
канонический `DurableEvidenceLedger` (AUDIT001-F5-REPLAY): если порт разошёлся с
ним хоть в чём-то, это видно тут, а не в проде.

Главное, что проверяется: отказ ПЕРЕЖИВАЕТ РЕСТАРТ. Одноразовость, живущая в
памяти процесса, не одноразовость — перезапуск сбрасывал бы её.
"""
from __future__ import annotations

import pytest

from bossman.learning_guard.evidence_ledger import DurableEvidenceLedger
from bossman_shared.objective_improvement import REQUIRED_STAGES, CandidateImprovement
from bossman_shared.objective_promotion import (LedgerPort, MeasuredPromotion, Outcome, Task,
                                                authorize, measure)

SCOPE = ("python.refactor",)
REF = "intelligence_preservation/paired/" + "b" * 64


def _tasks(n=40):
    return [Task(f"t-{i:03d}", "python.refactor") for i in range(n)]


def _run(variant, task):
    passed = variant == "candidate" or int(task.task_id.split("-")[1]) % 2 == 0
    return Outcome(task.task_id, passed, 1.0 if passed else 0.0)


def _candidate(version="v2"):
    return CandidateImprovement(kind="skill", current_version="v1", candidate_version=version,
                                hypothesis="measured refactor template",
                                completed_stages=REQUIRED_STAGES)


def _measure(version="v2"):
    return measure(_tasks(), candidate_id="skill.refactor", candidate_version=version,
                   baseline_version="v1", applicability_version="app-3", run=_run)


def _authorize(measurement, ledger, candidate):
    return authorize(measurement, candidate, ledger=ledger, applicability_version="app-3",
                     applicability_scope=SCOPE, retention=0.995, retention_evidence_ref=REF,
                     security_pass=True, rollback_available=True)


def test_the_canonical_durable_ledger_satisfies_the_promotion_port(tmp_path):
    assert isinstance(DurableEvidenceLedger(tmp_path / "ledger.json"), LedgerPort)


def test_a_measured_promotion_spends_the_durable_evidence_once(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = DurableEvidenceLedger(path)
    first = _measure()
    verdict = _authorize(first, ledger, _candidate())
    assert verdict.authorized, verdict.reason

    # Повтор тем же кандидатом — не реплей: продвижение можно перезапустить.
    assert _authorize(first, ledger, _candidate()).authorized


def test_the_refusal_survives_a_restart(tmp_path):
    """Одноразовость, не пережившая рестарт, — не одноразовость."""
    path = tmp_path / "ledger.json"
    first = _measure()
    assert _authorize(first, DurableEvidenceLedger(path), _candidate()).authorized

    # Новый процесс, новый объект журнала, те же байты измерения, другая версия.
    replay = MeasuredPromotion(
        candidate_id=first.candidate_id, candidate_version="v9",
        baseline_version=first.baseline_version, applicability=first.applicability,
        applicability_version=first.applicability_version, split=first.split,
        measured=first.measured, holdout=first.holdout,
        task_set_digest=first.task_set_digest)
    assert replay.evidence_key == first.evidence_key
    verdict = _authorize(replay, DurableEvidenceLedger(path), _candidate("v9"))
    assert not verdict.authorized and verdict.reason == "evidence_already_spent"


def test_an_unreadable_ledger_refuses_instead_of_promoting(tmp_path):
    """Неопределённость журнала fail-closed: непрочитанный журнал не разрешение."""
    path = tmp_path / "ledger.json"
    path.write_text("{ this is not json", encoding="utf-8")
    verdict = _authorize(_measure(), DurableEvidenceLedger(path), _candidate())
    assert not verdict.authorized and verdict.reason == "evidence_already_spent"


def test_a_second_candidate_needs_its_own_measurement_and_then_promotes(tmp_path):
    """Отрицательный контроль к одноразовости: журнал запрещает ПОВТОР улики, а
    не второе продвижение.

    Ключ описывает ИЗМЕРЕНИЕ, а не кандидата, поэтому вторая версия с теми же
    числами — это реплей и отказ. Со СВОИМ измерением ключ другой, и она
    проходит. Ровно так одноразовость и должна разделять эти два случая.
    """
    path = tmp_path / "ledger.json"
    first = _measure("v2")
    assert _authorize(first, DurableEvidenceLedger(path), _candidate("v2")).authorized

    same_numbers = _measure("v3")
    assert same_numbers.evidence_key == first.evidence_key       # то же измерение
    replayed = _authorize(same_numbers, DurableEvidenceLedger(path), _candidate("v3"))
    assert not replayed.authorized and replayed.reason == "evidence_already_spent"

    own = measure(_tasks(44), candidate_id="skill.refactor", candidate_version="v3",
                  baseline_version="v1", applicability_version="app-3", run=_run)
    assert own.evidence_key != first.evidence_key                # другое измерение
    assert _authorize(own, DurableEvidenceLedger(path), _candidate("v3")).authorized
