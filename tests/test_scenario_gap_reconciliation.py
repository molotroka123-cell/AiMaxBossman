"""Сверка объявленных пробелов с измеренными — на всех исходах, включая блокер.

BL-103. Сверка (`tools/scenario_runner.gap_reconciliation`) решает судьбу гейта
табло: она красит прогон, когда табло разошлось с действительностью. Первая её
версия исключала СЦЕНАРИЙ С БЛОКЕРОМ только из одной из трёх проверок, и
корневой CI немедленно покраснел: там по правилу BL-085 нет `command_center`,
OS-29 уходил в `INSUFFICIENT_EVIDENCE` с причиной-БЛОКЕРОМ, и сверка объявляла
«причина отличается от объявленной» там, где причины не было вовсе.

Локально этого не видно: `command_center` есть, сценарий исполняется, причина
совпадает. Поэтому здесь исходы собираются ЯВНО, а не берутся из среды — тест
обязан проверять и ту среду, которой на этой машине нет.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import scenario_runner as sr  # noqa: E402


def _result(scenario_id: str, level: str, *, gap: str = "", reason: str = "",
            blocked: bool = False) -> sr.ScenarioResult:
    scenario = sr.Scenario(id=scenario_id, number=int(scenario_id.split("-")[1]),
                           title="строка", chain="цепочка", requires=(),
                           model_step="none", depth=sr.PRODUCT_CONTRACTS,
                           func=lambda ctx: None, owner_gap=gap)
    blockers = [{"capability": "command_center", "detail": "нет в среде"}] if blocked else []
    return sr.ScenarioResult(scenario=scenario, level=level, reason=reason, blockers=blockers)


def _reconcile(*results):
    return sr.gap_reconciliation(list(results))


def test_a_blocked_scenario_is_judged_by_nothing():
    """Способности нет — сценарий НЕ ИСПОЛНЯЛСЯ, и судить не о чем.

    Это и есть та регрессия, что покрасила корневой CI: причина у такого
    прогона — текст блокера, а не пробел продукта.
    """
    blocked_declared = _result("OS-29", sr.INSUFFICIENT_EVIDENCE, gap="скачанное из сети",
                               reason="command_center: нет в среде", blocked=True)
    blocked_plain = _result("OS-32", sr.INSUFFICIENT_EVIDENCE,
                            reason="command_center: нет в среде", blocked=True)
    blocked_green = _result("OS-40", sr.AI_BACKED_CI, gap="провайдер", blocked=True)
    out = _reconcile(blocked_declared, blocked_plain, blocked_green)
    assert out == {"gaps_undeclared": [], "gaps_closed": [], "gaps_mislabelled": []}, out


def test_an_undeclared_not_proven_row_is_reported():
    out = _reconcile(_result("OS-07", sr.INSUFFICIENT_EVIDENCE, reason="нечем доказать"))
    assert out["gaps_undeclared"] == ["OS-07"]
    assert out["gaps_closed"] == [] and out["gaps_mislabelled"] == []


def test_a_gap_that_closed_is_reported():
    """Объявление, которое перестало быть правдой, — вечная индульгенция."""
    out = _reconcile(_result("OS-22", sr.AI_BACKED_CI, gap="координатный клик"))
    assert out["gaps_closed"] == ["OS-22"]
    assert out["gaps_undeclared"] == [] and out["gaps_mislabelled"] == []


def test_a_gap_whose_reason_changed_is_reported():
    out = _reconcile(_result("OS-29", sr.INSUFFICIENT_EVIDENCE, gap="скачанное из сети",
                             reason="теперь упало совсем по другому поводу"))
    assert out["gaps_mislabelled"] == ["OS-29"]
    assert out["gaps_undeclared"] == [] and out["gaps_closed"] == []


def test_a_declared_gap_with_a_matching_reason_is_silent():
    out = _reconcile(_result("OS-29", sr.INSUFFICIENT_EVIDENCE, gap="скачанное из сети",
                             reason="… скачанное из сети и отданное оболочке …"))
    assert out == {"gaps_undeclared": [], "gaps_closed": [], "gaps_mislabelled": []}


def test_a_proven_row_without_a_gap_is_silent():
    out = _reconcile(_result("OS-01", sr.AI_BACKED_CI))
    assert out == {"gaps_undeclared": [], "gaps_closed": [], "gaps_mislabelled": []}


@pytest.mark.parametrize("level", [sr.OWNER_REQUIRED, sr.OWNER_HARDWARE_REQUIRED])
def test_an_unblocked_owner_required_row_is_not_called_undeclared(level):
    """`OWNER_REQUIRED` без блокера — не «не доказано», а «нужен владелец».

    Сюда попадает, например, студия изображений: провайдер в продукте — мок, и
    это пробел, который владелец закрывает ключом, а не прогон, который что-то
    не доказал.
    """
    out = _reconcile(_result("OS-16", level, reason="генерация не настоящая"))
    assert out == {"gaps_undeclared": [], "gaps_closed": [], "gaps_mislabelled": []}
