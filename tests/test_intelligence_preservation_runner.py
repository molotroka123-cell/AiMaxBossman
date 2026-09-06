"""Измеритель удержания интеллекта: он обязан быть честным ДО живого прогона.

Гейт `tools/intelligence_preservation_gate.py` существовал без измерителя, и
канонический `docs/benchmark/intelligence-preservation-current.json` не
производил никто — поэтому проверка Intelligence Preservation в CI падала не
из-за модели и не из-за кода, а из-за отсутствия файла.

Здесь модель подменяется: проверяется НАША арифметика — парность полос,
подсчёт lost/gained, инверсия метрики выдумок, привязка к коммиту и то, что
недостаточная выборка честно остаётся недостаточной.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from intelligence_preservation_gate import GateConfig, evaluate  # noqa: E402
from intelligence_preservation_run import (BASELINE, MODES, REQUIRED_METRICS,  # noqa: E402
                                           SUFFICIENT_SAMPLES_PER_METRIC, RunnerError, Task,
                                           build_payload, load_tasks, measure, score)

TASKS = ROOT / "docs" / "benchmark" / "intelligence_tasks.json"
SHA = "a" * 40


def test_the_task_set_covers_every_metric_the_gate_requires():
    tasks = load_tasks(TASKS)
    covered = {t.metric for t in tasks}
    assert covered == set(REQUIRED_METRICS), sorted(set(REQUIRED_METRICS) - covered)
    assert len({t.task_id for t in tasks}) == len(tasks)


def test_every_task_can_be_scored_at_all():
    """Задача, которую нечем проверить, — не задача."""
    for task in load_tasks(TASKS):
        assert isinstance(score(task, "какой-то ответ"), bool)


def test_the_lanes_differ_only_by_the_scaffolding_never_by_the_task():
    task = Task("t", "reasoning_accuracy", "сколько будет 2+2?", {"kind": "equals", "value": "4"},
                context="важный факт")
    raw = task.rendered("raw")
    assert len(raw) == 1 and raw[0]["role"] == "user"
    assert "важный факт" not in raw[0]["content"]         # RAW не видит контекста
    assert task.rendered("system")[0]["role"] == "system"
    assert "важный факт" in task.rendered("context")[-1]["content"]
    assert "инструмент" in task.rendered("full")[0]["content"].lower()
    for mode in MODES:                                     # сама задача одна и та же
        assert "2+2" in task.rendered(mode)[-1]["content"]


@pytest.mark.parametrize("answer,expected", [
    ("4", True), (" 4 ", True), ("четыре", False), ("", False),
])
def test_equals_scoring_is_exact_but_forgiving_of_whitespace(answer, expected):
    task = Task("t", "reasoning_accuracy", "?", {"kind": "equals", "value": "4"})
    assert score(task, answer) is expected


def test_json_scoring_reads_a_fenced_answer_and_rejects_prose():
    task = Task("t", "structured_output_accuracy", "?",
                {"kind": "json_fields", "fields": {"name": "Ада", "age": "36"}})
    assert score(task, '```json\n{"name": "Ада", "age": 36}\n```') is True
    assert score(task, '{"name": "Ада"}') is False          # поля не хватает
    assert score(task, "Ада, 36 лет") is False              # это не JSON


def test_tool_scoring_refuses_an_answer_that_names_the_neighbour_too():
    task = Task("t", "tool_selection_accuracy", "?",
                {"kind": "tool", "name": "fs.read", "not": ["fs.write"]})
    assert score(task, "fs.read") is True
    assert score(task, "можно fs.read или fs.write") is False
    assert score(task, "fs.write") is False


def test_hallucination_scoring_counts_taking_the_bait_as_failure():
    task = Task("t", "hallucination_rate", "?", {"kind": "absent", "none_of": ["2031"]})
    assert score(task, "не знаю") is True
    assert score(task, "вышла в 2031 году") is False


def _lanes(answers):
    """answers: {mode: {task_id: ответ}}."""
    tasks = [Task(f"t{i}", "reasoning_accuracy", "?", {"kind": "equals", "value": "4"})
             for i in range(3)]
    calls = {"mode": None}
    order = list(MODES)

    def call(messages):
        # Полосы идут по порядку MODES, задачи внутри — по порядку списка.
        return call.script.pop(0)
    call.script = [answers[mode][t.task_id] for mode in order for t in tasks]
    return tasks, measure(tasks, call)


def test_paired_discordance_counts_what_the_scaffolding_lost_and_gained():
    """Смысл парности: не «сколько верно», а «что именно полоса потеряла»."""
    tasks, lanes = _lanes({
        "raw":     {"t0": "4", "t1": "4", "t2": "x"},
        "system":  {"t0": "4", "t1": "x", "t2": "4"},   # одну потеряла, одну приобрела
        "context": {"t0": "4", "t1": "4", "t2": "4"},   # ничего не потеряла, одну приобрела
        "full":    {"t0": "x", "t1": "x", "t2": "x"},   # потеряла обе
    })
    m = lanes["system"]["reasoning_accuracy"]
    assert (m.lost, m.gained) == (1, 1) and m.samples == 3
    assert lanes["context"]["reasoning_accuracy"].lost == 0
    assert lanes["context"]["reasoning_accuracy"].gained == 1
    assert lanes["full"]["reasoning_accuracy"].lost == 2
    assert BASELINE not in {"system", "context", "full"}


def test_every_lane_answers_the_same_items_so_the_gate_can_pair_them():
    _, lanes = _lanes({m: {f"t{i}": "4" for i in range(3)} for m in MODES})
    counts = {m: lanes[m]["reasoning_accuracy"].samples for m in MODES}
    assert len(set(counts.values())) == 1, counts


def test_a_model_that_does_not_answer_fails_the_item_instead_of_dropping_it():
    """Выброшенная задача ломает парность — молчание модели это провал, не пропуск."""
    tasks = [Task("t0", "reasoning_accuracy", "?", {"kind": "equals", "value": "4"})]

    def call(messages):
        raise ConnectionResetError("model went away")

    lanes = measure(tasks, call)
    for mode in MODES:
        metric = lanes[mode]["reasoning_accuracy"]
        assert metric.samples == 1 and metric.passed == 0.0


def test_the_hallucination_metric_is_inverted_so_higher_is_worse():
    """Для выдумок единица — плохо. Ошибка знака здесь превратила бы модель,
    которая врёт на каждом вопросе, в образцовую."""
    tasks = [Task(f"h{i}", m, "?", {"kind": "absent", "none_of": ["2031"]}
                  if m == "hallucination_rate" else {"kind": "equals", "value": "не знаю"})
             for i, m in enumerate(REQUIRED_METRICS)]
    clean = build_payload(measure(tasks, lambda m: "не знаю"),
                          model="m", dataset_id="d", evaluated_sha=SHA)
    assert clean["modes"]["raw"]["hallucination_rate"]["score"] == 0.0
    liar = build_payload(measure(tasks, lambda m: "вышла в 2031"),
                         model="m", dataset_id="d", evaluated_sha=SHA)
    assert liar["modes"]["raw"]["hallucination_rate"]["score"] == 1.0


def test_a_metric_with_no_tasks_is_a_refusal_not_a_zero():
    """Метрика без задач — дефект НАБОРА. Ноль отправил бы владельца чинить
    гейт («samples must be positive») вместо собственного набора."""
    lanes = measure([Task("t0", "reasoning_accuracy", "?", {"kind": "equals", "value": "4"})],
                    lambda m: "4")
    with pytest.raises(RunnerError, match="covers no items for 'coding_correctness'"):
        build_payload(lanes, model="m", dataset_id="d", evaluated_sha=SHA)


# --------------------------------------------- сквозная проверка через гейт

def test_a_perfect_run_produces_a_payload_the_real_gate_accepts_structurally():
    """Файл обязан быть тем самым, который читает гейт, а не похожим на него."""
    tasks = load_tasks(TASKS)
    answers = {}
    for task in tasks:
        expect = task.expect
        kind = expect["kind"]
        if kind == "equals":
            answers[task.task_id] = expect["value"]
        elif kind == "contains":
            answers[task.task_id] = " ".join(expect["all_of"])
        elif kind == "absent":
            answers[task.task_id] = "не знаю"
        elif kind == "tool":
            answers[task.task_id] = expect["name"]
        else:
            answers[task.task_id] = json.dumps(expect["fields"], ensure_ascii=False)

    lanes = measure(tasks, lambda messages: answers[_task_id_of(messages, tasks)])
    payload = build_payload(lanes, model="qwen2.5-coder:14b", dataset_id="bossman-retention-v1",
                            evaluated_sha=SHA)
    # Набор рассчитан ровно на порог гейта: 20 задач на каждую требуемую метрику.
    for name in REQUIRED_METRICS:
        assert payload["modes"]["raw"][name]["samples"] == 20, name
    report = evaluate(payload, GateConfig(expect_sha=SHA, min_samples_per_metric=20))
    assert report["status"] in ("PASS", "NO_GO", "INSUFFICIENT_EVIDENCE"), report
    # Идеальный прогон во всех полосах: ни одна полоса ничего не потеряла.
    for lane in ("system", "context", "full"):
        assert payload["modes"][lane]["reasoning_accuracy"]["paired"]["lost"] == 0
    


def test_a_lane_that_loses_core_ability_is_not_a_pass():
    """Отрицательный контроль ко всему измерению: если обвязка ломает модель,
    вердикт обязан это увидеть, иначе гейт бесполезен."""
    tasks = load_tasks(TASKS)
    def call(messages):
        task_id = _task_id_of(messages, tasks)
        task = next(t for t in tasks if t.task_id == task_id)
        # FULL отвечает мусором на треть задач — обвязка отняла способность.
        if any("инструмент" in m["content"].lower() for m in messages if m["role"] == "system"):
            if hash(task_id) % 3 == 0:
                return "не знаю"
        return _perfect_answer(task)
    lanes = measure(tasks, call)
    payload = build_payload(lanes, model="m", dataset_id="bossman-retention-v1",
                            evaluated_sha=SHA)
    report = evaluate(payload, GateConfig(expect_sha=SHA, min_samples_per_metric=20))
    assert report["status"] != "PASS", report["status"]
    assert payload["modes"]["full"]["reasoning_accuracy"]["paired"]["lost"] > 0


def _perfect_answer(task):
    expect = task.expect
    kind = expect["kind"]
    if kind == "equals":
        return expect["value"]
    if kind == "contains":
        return " ".join(expect["all_of"])
    if kind == "absent":
        return "не знаю"
    if kind == "tool":
        return expect["name"]
    return json.dumps(expect["fields"], ensure_ascii=False)


def _task_id_of(messages, tasks):
    body = messages[-1]["content"]
    for task in tasks:
        if task.prompt in body:
            return task.task_id
    raise AssertionError("prompt not found")


def test_the_payload_is_bound_to_the_commit_under_test():
    """Вердикт по старому SHA не является вердиктом по новому."""
    tasks = load_tasks(TASKS)
    lanes = measure(tasks, lambda messages: _perfect_answer(
        next(t for t in tasks if t.task_id == _task_id_of(messages, tasks))))
    payload = build_payload(lanes, model="m", dataset_id="d", evaluated_sha=SHA)
    with pytest.raises(ValueError, match="not the commit under test"):
        evaluate(payload, GateConfig(expect_sha="b" * 40))


def test_a_task_set_naming_an_unknown_metric_is_refused(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"dataset_id": "x", "tasks": [
        {"task_id": "a", "metric": "vibes", "prompt": "?", "expect": {"kind": "equals", "value": "1"}}
    ]}), encoding="utf-8")
    with pytest.raises(RunnerError, match="metrics the gate does not know"):
        load_tasks(path)
