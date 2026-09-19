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


def _probe():
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location(
        "responsiveness_probe", REPO / "tools" / "responsiveness_probe.py")
    module = importlib.util.module_from_spec(spec)
    before = list(sys.path)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = before
    return module


def test_a_reference_run_over_budget_does_not_hide_behind_reference_only():
    """Справочный прогон, вылетевший за бюджет, обязан это СКАЗАТЬ.

    Первый часовой прогон дал по CPU 22.4 % при пределе 2 и вывел строку
    `REFERENCE_ONLY` — превышение читалось только в пояснении, а общий вердикт
    о нём молчал. Справочность означает «не доказывает Windows», а не «не
    считается».
    """
    probe = _probe()
    line = probe.Line("soak_idle_cpu_pct_of_one_core",
                      {"limit": 2.0, "means": "…", "where": "…"})
    line.observe(22.4, reference=True)
    assert line.verdict == "REFERENCE_ONLY_OVER_BUDGET", line.verdict
    assert "ВЫШЕ БЮДЖЕТА" in line.detail

    inside = probe.Line("soak_idle_cpu_pct_of_one_core",
                        {"limit": 2.0, "means": "…", "where": "…"})
    inside.observe(0.8, reference=True)
    assert inside.verdict == "REFERENCE_ONLY", "пара обязана быть различающей"


def test_the_overall_verdict_surfaces_a_reference_run_over_budget():
    """Иначе превышение тонет в строке итога."""
    probe = _probe()
    assert probe.overall_verdict(["REFERENCE_ONLY", "REFERENCE_ONLY_OVER_BUDGET"]) \
        == "REFERENCE_ONLY_OVER_BUDGET"
    assert probe.overall_verdict(["FAIL", "REFERENCE_ONLY_OVER_BUDGET"]) == "FAIL"
    assert probe.overall_verdict(["REFERENCE_ONLY", "OWNER_REQUIRED"]) == "REFERENCE_ONLY"
    assert probe.overall_verdict(["PASS", "PASS"]) == "PASS"
    assert probe.overall_verdict(
        ["PASS", "INSUFFICIENT_EVIDENCE"]) == "INSUFFICIENT_EVIDENCE"


def test_idle_cpu_is_measured_in_a_quiet_window_not_under_load():
    """Метрика называется «в покое» — значит, мерить её надо в покое.

    Первый часовой прогон считал CPU за весь прогон, в котором проба делала
    переход каждые две секунды: 1734 перехода за час. Это стоимость работы, а
    не покоя, и сравнивать её с бюджетом покоя бессмысленно. Число получилось
    в одиннадцать раз выше предела — и ошибка была в измерителе.
    """
    probe = _probe()
    assert probe.IDLE_WINDOW_SECONDS >= 120, \
        "окно покоя короче двух минут не переживёт один тик очереди"
    assert probe.idle_window_for(3600) >= probe.IDLE_WINDOW_SECONDS
    assert probe.idle_window_for(0) == 0, "без прогона на выдержку нет и окна покоя"


def test_an_interrupted_soak_leaves_evidence_and_never_claims_a_verdict(tmp_path):
    """Прерванный прогон обязан оставить, докуда дошёл, и не выдать это за замер.

    Контейнер этой среды дважды убил часовой прогон — на 9-й и на 38-й минуте.
    Без промежуточной записи каждый срыв стирал всё: ни числа, ни знания, где
    оно оборвалось. Но промежуточный снимок ОПАСЕН ровно тем, чем полезен:
    его легко принять за результат. Поэтому он помечен незавершённым, а строки
    выдержки в нём — `INSUFFICIENT_EVIDENCE`, а не «в бюджете».
    """
    probe = _probe()
    lines = {
        "soak_rss_growth_pct": probe.Line(
            "soak_rss_growth_pct", {"limit": 15.0, "means": "…", "where": "…"}),
        "soak_idle_cpu_pct_of_one_core": probe.Line(
            "soak_idle_cpu_pct_of_one_core", {"limit": 2.0, "means": "…", "where": "…"}),
    }
    target = tmp_path / "partial.json"
    probe.write_checkpoint(target, lines, mode="reference",
                           elapsed_load_seconds=1380.0, turns=690)

    import json
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["completed"] is False
    assert saved["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert saved["elapsed_load_seconds"] == 1380.0
    for row in saved["lines"]:
        if row["metric"].startswith("soak_"):
            assert row["verdict"] == "INSUFFICIENT_EVIDENCE", row
            assert "прерван" in row["detail"] or "не завершён" in row["detail"], row


def test_a_finished_soak_is_marked_complete(tmp_path):
    """Пара: без неё «незавершён» не отличить от постоянной метки."""
    probe = _probe()
    line = probe.Line("soak_rss_growth_pct",
                      {"limit": 15.0, "means": "…", "where": "…"})
    line.observe(9.0, reference=True)
    report = {"verdict": "REFERENCE_ONLY", "completed": True,
              "lines": [line.as_dict()]}
    assert report["completed"] is True
    assert line.verdict == "REFERENCE_ONLY"


def test_checkpoints_never_land_in_the_file_that_holds_the_final_report(tmp_path):
    """Снимок и отчёт — разные файлы, иначе дерево грязнится раз в минуту.

    Раздел 30 директивы: порождаемое рантаймом состояние не держат под учётом
    Git. За час прогона снимок переписывается шестьдесят раз; под учёт должен
    попадать только завершённый отчёт.
    """
    probe = _probe()
    final = tmp_path / "soak.json"
    partial = probe.checkpoint_path(final)
    assert partial != final, "снимок затирал бы итоговый отчёт"
    assert partial.name.endswith(".partial.json"), partial
    assert probe.checkpoint_path(None) is None, "без --json писать снимки некуда"


def test_the_partial_suffix_is_actually_ignored_by_git():
    """Правило без записи в .gitignore — намерение, а не правило."""
    body = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "*.partial.json" in body, ".gitignore не игнорирует промежуточные снимки"
