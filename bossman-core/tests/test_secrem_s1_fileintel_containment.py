"""S1 (P0) — containment путей в toolkit/fileintel (file.parse / artifact.create).

REPRO: `_resolve` сверял путь с workdir по префиксу строки
(`str(p).startswith(str(workdir))`). Сосед с общим префиксом имени
(`.../coder-secrets` при workdir `.../coder`) проходил проверку, поэтому
`artifact.create(path="../coder-secrets/pwned.md")` писал файл ЗА пределами
рабочей папки, а `file.parse` тем же путём читал чужой секрет. Оба инструмента
зарегистрированы с confirm_default=False, то есть без подтверждения владельца.

Ожидаемое поведение — ровно то же, что у fs.* (files.py): отношение путей
через `files._contains`, любой выход (сосед по префиксу, абсолютный путь,
symlink) — PermissionError.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from bossman.toolkit import REGISTRY, ToolContext, files, fileintel


@pytest.fixture()
def wd(tmp_path: Path) -> Path:
    """workdir `.../coder` и сосед `.../coder-secrets` с общим префиксом имени."""
    work = tmp_path / "coder"
    work.mkdir()
    sibling = tmp_path / "coder-secrets"
    sibling.mkdir()
    (sibling / "secret.txt").write_text("TOP SECRET", encoding="utf-8")
    return work


def ctx_for(work: Path) -> ToolContext:
    return ToolContext(agent="coder", workdir=work)


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- write path

def test_artifact_create_rejects_sibling_prefix_escape(wd: Path):
    """REPRO S1: `../coder-secrets/pwned.md` писался наружу (startswith)."""
    with pytest.raises(PermissionError):
        run(fileintel.artifact_create(
            {"path": "../coder-secrets/pwned.md", "format": "md", "content": "OWNED"},
            ctx_for(wd)))
    assert not (wd.parent / "coder-secrets" / "pwned.md").exists(), \
        "artifact.create записал файл вне workdir"


def test_artifact_create_rejects_absolute_path_outside(wd: Path):
    outside = wd.parent / "coder-secrets" / "abs.md"
    with pytest.raises(PermissionError):
        run(fileintel.artifact_create(
            {"path": str(outside), "format": "md", "content": "OWNED"}, ctx_for(wd)))
    assert not outside.exists()


def test_artifact_create_rejects_symlink_escape(wd: Path):
    """Symlink внутри workdir, целящий наружу: сравнивается реальная цель."""
    (wd / "out").symlink_to(wd.parent / "coder-secrets", target_is_directory=True)
    with pytest.raises(PermissionError):
        run(fileintel.artifact_create(
            {"path": "out/pwned.md", "format": "md", "content": "OWNED"}, ctx_for(wd)))
    assert not (wd.parent / "coder-secrets" / "pwned.md").exists()


def test_artifact_create_still_writes_inside_workdir(wd: Path):
    """Регресс не должен запретить нормальную работу инструмента."""
    res = run(fileintel.artifact_create(
        {"path": "sub/report.md", "format": "md", "content": "ok"}, ctx_for(wd)))
    assert not res.error
    assert (wd / "sub" / "report.md").read_bytes() == b"ok"


# ----------------------------------------------------------------- read path

def test_file_parse_rejects_sibling_prefix_escape(wd: Path):
    """REPRO S1 (чтение): тот же префикс — тот же выход наружу."""
    with pytest.raises(PermissionError):
        run(fileintel.file_parse({"path": "../coder-secrets/secret.txt"}, ctx_for(wd)))


def test_file_parse_rejects_absolute_path_outside(wd: Path):
    with pytest.raises(PermissionError):
        run(fileintel.file_parse(
            {"path": str(wd.parent / "coder-secrets" / "secret.txt")}, ctx_for(wd)))


def test_file_parse_rejects_symlink_escape(wd: Path):
    (wd / "leak.txt").symlink_to(wd.parent / "coder-secrets" / "secret.txt")
    with pytest.raises(PermissionError):
        run(fileintel.file_parse({"path": "leak.txt"}, ctx_for(wd)))


def test_file_parse_still_reads_inside_workdir(wd: Path):
    (wd / "own.txt").write_text("hello", encoding="utf-8")
    res = run(fileintel.file_parse({"path": "own.txt"}, ctx_for(wd)))
    assert not res.error and "hello" in res.content


# ------------------------------------------------------- общая дисциплина fs.*

def test_fileintel_reuses_the_fs_containment_helper():
    """Докстринг обещает «РОВНО тот же containment, что у fs.*» — это должно быть
    одной и той же функцией, а не второй копией правила."""
    assert fileintel._contains is files._contains


@pytest.mark.parametrize("rel", [
    "../coder-secrets/x.md", "../../etc/passwd", "sub/../../coder-secrets/x.md",
])
def test_fileintel_and_files_resolve_agree_on_escapes(wd: Path, rel: str):
    """fs.* и file.parse/artifact.create отвечают на один и тот же путь одинаково."""
    ctx = ctx_for(wd)
    with pytest.raises(PermissionError):
        files._resolve(ctx, rel)
    with pytest.raises(PermissionError):
        fileintel._resolve(ctx, rel)


def test_write_tools_are_registered_without_silent_auto_write():
    """Инструменты подключены (F-018) — значит containment их единственная защита:
    artifact.create идёт с confirm_default=False, поэтому путь обязан быть
    жёстко ограничен рабочей папкой."""
    assert "artifact.create" in REGISTRY and "file.parse" in REGISTRY
    assert REGISTRY["artifact.create"].rights == "write"
