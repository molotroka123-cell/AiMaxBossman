"""Репетиция обновления/отката обязана быть исполняемой, а не описанной.

Сам сценарий живёт в `scripts/update_rollback_rehearsal.py`: он пишет данные
владельца настоящим runtime, снимает резерв, портит базу «новой сборкой»,
требует от прежней сборки отказа по имени и возвращает резерв.

Здесь он прогоняется как тест — чтобы правка, снявшая защиту, краснела в CI,
а не обнаруживалась на машине владельца. Мутационный контроль ниже проверяет
ровно это: без отказа шаг 4 обязан стать FAIL.
"""
import asyncio
import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _rehearsal():
    """Грузит сценарий, не оставляя следа в sys.path верхнего уровня."""
    spec = importlib.util.spec_from_file_location(
        "update_rollback_rehearsal", REPO / "scripts" / "update_rollback_rehearsal.py")
    module = importlib.util.module_from_spec(spec)
    before = list(sys.path)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = before
    return module


@pytest.mark.asyncio
async def test_the_owner_data_survives_an_update_and_a_rollback(tmp_path):
    result = await _rehearsal().rehearse(tmp_path / "run")
    failed = [s for s in result["steps"] if s["verdict"] != "PASS"]
    assert not failed, failed
    assert result["verdict"] == "PASS"


@pytest.mark.asyncio
async def test_the_rehearsal_turns_red_when_the_refusal_is_removed(tmp_path, monkeypatch):
    """Негативный контроль: сценарий, который нельзя провалить, ничего не меряет."""
    module = _rehearsal()
    import bcc.db as db

    async def _no_guard(self):
        return None

    monkeypatch.setattr(db.Database, "_refuse_a_newer_database", _no_guard)
    result = await module.rehearse(tmp_path / "blind")
    assert result["verdict"] == "FAIL"
    refusal_step = [s for s in result["steps"] if "ОТКАЗАЛ" in s["step"]][0]
    assert refusal_step["verdict"] == "FAIL"
    assert "молча" in refusal_step["detail"], refusal_step["detail"]


def test_the_rehearsal_names_what_it_does_not_cover():
    """Улика не имеет права выглядеть как доказанный откат целиком."""
    module = _rehearsal()
    with tempfile.TemporaryDirectory() as tmp:
        result = asyncio.run(module.rehearse(Path(tmp) / "scope"))
    assert result["scope"]["UPDATE_ROLLBACK_ARCHIVE"] == "OWNER_REQUIRED"
    assert result["scope"]["UPDATE_ROLLBACK_PRE_STAMP"] == "NOT_COVERED"
    assert any("Windows" in line for line in result["not_covered"])
    assert any("резерв" in line for line in result["not_covered"])
