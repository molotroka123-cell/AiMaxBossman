"""Organization Layer ← bossman.company: план AI Company Mode становится
контрактами делегирования без второго планировщика.

Проверяется перенос семантики: DAG → dependencies, роль → отдел из плана,
action → required_capability, evidence_requirements → evidence_required,
гейтуемые виды (publish/spend/…) → HIGH-риск, read → информационная работа.
"""
from __future__ import annotations

from bossman.company import synthetic_seo as seo
from bossman_v3.organization import RiskTier, contracts_from_company_plan
from bossman_v3.organization.runtime import _topological


def test_company_plan_maps_to_contracts_preserving_dag_and_risk():
    plan = seo.build_plan()
    contracts = contracts_from_company_plan(plan, mission_id="seo-1")
    by_id = {c.work_id: c for c in contracts}

    assert set(by_id) == {t.id for t in plan.tasks}
    assert by_id["seo-publish"].risk == RiskTier.HIGH and by_id["seo-publish"].metadata["gated"] == ["publish"]
    assert by_id["seo-fix-titles"].risk == RiskTier.MEDIUM and by_id["seo-fix-titles"].side_effect
    assert by_id["seo-audit"].side_effect is False and by_id["seo-audit"].problems() == []
    assert by_id["seo-publish"].dependencies == ["seo-rescore"]
    assert by_id["seo-fix-alt"].department_id == "engineering"
    assert by_id["seo-publish"].department_id == "compliance"
    assert by_id["seo-fix-meta"].required_capability == "seo.fix_meta"
    assert [e.kind for e in by_id["seo-rescore"].evidence_required] == ["site"]
    order = [c.work_id for c in _topological(contracts)]
    assert order.index("seo-publish") > order.index("seo-rescore") > order.index("seo-fix-alt")
