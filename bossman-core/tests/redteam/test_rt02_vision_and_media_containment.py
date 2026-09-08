"""RT-02/RT-03: путь агента не должен превращаться в чтение файла вне рабочей папки.

RT-02 — `vision.describe` отдавал СЫРОЙ путь в `vision_caption`, который делал
`read_bytes()`. Любой абсолютный путь хоста (или symlink изнутри наружу)
становился base64 внутри запроса к модели: канал вывода данных, а не подпись.

RT-03 — `media._path_arg_ok` лексическая: ловит «..» и абсолютные пути, но не
symlink. `leak.mp4 -> /outside/secret` проходил её и доезжал до ffprobe.

Байты до модели здесь не доходят: адаптер подменён и лишь фиксирует, что ему
передали. Файлы-приманки синтетические, во временном каталоге.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bossman.toolkit import ToolContext
from bossman.toolkit.media import VISION_MAX_BYTES, probe, vision_describe

pytestmark = pytest.mark.asyncio

CANARY = "CANARY-EXFIL-DO-NOT-SEND"


@pytest.fixture()
def sent(monkeypatch):
    """Граница модели: записывает то, что ей передали, и ничего не отправляет."""
    seen: list[tuple[str, bytes]] = []

    async def _capture(agent_name, question, *, data: bytes, source: str = ""):
        seen.append((source, bytes(data)))
        return "caption"

    import bossman.llm as llm
    monkeypatch.setattr(llm, "vision_caption", _capture)
    return seen


@pytest.fixture()
def workspace(tmp_path: Path):
    workdir = tmp_path / "workspace" / "coder"
    workdir.mkdir(parents=True)
    secret = tmp_path / "canary_secret.txt"
    secret.write_text(CANARY + "\n", encoding="utf-8")
    frame = workdir / "frame.png"
    frame.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    return workdir, secret, frame, ToolContext(agent="coder", workdir=workdir, run_id=1)


async def test_absolute_outside_path_never_reaches_the_model(workspace, sent):
    workdir, secret, _, ctx = workspace
    result = await vision_describe({"path": str(secret), "question": "q"}, ctx)
    assert result.error
    assert sent == [], "байты вне рабочей папки дошли до модели"


async def test_parent_traversal_never_reaches_the_model(workspace, sent):
    _, _, _, ctx = workspace
    assert (await vision_describe({"path": "../../canary_secret.txt"}, ctx)).error
    assert sent == []


async def test_symlink_inside_workdir_pointing_outside_is_refused(workspace, sent):
    workdir, secret, _, ctx = workspace
    (workdir / "leak.png").symlink_to(secret)
    result = await vision_describe({"path": "leak.png", "question": "q"}, ctx)
    assert result.error, "symlink наружу отдал содержимое модели"
    assert sent == []
    assert all(CANARY.encode() not in payload for _, payload in sent)


async def test_prefix_sibling_directory_is_not_inside(tmp_path: Path, sent):
    """`.../coder-secrets` не «внутри» `.../coder`: сравнение отношением путей."""
    workdir = tmp_path / "coder"
    workdir.mkdir()
    sibling = tmp_path / "coder-secrets"
    sibling.mkdir()
    (sibling / "s.png").write_text(CANARY, encoding="utf-8")
    ctx = ToolContext(agent="coder", workdir=workdir, run_id=1)
    assert (await vision_describe({"path": "../coder-secrets/s.png"}, ctx)).error
    assert sent == []


async def test_an_ordinary_frame_inside_the_workdir_still_works(workspace, sent):
    """Положительный контроль: containment не должен убить сам инструмент."""
    _, _, frame, ctx = workspace
    result = await vision_describe({"path": "frame.png", "question": "q"}, ctx)
    assert not result.error, result.content
    assert len(sent) == 1
    source, payload = sent[0]
    assert payload == frame.read_bytes()
    assert source == "frame.png", "в запрос уехал абсолютный путь хоста"


async def test_an_oversized_frame_is_refused_before_reading_out(workspace, sent, monkeypatch):
    workdir, _, _, ctx = workspace
    big = workdir / "big.png"
    big.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 1024)
    monkeypatch.setattr("bossman.toolkit.media.VISION_MAX_BYTES", 16)
    assert (await vision_describe({"path": "big.png"}, ctx)).error
    assert sent == []


async def test_probe_refuses_a_symlink_that_leaves_the_workdir(workspace):
    """RT-03: лексический барьер пропускал `leak.mp4`; содержание — нет."""
    workdir, secret, _, ctx = workspace
    (workdir / "leak.mp4").symlink_to(secret)
    result = await probe({"path": "leak.mp4"}, ctx)
    assert result.error
    assert "рабочей папки" in result.content


async def test_probe_still_refuses_absolute_and_parent_paths(workspace):
    _, secret, _, ctx = workspace
    assert (await probe({"path": str(secret)}, ctx)).error
    assert (await probe({"path": "../../canary_secret.txt"}, ctx)).error
