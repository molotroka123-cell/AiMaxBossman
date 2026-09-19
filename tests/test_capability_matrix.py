"""Матрица возможностей обязана быть проверяемой, иначе это просто таблица.

Раздел 7 задания: четыре состояния и ни одного пятого; каждая строка со
ссылкой на улику; `INSUFFICIENT_EVIDENCE` никогда не становится `PASS`.

Здесь проверяется ровно это — и главное, что улика СУЩЕСТВУЕТ. Матрица,
ссылающаяся на файл, которого нет, врёт убедительнее прозы: она выглядит
проверенной.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MATRIX = REPO / "docs" / "v8" / "CAPABILITY_MATRIX.json"
STATES = {"IMPLEMENTED_AND_TESTED", "IMPLEMENTED_LIVE_PENDING",
          "NOT_IMPLEMENTED", "OWNER_REQUIRED"}


@pytest.fixture(scope="module")
def matrix():
    return json.loads(MATRIX.read_text(encoding="utf-8"))


def _evidence_path(reference: str) -> Path:
    """Улика может указывать на файл, на тест в нём или на якорь в документе."""
    return REPO / reference.split("::", 1)[0].split("#", 1)[0]


def test_every_row_uses_one_of_the_four_states_and_no_fifth(matrix):
    for row in matrix["capabilities"]:
        assert row["state"] in STATES, (row["id"], row["state"])
    assert set(matrix["states"]) == STATES


def test_every_row_names_an_evidence_file_that_actually_exists(matrix):
    """Самая важная проверка: ссылка на несуществующий файл — это ложь."""
    for row in matrix["capabilities"]:
        assert row["evidence"], row["id"]
        for reference in row["evidence"]:
            path = _evidence_path(reference)
            assert path.exists(), f"{row['id']}: улика {reference} не существует ({path})"


def test_a_named_test_inside_an_evidence_file_is_really_there(matrix):
    """`файл::тест` обязан существовать не только файлом, но и тестом."""
    checked = 0
    for row in matrix["capabilities"]:
        for reference in row["evidence"]:
            if "::" not in reference:
                continue
            path, name = reference.split("::", 1)
            body = (REPO / path).read_text(encoding="utf-8")
            assert f"def {name}(" in body, f"{row['id']}: в {path} нет теста {name}"
            checked += 1
    assert checked, "ни одна строка не ссылается на конкретный тест — проверка холостая"


def test_owner_required_rows_say_what_the_owner_supplies(matrix):
    """«Нужен владелец» без «чего именно» — отговорка, а не состояние."""
    for row in matrix["capabilities"]:
        if row["state"] != "OWNER_REQUIRED":
            continue
        supplies = row.get("owner_supplies", "")
        assert supplies.strip(), f"{row['id']}: не сказано, ЧТО предоставляет владелец"


def test_intelligence_preservation_is_never_a_pass(matrix):
    """Гейт красный из-за ОТСУТСТВИЯ замера; подменять это словом PASS нельзя."""
    row = next(r for r in matrix["capabilities"]
               if r["id"] == "intelligence.preservation")
    assert row["state"] == "OWNER_REQUIRED"
    assert row["measurement"] == "INSUFFICIENT_EVIDENCE"
    assert "парный" in row["owner_supplies"].lower(), \
        "замер обязан быть парным: прогон обвязки — не замер сохранности"
    # Проверяем ПОЛЯ ВЕРДИКТА, а не текст: пояснение имеет полное право
    # содержать слово PASS — оно там как раз затем, чтобы сказать «не станет».
    assert row["state"] != "PASS" and row["measurement"] != "PASS"
    assert "не превращается в pass" in row["note"].lower()


def test_no_row_claims_a_linux_source_run_proves_the_windows_archive(matrix):
    """Ровно та подмена, на которой легче всего себя обмануть."""
    rules = " ".join(matrix["rules"]).lower()
    assert "не доказательство об установленном архиве windows" in rules
    perf = next(r for r in matrix["capabilities"] if r["id"] == "perf.measured_source")
    assert "reference_only" in perf["note"].lower()
    assert "не является" in perf["note"].lower()


def test_the_open_money_risk_is_stated_as_not_implemented(matrix):
    """BL-084 обязан стоять открытым, пока он открыт."""
    row = next(r for r in matrix["capabilities"] if r["id"] == "studio.price_gate_zero")
    assert row["state"] == "NOT_IMPLEMENTED"
    assert "BL-084" in " ".join(row["evidence"])
    ledger = (REPO / "docs" / "final" / "BUG_LEDGER.md").read_text(encoding="utf-8")
    assert "BL-084" in ledger, "строка матрицы ссылается на запись, которой нет в журнале"


def test_ids_are_unique(matrix):
    ids = [row["id"] for row in matrix["capabilities"]]
    assert len(ids) == len(set(ids)), sorted(i for i in ids if ids.count(i) > 1)


def test_the_readable_matrix_matches_its_source(matrix):
    """Второй список, который пишут руками, расходится молча. Здесь не разойдётся."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "render_capability_matrix", REPO / "tools" / "render_capability_matrix.py")
    module = importlib.util.module_from_spec(spec)
    before = list(sys.path)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = before

    assert module.main(["--check"]) == 0, (
        "docs/v8/CAPABILITY_MATRIX.md разошёлся с CAPABILITY_MATRIX.json — "
        "перерисуйте: python tools/render_capability_matrix.py")
    body = (REPO / "docs" / "v8" / "CAPABILITY_MATRIX.md").read_text(encoding="utf-8")
    for row in matrix["capabilities"]:
        assert row["title"] in body, row["id"]
