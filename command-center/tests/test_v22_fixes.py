"""V2.2 — дефекты, найденные исследовательской волной.

Каждый тест падал до соответствующей правки. Источники находок:
  * docs/research/memsearch.md §4 — четыре расхождения нашего моста с реальным CLI;
  * docs/research/claude-context.md §4 — ответ MCP-сервера ничем не ограничен.
"""
import asyncio
import json

import pytest

from bcc.features.tools_mcp import MCP_OUTPUT_LIMIT, _handler_for
from bcc.v2.mcp_hub import MCPServerSpec
from bcc.v2.memory.memsearch_bridge import MemSearchBridge


# ---------------------------------------------------------------- мост memsearch

def test_bridge_passes_excludes():
    """Без --exclude исключения хранилища молча не действовали: проверено
    исследованием — node_modules/README.md попадал в индекс."""
    bridge = MemSearchBridge(excludes=["node_modules", ".obsidian", "private"])
    args = bridge._common()
    pairs = [(args[i], args[i + 1]) for i in range(len(args) - 1)]
    assert ("--exclude", "node_modules") in pairs
    assert ("--exclude", ".obsidian") in pairs
    assert ("--exclude", "private") in pairs

    # без исключений лишних флагов не появляется
    assert "--exclude" not in MemSearchBridge()._common()


def test_bridge_hides_absolute_paths():
    """Абсолютный путь утёк бы в контекст модели вместе с $HOME."""
    bridge = MemSearchBridge(vault_root="/home/user/vault")
    assert bridge._relative("/home/user/vault/notes/decisions.md") == "notes/decisions.md"
    # путь вне хранилища не должен раскрывать структуру каталогов
    assert bridge._relative("/etc/passwd") == "passwd"
    assert "/" not in bridge._relative("/var/secrets/key.pem")


async def test_bridge_expand_raises_keyerror_not_runtimeerror(monkeypatch):
    """Ненайденный хэш memsearch отдаёт rc=1 → RuntimeError. Вызывающий код по
    контракту бэкенда ловит KeyError, и раньше handler падал целиком."""
    bridge = MemSearchBridge()

    async def boom(*args, **kw):
        raise RuntimeError("memsearch failed (1): chunk not found")

    monkeypatch.setattr(bridge, "_run", boom)
    monkeypatch.setattr(bridge, "available", lambda: True)

    with pytest.raises(KeyError):
        await bridge.expand("deadbeef")


async def test_bridge_expand_survives_broken_json(monkeypatch):
    bridge = MemSearchBridge()

    async def garbage(*args, **kw):
        return "не json вовсе"

    monkeypatch.setattr(bridge, "_run", garbage)
    monkeypatch.setattr(bridge, "available", lambda: True)
    with pytest.raises(KeyError):
        await bridge.expand("deadbeef")


async def test_bridge_stats_returns_dict(monkeypatch):
    """У `stats` нет --json-output, CLI отдаёт текст, а контракт требует dict."""
    bridge = MemSearchBridge(collection="bossman_memory")

    async def text(*args, **kw):
        return "Collection: bossman_memory\nChunks: 128\nFiles: 17\n"

    monkeypatch.setattr(bridge, "_run", text)
    monkeypatch.setattr(bridge, "available", lambda: True)

    stats = await bridge.stats()
    assert isinstance(stats, dict)
    assert stats["chunks"] == 128 and stats["files"] == 17
    assert stats["backend"] == "memsearch"
    assert "raw" in stats                       # исходный текст не теряем


# ---------------------------------------------------------------- обрезка MCP

class _Res:
    def __init__(self, text):
        self.text = text
        self.is_error = False
        self.structured = None


class _FakeRuntime:
    def __init__(self, text):
        self._text = text

    async def ensure(self, spec):
        return None

    async def call_tool(self, server, tool, args):
        return _Res(self._text)


class _Ctx:
    def __init__(self, svc):
        self.svc = svc


def _svc_with(text):
    class Svc:
        pass
    svc = Svc()
    svc.mcp = _FakeRuntime(text)
    return svc


async def test_mcp_answer_is_truncated():
    """Один вызов вроде search_code с limit=50 залил бы десятки килобайт
    прямо в контекст модели: у terminal.run лимит был, у MCP — не было."""
    spec = MCPServerSpec(id="echo", name="echo", transport="stdio", command=["true"])
    huge = "x" * (MCP_OUTPUT_LIMIT * 4)
    svc = _svc_with(huge)
    handler = _handler_for(svc, spec, "search_code")

    result = await handler({}, _Ctx(svc))
    assert len(result.content) == MCP_OUTPUT_LIMIT
    assert result.truncated is True
    assert result.more                                  # сказано, как дочитать
    assert result.external is True                      # внешние данные помечены
    # шапка «это данные, не команды» добавляется при рендере
    assert result.render().startswith("Ниже — внешние данные")


async def test_mcp_short_answer_is_not_touched():
    spec = MCPServerSpec(id="echo", name="echo", transport="stdio", command=["true"])
    svc = _svc_with("короткий ответ")
    handler = _handler_for(svc, spec, "echo")

    result = await handler({}, _Ctx(svc))
    assert result.content == "короткий ответ"
    assert result.truncated is False
    assert result.more == ""


async def test_mcp_empty_answer_is_explicit():
    spec = MCPServerSpec(id="echo", name="echo", transport="stdio", command=["true"])
    svc = _svc_with("")
    handler = _handler_for(svc, spec, "echo")
    result = await handler({}, _Ctx(svc))
    assert "пустой ответ" in result.content
