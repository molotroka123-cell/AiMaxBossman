"""plugin:obsidian.read — каталог или пустой путь вместо заметки.

Дефект (zone plugins, 2026-10-06): `confine_path("")` законно возвращает сам
корень vault, а `"sub"` — подкаталог; хендлер делал `read_text` по каталогу,
и наружу вылетал сырой `PermissionError` (Windows) / `IsADirectoryError`
(POSIX) с АБСОЛЮТНЫМ путём vault владельца. Хендлер обязан отвечать
ToolResult-ошибкой, как на любой другой отказ, и не раскрывать путь.
"""
from __future__ import annotations

import pytest

import bcc.features.plugins as P
from bcc.tools import REGISTRY


@pytest.fixture(autouse=True)
async def registered():
    await P.setup(None)
    yield


def _ctx():
    return type("C", (), {"svc": None, "task": {}, "run_id": 1, "agent": {},
                          "workspace": "", "call_id": "c", "step": 0})()


@pytest.mark.parametrize("path", ["", "sub", "sub/", "."])
async def test_obsidian_read_directory_is_refused_not_raised(tmp_path, monkeypatch, path):
    vault = tmp_path / "vault"
    (vault / "sub").mkdir(parents=True)
    (vault / "a.md").write_text("hello", "utf-8")
    monkeypatch.setenv("OBSIDIAN_VAULT", str(vault))
    res = await REGISTRY.get("plugin:obsidian.read").handler({"path": path}, _ctx())
    assert res.error
    assert "obsidian.read" in res.one_line
    assert str(tmp_path) not in res.content          # абсолютный путь не утекает


async def test_obsidian_read_file_still_works(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text("hello", "utf-8")
    monkeypatch.setenv("OBSIDIAN_VAULT", str(vault))
    res = await REGISTRY.get("plugin:obsidian.read").handler({"path": "a.md"}, _ctx())
    assert not res.error and res.content == "hello"
