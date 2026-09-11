"""Раннер замера удержания обязан отказываться, а не выдумывать.

Гейт `intelligence_preservation_gate.py` годами отвечал INSUFFICIENT_EVIDENCE
не из-за деградации, а потому что файл замера было нечем произвести. Раннер
эту дыру закрывает — и вместе с ней открывает новую опасность: соблазн выдать
любой JSON за замер. Эти тесты держат раннер честным.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import intelligence_retention_run as runner  # noqa: E402


def _layers(system="ПОЛИТИКА", context="КОНТЕКСТ", tools="ИНСТРУМЕНТЫ"):
    return {"system": system, "context": context, "tools": tools}


def _item(metric="reasoning_accuracy"):
    return {"id": "i1", "metric": metric, "prompt": "два плюс два",
            "check": {"kind": "exact", "value": "4"}}


def test_lanes_must_actually_differ() -> None:
    """Совпавшие лейны показали бы 100% удержания, ничего не измерив."""
    with pytest.raises(runner.Unmeasurable) as same:
        runner.refuse_identical_lanes([_item()], _layers(system="X", context="X", tools="Y"))
    assert "FAKE_LANES" in str(same.value)

    with pytest.raises(runner.Unmeasurable):
        runner.refuse_identical_lanes([_item()], _layers(context="   "))

    runner.refuse_identical_lanes([_item()], _layers())          # различимые слои проходят


def test_each_lane_adds_exactly_the_layers_it_declares() -> None:
    item, layers = _item(), _layers()
    assert runner.lane_layers("raw") == []
    raw = runner.build_envelope(item, "raw", layers)
    assert [m["role"] for m in raw["messages"]] == ["user"]
    full = runner.build_envelope(item, "full", layers)
    assert [m["role"] for m in full["messages"]] == ["system", "system", "user"]
    assert layers["tools"] in full["messages"][-1]["content"]
    digests = {lane: runner.envelope_digest(runner.build_envelope(item, lane, layers))
               for lane in runner.LANES}
    assert len(set(digests.values())) == 4


def test_a_partial_run_is_not_evidence() -> None:
    """Метрика без замеров в каком-то лейне обязана прервать выпуск нагрузки."""
    outcomes = {lane: {m: [True] * 3 for m in runner.METRICS} for lane in runner.LANES}
    outcomes["full"].pop("coding_correctness")
    with pytest.raises(runner.Unmeasurable) as exc:
        runner.to_payload(outcomes, model="m", dataset_id="d", sha="a" * 40)
    assert "coding_correctness" in str(exc.value)


def test_payload_carries_paired_discordance_against_the_raw_lane() -> None:
    outcomes = {lane: {m: [True] * 4 for m in runner.METRICS} for lane in runner.LANES}
    outcomes["full"]["reasoning_accuracy"] = [True, False, False, True]
    outcomes["raw"]["reasoning_accuracy"] = [True, True, False, True]
    payload = runner.to_payload(outcomes, model="m", dataset_id="d", sha="b" * 40)
    entry = payload["modes"]["full"]["reasoning_accuracy"]
    assert entry["samples"] == 4 and entry["score"] == 0.5
    assert entry["paired"] == {"lost": 1, "gained": 0}
    assert "paired" not in payload["modes"]["raw"]["reasoning_accuracy"]


def test_lower_is_better_metric_is_inverted_once() -> None:
    """hallucination_rate: попадание проверки = отсутствие выдумки."""
    outcomes = {lane: {m: [True] * 5 for m in runner.METRICS} for lane in runner.LANES}
    outcomes["full"]["hallucination_rate"] = [True, True, True, True, False]
    payload = runner.to_payload(outcomes, model="m", dataset_id="d", sha="c" * 40)
    assert payload["modes"]["full"]["hallucination_rate"]["score"] == pytest.approx(0.2)
    assert payload["modes"]["raw"]["hallucination_rate"]["score"] == pytest.approx(0.0)


@pytest.mark.parametrize("check,answer,expected", [
    ({"kind": "exact", "value": "4"}, " 4 ", True),
    ({"kind": "exact", "value": "4"}, "четыре", False),
    ({"kind": "contains", "value": ["альфа", "бета"]}, "Альфа и БЕТА", True),
    ({"kind": "absent", "value": ["выдумка"]}, "честный ответ", True),
    ({"kind": "absent", "value": ["выдумка"]}, "это выдумка", False),
    ({"kind": "regex", "value": r"^\d+$"}, "42", True),
    ({"kind": "json_keys", "value": ["a", "b"]}, 'текст {"a":1,"b":2} хвост', True),
    ({"kind": "json_keys", "value": ["a", "b"]}, "не json", False),
])
def test_grading_is_deterministic_and_declared_by_the_item(check, answer, expected) -> None:
    assert runner.grade({"check": check}, answer) is expected


def test_an_unreachable_model_is_refused_not_guessed() -> None:
    with pytest.raises(runner.Unmeasurable) as exc:
        runner.ask("http://127.0.0.1:1/v1", "m", None, {"messages": []}, timeout=2)
    assert "модель недоступна" in str(exc.value)
