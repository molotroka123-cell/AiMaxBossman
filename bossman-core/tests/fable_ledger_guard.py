"""Страховка вокруг денег для тестов, которые трогают журнал потолка Fable.

Журнал HARD-$3 — один durable-файл на реальной машине владельца. Тест, снявший
там резерв по-настоящему, съел бы настоящие деньги и оставил бы их съеденными,
поэтому каждому тесту выдаётся свой файл.

Почему это подключается пофайлово, а не autouse-фикстурой в conftest: журнал
трогают четыре файла, а conftest навязал бы фикстуру всем ~1800 тестам пакета,
включая benchmark-набор, который от лишней фикстуры на каждый тест ломался.
Общая страховка не должна стоить чужому набору зелёного прогона.

Перенаправление — патч атрибута в процессе, а не настройка: переменной
окружения, двигающей журнал, нет намеренно, потому что журнал, который можно
перенести, — это потолок, который можно поднять.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path and (_ROOT / "bossman_shared").is_dir():
    sys.path.insert(0, str(_ROOT))


@pytest.fixture(autouse=True)
def fable_ledger_off_the_real_machine(tmp_path, monkeypatch):
    from bossman_shared import fable_budget
    monkeypatch.setattr(fable_budget, "LEDGER_PATH", tmp_path / "fable_hard_cap.json")
