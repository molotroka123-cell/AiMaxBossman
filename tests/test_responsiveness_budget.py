"""Бюджет отзывчивости обязан быть неподвижным, а не подвижным.

Смысл §5 задания в одной фразе: «зафиксировать бюджет ДО замера; не поднять
его после неудачи». Файл `tools/responsiveness_budget.json` можно отредактировать
в любой момент — поэтому числа продублированы ЗДЕСЬ. Поднять предел теперь
значит изменить два файла в одном коммите и объяснить зачем; тихо подвинуть
границу после красного прогона нельзя.

Второе, что здесь проверяется: замер без улик не имеет права стать `PASS`.
"""
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BUDGET = REPO / "tools" / "responsiveness_budget.json"

# Зафиксировано 18.09.2026 до первого запуска пробы. Если строка ниже
# расходится с JSON — расходится намеренно и с причиной в коммите.
PINNED = {
    "cold_start_to_interactive_s": 8.0,
    "warm_start_to_interactive_s": 4.0,
    "navigation_p50_ms": 150.0,
    "navigation_p95_ms": 400.0,
    "gallery_100_first_paint_ms": 1000.0,
    "gallery_1000_first_paint_ms": 3000.0,
    "open_close_cycles_rss_growth_pct": 10.0,
    "soak_rss_growth_pct": 15.0,
    "soak_idle_cpu_pct_of_one_core": 2.0,
}


@pytest.fixture(scope="module")
def budget():
    return json.loads(BUDGET.read_text(encoding="utf-8"))


def test_every_pinned_number_still_matches_the_budget_file(budget):
    actual = {k: float(v["limit"]) for k, v in budget["budgets"].items()}
    assert actual == PINNED, (
        "бюджет разошёлся с закреплёнными числами. Это допустимо только "
        "осознанной правкой обоих файлов — но не молча после красного прогона.")


def test_the_budget_says_it_was_fixed_before_any_measurement(budget):
    assert budget["fixed_before_measurement"] is True
    assert budget["fixed_at"] == "2026-09-18"


def test_every_line_says_what_it_means_and_where_it_is_valid(budget):
    """Предел без определения — не бюджет, а число."""
    for key, spec in budget["budgets"].items():
        assert spec["means"].strip(), key
        assert spec["where"].strip(), key


def test_the_budget_refuses_the_three_ways_of_cheating(budget):
    rules = " ".join(budget["refusal_rules"]).lower()
    assert "не поднимается после неудачного замера" in rules
    assert "insufficient_evidence" in rules
    assert "reference_only" in rules


def test_the_process_tree_rule_is_stated(budget):
    """Одно число по главному процессу не отвечает «летает ли»."""
    assert "все процессы" in budget["process_tree_rule"].lower()


def test_an_unmeasured_line_is_insufficient_evidence_not_pass():
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location(
        "responsiveness_probe", REPO / "tools" / "responsiveness_probe.py")
    probe = importlib.util.module_from_spec(spec)
    before = list(sys.path)
    try:
        spec.loader.exec_module(probe)
    finally:
        sys.path[:] = before

    line = probe.Line("navigation_p50_ms", {"limit": 150.0, "means": "…", "where": "…"})
    assert line.verdict == "INSUFFICIENT_EVIDENCE"
    assert line.measured is None


def test_a_measurement_over_budget_is_a_failure_not_a_new_budget():
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location(
        "responsiveness_probe", REPO / "tools" / "responsiveness_probe.py")
    probe = importlib.util.module_from_spec(spec)
    before = list(sys.path)
    try:
        spec.loader.exec_module(probe)
    finally:
        sys.path[:] = before

    line = probe.Line("navigation_p95_ms", {"limit": 400.0, "means": "…", "where": "…"})
    line.observe(900.0, reference=False)
    assert line.verdict == "FAIL"
    assert line.limit == 400.0, "предел не имеет права подвинуться под замер"
    assert "ВЫШЕ БЮДЖЕТА" in line.detail

    inside = probe.Line("navigation_p95_ms", {"limit": 400.0, "means": "…", "where": "…"})
    inside.observe(120.0, reference=False)
    assert inside.verdict == "PASS", "пара обязана быть различающей"


def test_a_reference_run_never_claims_pass():
    """Linux из исходников — не доказательство об установленном архиве Windows."""
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location(
        "responsiveness_probe", REPO / "tools" / "responsiveness_probe.py")
    probe = importlib.util.module_from_spec(spec)
    before = list(sys.path)
    try:
        spec.loader.exec_module(probe)
    finally:
        sys.path[:] = before

    line = probe.Line("navigation_p50_ms", {"limit": 150.0, "means": "…", "where": "…"})
    line.observe(10.0, reference=True)
    assert line.verdict == "REFERENCE_ONLY"


def test_a_budget_not_marked_as_fixed_is_refused(tmp_path):
    """Негативный контроль к самой проверке фиксации."""
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location(
        "responsiveness_probe", REPO / "tools" / "responsiveness_probe.py")
    probe = importlib.util.module_from_spec(spec)
    before = list(sys.path)
    try:
        spec.loader.exec_module(probe)
    finally:
        sys.path[:] = before

    loose = json.loads(BUDGET.read_text(encoding="utf-8"))
    loose["fixed_before_measurement"] = False
    path = tmp_path / "loose.json"
    path.write_text(json.dumps(loose, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SystemExit):
        probe.load_budget(path)

    assert probe.load_budget(BUDGET)["fixed_before_measurement"] is True
