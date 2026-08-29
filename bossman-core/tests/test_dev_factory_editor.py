"""GatewayEditor — минимальный безопасный адаптер правки через существующий шов llm.chat.

Границы: пишет ТОЛЬКО в рабочую копию; пути вне неё/с «..»/симлинки отвергаются
ДО записи; потолки по числу файлов и объёму; сбой модели/битый JSON → ошибка
домена (потраченная попытка), а не тихий «успех» и не частичная запись.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bossman import errors
from bossman.dev_factory.editor import GatewayEditor, MAX_FILES
from bossman.dev_factory.models import DevStep, StepKind, new_id


@pytest.fixture
def anyio_backend():
    return "asyncio"


pytestmark = pytest.mark.anyio


class _Job:
    def __init__(self, ws): self.workspace = str(ws); self.task = "t"; self.id = "dj1"


def _step():
    return DevStep(id=new_id("st"), kind=StepKind.EDIT, description="fix")


def _chat_returning(files):
    async def chat(agent, messages, **kw):
        return {"content": json.dumps({"files": files})}
    return chat


async def test_writes_only_inside_workspace(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "a.py").write_text("old", encoding="utf-8")
    ed = GatewayEditor(agent=None, chat=_chat_returning([{"path": "a.py", "content": "new"}]))
    await ed(_Job(ws), _step())
    assert (ws / "a.py").read_text() == "new"


@pytest.mark.parametrize("evil", [
    "/etc/passwd", "../escape.py", "../../x", "sub/../../y", ".git/hooks/pre-commit",
])
async def test_path_escape_rejected_before_write(tmp_path, evil):
    ws = tmp_path / "ws"; ws.mkdir()
    marker = tmp_path / "escape.py"
    ed = GatewayEditor(agent=None, chat=_chat_returning([{"path": evil, "content": "PWN"}]))
    with pytest.raises(errors.PolicyDenied):
        await ed(_Job(ws), _step())
    assert not marker.exists()


async def test_partial_write_does_not_happen_on_mixed_batch(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    ed = GatewayEditor(agent=None, chat=_chat_returning([
        {"path": "good.py", "content": "ok"},
        {"path": "../evil.py", "content": "bad"},
    ]))
    with pytest.raises(errors.PolicyDenied):
        await ed(_Job(ws), _step())
    # хороший файл НЕ записан: проверка путей проходит до любой записи
    assert not (ws / "good.py").exists()


async def test_model_failure_raises_not_silent(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()

    async def boom(agent, messages, **kw):
        raise RuntimeError("gateway down")

    ed = GatewayEditor(agent=None, chat=boom)
    with pytest.raises(errors.ModelUnavailable):
        await ed(_Job(ws), _step())


async def test_broken_json_raises(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()

    async def bad(agent, messages, **kw):
        return {"content": "not json at all"}

    ed = GatewayEditor(agent=None, chat=bad)
    with pytest.raises(errors.BossmanError):
        await ed(_Job(ws), _step())


async def test_no_workspace_refuses(tmp_path):
    ed = GatewayEditor(agent=None, chat=_chat_returning([{"path": "a", "content": "b"}]))
    job = _Job(tmp_path / "does-not-exist")
    with pytest.raises(errors.PolicyDenied):
        await ed(job, _step())


async def test_too_many_files_capped(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    files = [{"path": f"f{i}.py", "content": "x"} for i in range(MAX_FILES + 20)]
    ed = GatewayEditor(agent=None, chat=_chat_returning(files))
    await ed(_Job(ws), _step())
    written = list(ws.glob("f*.py"))
    assert len(written) <= MAX_FILES
