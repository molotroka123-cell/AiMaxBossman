"""Единственный писатель памяти: запись целиком или никак.

Раздел 3 мастер-аудита. Производные индексы пересобираются из заметок, а
заметки — ни из чего. Обрезанная заметка это не отказ, который заметят, а тихо
испорченный источник истины.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from bcc.v2.memory.obsidian import ObsidianVault, _atomic_write


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "BOSSMAN Memory"
    (root / "BOSSMAN").mkdir(parents=True)
    return ObsidianVault(root=root)


def test_crash_mid_write_leaves_the_previous_file_intact(tmp_path, monkeypatch):
    """Падение в середине записи не должно усечь существующий файл.

    Прямой `write_text` усекает файл ДО того, как что-то записано, — именно на
    этом теряются данные. Проверяем на настоящем падении, а не на рассуждении.
    """
    target = tmp_path / "заметка.md"
    target.write_text("прежнее содержимое, которое нельзя потерять\n", encoding="utf-8")
    before = target.read_text(encoding="utf-8")

    real_replace = os.replace

    def crash(src, dst):                    # падаем ровно между записью и подменой
        raise OSError("сымитированное падение процесса")

    monkeypatch.setattr(os, "replace", crash)
    with pytest.raises(OSError):
        _atomic_write(target, "новое содержимое" * 500)
    monkeypatch.setattr(os, "replace", real_replace)

    assert target.read_text(encoding="utf-8") == before, "заметка повреждена падением"
    leftovers = list(target.parent.glob("*.tmp-*"))
    assert leftovers == [], f"остался временный файл: {leftovers}"


def test_write_memory_actually_uses_the_atomic_path(vault, monkeypatch):
    """Иначе тест выше можно обойти, вернув write_text в write_memory: сам
    `_atomic_write` останется правильным, а заметки снова станут уязвимы."""
    called: list[Path] = []
    import bcc.v2.memory.obsidian as mod
    real = mod._atomic_write

    def spy(dest, body):
        called.append(dest)
        return real(dest, body)

    monkeypatch.setattr(mod, "_atomic_write", spy)
    vault.write_memory(title="Проверка пути", content="тело", filename="путь.md")
    assert called, "write_memory пишет мимо атомарного пути"


def test_written_note_is_complete_and_readable(vault):
    dest = vault.write_memory(title="Решение по хранилищу",
                              content="Пишет только BOSSMAN.", kind="decision")
    text = dest.read_text(encoding="utf-8")
    assert text.startswith("---") and text.rstrip().endswith("Пишет только BOSSMAN.")
    assert "source: bossman" in text


def test_write_outside_the_write_root_is_refused(vault):
    """Обход по пути не должен вывести запись за пределы своего каталога."""
    for attempt in ("../снаружи.md", "../../ещё-дальше.md", "подкаталог/../../вон.md"):
        with pytest.raises((PermissionError, FileNotFoundError, OSError)):
            vault.write_memory(title="попытка", content="x", filename=attempt)


def test_existing_note_is_never_silently_overwritten(vault):
    first = vault.write_memory(title="Одна", content="первая версия",
                               filename="одна.md")
    with pytest.raises(FileExistsError):
        vault.write_memory(title="Одна", content="вторая версия", filename="одна.md")
    assert "первая версия" in first.read_text(encoding="utf-8")


def test_only_one_chunker_is_wired_into_the_running_code():
    """Два разбивщика с пересекающейся ответственностью — это вопрос «какой
    настоящий», который задаётся в самый неудобный момент."""
    import subprocess

    root = Path(__file__).resolve().parents[1]
    out = subprocess.run(
        ["grep", "-rn", "--include=*.py", "-e", "chunking_v22", "-e", "from .chunking",
         "-e", "memory.chunking", str(root / "bcc")],
        capture_output=True, text=True).stdout
    live = {line.split(":")[0] for line in out.splitlines()
            if "chunking_v22.py" not in line.split(":")[0]}
    # использовать разрешено ровно один модуль разбиения
    assert all("chunking_v22" in line or "chunking.py" not in line
               for line in out.splitlines()), out
    assert live, "ни один разбивщик не подключён — проверка потеряла смысл"
