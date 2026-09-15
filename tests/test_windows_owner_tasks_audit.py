"""Приговор длинного прогона обязан уметь провалиться.

`audit_markers` — единственное место, которое решает, потерял ли продукт
подтверждённую команду и не исполнил ли её дважды. Если эта функция не умеет
сказать «плохо», то зелёный результат прогона не значит ничего.

Поэтому здесь к ней подводят четыре ПОДЛОГА, каждый из которых имитирует свой
класс дефекта, и требуют, чтобы она его назвала. Плюс обратный контроль на
чистых данных: если бы функция ругалась всегда, тесты ниже тоже были бы
зелёными и тоже ничего не доказывали.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from windows_owner_tasks import audit_markers  # noqa: E402


def _disk(tmp_path: Path, rows: dict[str, list[str]]) -> Path:
    done = tmp_path / "hard"
    done.mkdir()
    for marker, lines in rows.items():
        (done / f"{marker}.txt").write_text("".join(f"{ln}\n" for ln in lines), encoding="utf-8")
    return done


def _clean(tmp_path: Path):
    markers = [f"HARD-{i:04d}" for i in range(1, 6)]
    return _disk(tmp_path, {m: [m] for m in markers}), markers


def test_clean_run_produces_no_findings(tmp_path: Path) -> None:
    """Обратный контроль: на честных данных функция обязана молчать."""
    done, markers = _clean(tmp_path)
    facts = audit_markers(done, markers, markers)
    assert facts == {"файлов": 5, "потеряно": [], "дважды": {}, "лишние": [], "подменено": {}}


def test_a_confirmed_command_that_left_no_file_is_reported_lost(tmp_path: Path) -> None:
    """Движок отчитался об успехе, а эффекта нет — ровно тот случай, ради
    которого нельзя верить `success = true`."""
    done, markers = _clean(tmp_path)
    (done / "HARD-0003.txt").unlink()
    facts = audit_markers(done, markers, markers)
    assert facts["потеряно"] == ["HARD-0003"]
    assert facts["файлов"] == 4


def test_a_command_executed_twice_is_reported(tmp_path: Path) -> None:
    """Перезапуск после падения не имеет права переисполнить то, что уже прошло."""
    done, markers = _clean(tmp_path)
    (done / "HARD-0002.txt").write_text("HARD-0002\nHARD-0002\n", encoding="utf-8")
    facts = audit_markers(done, markers, markers)
    assert facts["дважды"] == {"HARD-0002": ["HARD-0002", "HARD-0002"]}
    assert facts["потеряно"] == []


def test_a_command_the_owner_never_issued_is_reported(tmp_path: Path) -> None:
    """Исполнено то, чего никто не подтверждал."""
    done, markers = _clean(tmp_path)
    (done / "HARD-0099.txt").write_text("HARD-0099\n", encoding="utf-8")
    facts = audit_markers(done, markers, markers)
    assert facts["лишние"] == ["HARD-0099"]


def test_a_file_holding_someone_elses_marker_is_reported(tmp_path: Path) -> None:
    """Файл есть, но в нём результат ДРУГОЙ команды: подсчёт по наличию файла
    такое пропустил бы, а содержимое — нет."""
    done, markers = _clean(tmp_path)
    (done / "HARD-0004.txt").write_text("HARD-0001\n", encoding="utf-8")
    facts = audit_markers(done, markers, markers)
    assert facts["подменено"] == {"HARD-0004": ["HARD-0001"]}
    assert facts["потеряно"] == ["HARD-0004"]


def test_an_inflight_command_without_a_file_is_not_called_lost(tmp_path: Path) -> None:
    """Команда, которую убили в полёте, НЕ подтверждена. Считать её потерей —
    значит красить в красный честный исход; поэтому в `confirmed` её нет.
    Но если бы она исполнилась дважды, это увидели бы всё равно."""
    done, markers = _clean(tmp_path)
    inflight = "HARD-0006"
    facts = audit_markers(done, [*markers, inflight], markers)
    assert facts["потеряно"] == [] and facts["лишние"] == []

    (done / f"{inflight}.txt").write_text(f"{inflight}\n{inflight}\n", encoding="utf-8")
    twice = audit_markers(done, [*markers, inflight], markers)
    assert twice["дважды"] == {inflight: [inflight, inflight]}


def test_empty_disk_with_confirmed_commands_is_a_total_loss(tmp_path: Path) -> None:
    done = tmp_path / "hard"
    done.mkdir()
    facts = audit_markers(done, ["HARD-0001"], ["HARD-0001"])
    assert facts["потеряно"] == ["HARD-0001"] and facts["файлов"] == 0


@pytest.mark.parametrize("noise", ["notes.txt", "HARD.txt", "hard-0001.txt"])
def test_only_the_run_own_files_are_audited(tmp_path: Path, noise: str) -> None:
    """Посторонний файл рядом не должен превращаться в «лишнее исполнение»."""
    done, markers = _clean(tmp_path)
    (done / noise).write_text("посторонний\n", encoding="utf-8")
    assert audit_markers(done, markers, markers)["лишние"] == []
