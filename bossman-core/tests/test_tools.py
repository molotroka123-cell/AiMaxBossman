"""Приёмка 11: ни один инструмент не возвращает результат выше лимита из 10.4;
truncated и способ дочитать присутствуют."""
import pytest

from bossman.context import estimate_tokens
from bossman.toolkit import REGISTRY, ToolContext
from bossman.toolkit.files import fs_list, fs_read, fs_search


@pytest.fixture
def ctx(tmp_path):
    return ToolContext(agent="test", workdir=tmp_path)


async def test_fs_read_limits_to_200_lines(ctx):
    (ctx.workdir / "big.txt").write_text("\n".join(f"строка {i}" for i in range(1, 501)),
                                         encoding="utf-8")
    res = await fs_read({"path": "big.txt"}, ctx)
    assert res.truncated and "fs.read" in res.more       # как дочитать — указано
    assert len(res.content.splitlines()) <= 200
    assert estimate_tokens(res.content) <= 4000


async def test_fs_read_range_continues(ctx):
    (ctx.workdir / "big.txt").write_text("\n".join(f"строка {i}" for i in range(1, 501)),
                                         encoding="utf-8")
    res = await fs_read({"path": "big.txt", "from": 201, "to": 400}, ctx)
    assert res.content.splitlines()[0].startswith("201")


async def test_fs_read_refuses_escape(ctx):
    with pytest.raises(PermissionError):
        await fs_read({"path": "../../etc/passwd"}, ctx)


async def test_fs_search_limits_to_50_hits(ctx):
    (ctx.workdir / "data.txt").write_text("\n".join("иголка тут" for _ in range(200)),
                                          encoding="utf-8")
    res = await fs_search({"pattern": "иголка"}, ctx)
    assert len(res.content.splitlines()) == 50
    assert res.truncated and "offset=50" in res.more


async def test_fs_list_limits_to_100_entries(ctx):
    for i in range(150):
        (ctx.workdir / f"f{i:03}.txt").write_text("x")
    res = await fs_list({"path": "."}, ctx)
    assert len(res.content.splitlines()) == 100
    assert res.truncated and "offset=100" in res.more


def test_every_tool_declares_limit_and_rights():
    assert REGISTRY, "реестр инструментов пуст"
    for name, tool in REGISTRY.items():
        assert tool.rights in ("read", "write", "exec", "send"), name
        assert tool.token_limit > 0, name
        assert tool.description, name


def test_irreversible_tools_require_confirmation():
    assert REGISTRY["gmail.send"].confirm_default
    assert REGISTRY["crm.write"].confirm_default
