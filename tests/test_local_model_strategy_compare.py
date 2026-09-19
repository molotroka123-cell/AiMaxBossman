"""Сравнение стратегий обязано отказываться сравнивать несравнимое.

Раздел 26 запрещает зашивать ответ «одна большая» или «команда маленьких» и
требует измерить его на машине владельца. Опасность здесь не в арифметике, а
в том, что любые два прогона можно поставить рядом и получить таблицу — даже
когда они сняты на разных задачах, разных коммитах или разной обвязке.
Поэтому проверяется в первую очередь то, ЧТО ИНСТРУМЕНТ ОТКАЗЫВАЕТСЯ делать.
"""
from __future__ import annotations

import json

import pytest

from tools.intelligence_preservation_gate import CORE_METRICS, MODES, REQUIRED_METRICS
from tools.local_model_strategy_compare import (
    Incomparable,
    build_report,
    compare_metric,
    main,
    require_comparable,
)

SHA = "e" * 40


def run(model: str, *, score: float = 0.80, samples: int = 400, dataset: str = "suite-v1",
        sha: str = SHA, executes_tools: bool = True, hallucination: float = 0.05,
        overrides: dict | None = None, **extra):
    modes = {}
    for lane in MODES:
        block = {}
        for metric in REQUIRED_METRICS:
            value = hallucination if metric == "hallucination_rate" else score
            block[metric] = {"score": value, "samples": samples}
            if lane != "raw":
                block[metric]["paired"] = {"lost": 0, "gained": 0}
        modes[lane] = block
    for metric, value in (overrides or {}).items():
        modes["full"][metric]["score"] = value
    lanes = {lane: {"lane": lane, "kind": "prompt_ablation", "executes_tools": False}
             for lane in MODES if lane != "full"}
    lanes["full"] = {"lane": "full", "kind": "production_execution_loop",
                     "executes_tools": executes_tools,
                     "observed": {"executed": samples}}
    return {"model": model, "dataset_id": dataset, "evaluated_sha": sha,
            "lanes": lanes, "modes": modes, **extra}


# ------------------------------------------------------------------ отказы

def test_two_runs_on_different_task_sets_are_not_a_comparison():
    with pytest.raises(Incomparable, match="наборы задач различаются"):
        require_comparable({"A": run("big", dataset="suite-v1"),
                            "B": run("small-team", dataset="suite-v2")})


def test_two_runs_on_different_commits_are_not_a_comparison():
    """Разный SHA — разная обвязка, и разница в числах ей и принадлежит."""
    with pytest.raises(Incomparable, match="РАЗНЫХ коммитах"):
        require_comparable({"A": run("big", sha="a" * 40),
                            "B": run("small-team", sha="b" * 40)})


def test_a_prompt_ablation_cannot_represent_a_strategy():
    """AF-04 держится и здесь: стратегию оценивают по работающей обвязке."""
    with pytest.raises(Incomparable, match="не исполняла инструменты"):
        require_comparable({"A": run("big"),
                            "B": run("small-team", executes_tools=False)})


def test_the_same_model_twice_is_not_two_strategies():
    with pytest.raises(Incomparable, match="одна и та же модель"):
        require_comparable({"A": run("big"), "B": run("big")})


def test_different_sample_counts_cannot_be_put_side_by_side():
    with pytest.raises(Incomparable, match="разное число предметов"):
        require_comparable({"A": run("big", samples=400),
                            "B": run("small-team", samples=200)})


def test_one_run_is_not_a_comparison():
    with pytest.raises(Incomparable, match="не меньше двух"):
        require_comparable({"A": run("big")})


def test_comparable_runs_are_accepted():
    """Обратный контроль ко всем отказам выше.

    Без него проверки можно было бы ужесточать до тех пор, пока не отвергается
    вообще всё, и набор остался бы зелёным.
    """
    identity = require_comparable({"A": run("big"), "B": run("small-team")})
    assert identity["dataset_id"] == "suite-v1" and identity["evaluated_sha"] == SHA


# ------------------------------------------------------- различимость, а не разница

def test_a_visible_difference_on_few_items_is_called_indistinguishable():
    """Главное свойство инструмента.

    0.85 против 0.75 на двадцати предметах — это разница, которую видно
    глазом, и шум, который нельзя защитить. Человек скажет «A лучше»;
    интервал говорит «не хватает предметов».
    """
    result = compare_metric(
        {"A": run("big", samples=20, overrides={"reasoning_accuracy": 0.85}),
         "B": run("small-team", samples=20, overrides={"reasoning_accuracy": 0.75})},
        "reasoning_accuracy")
    assert result["verdict"] == "INDISTINGUISHABLE"
    assert result["leader"] is None
    assert "предметах эти числа неразличимы" in result["note"]


def test_the_same_difference_on_enough_items_is_called_distinguishable():
    """Обратный контроль: отвергается НЕДОСТАТОК ПРЕДМЕТОВ, а не сама разница."""
    result = compare_metric(
        {"A": run("big", samples=2000, overrides={"reasoning_accuracy": 0.85}),
         "B": run("small-team", samples=2000, overrides={"reasoning_accuracy": 0.75})},
        "reasoning_accuracy")
    assert result["verdict"] == "DISTINGUISHABLE"
    assert result["leader"] == "A"


def test_a_lower_is_better_metric_is_not_read_upside_down():
    """У доли выдумок «лучше» означает «меньше» — в одной таблице с остальными."""
    result = compare_metric(
        {"A": run("big", samples=2000, hallucination=0.02),
         "B": run("small-team", samples=2000, hallucination=0.20)},
        "hallucination_rate")
    assert result["lower_is_better"] is True
    assert result["leader"] == "A", result


# ------------------------------------------------------------------ вердикт

def test_a_split_result_is_not_resolved_into_a_winner():
    """Когда стратегии ведут по разным метрикам, победителя не назначают.

    Выбрать одну метрику задним числом — самый простой способ получить
    «нужный» архитектурный вывод.
    """
    report = build_report({
        "A": run("big", samples=2000, overrides={"reasoning_accuracy": 0.95}),
        "B": run("small-team", samples=2000, overrides={"tool_selection_accuracy": 0.98}),
    })
    assert report["verdict"] == "SPLIT"
    assert "Общего победителя нет" in report["why"]


def test_no_measurable_difference_is_itself_the_answer():
    report = build_report({"A": run("big", samples=400),
                           "B": run("small-team", samples=400)})
    assert report["verdict"] == "INDISTINGUISHABLE"
    assert "по стоимости и памяти" in report["why"]


def test_a_consistent_leader_is_named():
    report = build_report({
        "A": run("big", samples=2000, score=0.90),
        "B": run("small-team", samples=2000, score=0.60),
    })
    assert report["verdict"] == "LEADER:A", report["why"]


# ------------------------------------------------- скорость и память не выдумываются

def test_unmeasured_throughput_is_named_not_zeroed():
    report = build_report({"A": run("big"), "B": run("small-team")})
    table = report["throughput_and_cost"]
    assert table["decode_tokens_per_second"]["values"]["A"] == "не измерено"
    assert table["peak_resident_memory_mib"]["values"]["B"] == "не измерено"


def test_measured_throughput_is_carried_through():
    report = build_report({
        "A": run("big", decode_tokens_per_second=31.4, peak_resident_memory_mib=62_000),
        "B": run("small-team", decode_tokens_per_second=88.2, peak_resident_memory_mib=19_500),
    })
    table = report["throughput_and_cost"]
    assert table["decode_tokens_per_second"]["values"] == {"A": 31.4, "B": 88.2}
    assert table["peak_resident_memory_mib"]["values"]["B"] == 19_500


def test_hardware_block_is_also_read():
    report = build_report({
        "A": run("big", hardware={"unified_memory_mib": 131_072}),
        "B": run("small-team"),
    })
    values = report["throughput_and_cost"]["unified_memory_mib"]["values"]
    assert values["A"] == 131_072 and values["B"] == "не измерено"


# ------------------------------------------------------------------ командная строка

def test_cli_refuses_and_says_why(tmp_path, capsys):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(run("big", dataset="one")), encoding="utf-8")
    b.write_text(json.dumps(run("small-team", dataset="two")), encoding="utf-8")
    assert main([f"A={a}", f"B={b}", "--out", str(tmp_path / "r.json")]) == 2
    out = capsys.readouterr().out
    assert "STRATEGY_COMPARISON=INCOMPARABLE" in out
    written = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert written["verdict"] == "INCOMPARABLE" and "наборы задач" in written["reason"]


def test_cli_writes_a_report_it_can_defend(tmp_path, capsys):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(run("big", samples=2000, score=0.90)), encoding="utf-8")
    b.write_text(json.dumps(run("small-team", samples=2000, score=0.60)), encoding="utf-8")
    assert main([f"A={a}", f"B={b}", "--out", str(tmp_path / "r.json")]) == 0
    written = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert written["verdict"] == "LEADER:A"
    # Каждая различимая метрика несёт числа, по которым вывод перепроверяется.
    for row in written["quality"]:
        assert row["metric"] in CORE_METRICS
        for side in row["runs"].values():
            assert side["samples"] == 2000 and len(side["ci95"]) == 2
    assert "только для этой машины" in written["caveat"]


def test_a_duplicate_strategy_name_is_refused(tmp_path):
    a = tmp_path / "a.json"
    a.write_text(json.dumps(run("big")), encoding="utf-8")
    assert main([f"A={a}", f"A={a}"]) == 2
