"""V2.6 аудит, D1 — load_sdk() импортирует РЕАЛЬНЫЕ пути официального SDK.

История дефекта: рантайм импортировал `mcp.client.client.Client` — путь,
которого в 1.x SDK нет; `sdk_available()` глотал ImportError, и MCP вечно
отчитывался «SDK не установлен» при установленном пакете `mcp`.

Проверяем без сети и без реальных процессов:
1) минимальный ФЕЙКОВЫЙ пакет `mcp` с настоящей структурой SDK
   (`mcp.client.session.ClientSession`, `mcp.client.stdio.stdio_client`,
   `StdioServerParameters`) и БЕЗ `mcp.client.client` → sdk_available() is True.
   Это доказывает, что рантайм ходит по реальным путям, а не по выдуманным.
2) отравленный sys.modules (import mcp → ImportError) → sdk_available() False:
   честный degrade сохранён.
"""
from __future__ import annotations

import importlib.util
import sys
import types

import pytest

from bcc.v2 import mcp_runtime


# --------------------------------------------------------------- обвязка

def _purge_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    """Убрать настоящий (или прежний фейковый) пакет mcp из кэша импорта."""
    for name in list(sys.modules):
        if name == "mcp" or name.startswith("mcp."):
            monkeypatch.delitem(sys.modules, name, raising=False)


def _install_fake_sdk(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Минимальный фейковый пакет mcp с РЕАЛЬНОЙ структурой официального SDK.

    Намеренно НЕ содержит `mcp.client.client` — фантазийного пути, из-за
    которого возник дефект: старый load_sdk() на этом фейке упал бы.
    """
    _purge_mcp(monkeypatch)

    class ClientSession:                       # noqa: D401 — маркер, не реализация
        def __init__(self, read, write): ...

    def stdio_client(params): ...

    class StdioServerParameters:
        def __init__(self, **kw): ...

    mcp_pkg = types.ModuleType("mcp")
    mcp_pkg.__path__ = []                      # ведёт себя как пакет
    mcp_pkg.StdioServerParameters = StdioServerParameters

    client_pkg = types.ModuleType("mcp.client")
    client_pkg.__path__ = []

    session_mod = types.ModuleType("mcp.client.session")
    session_mod.ClientSession = ClientSession

    stdio_mod = types.ModuleType("mcp.client.stdio")
    stdio_mod.stdio_client = stdio_client
    stdio_mod.StdioServerParameters = StdioServerParameters

    mcp_pkg.client = client_pkg
    client_pkg.session = session_mod
    client_pkg.stdio = stdio_mod

    for name, mod in {"mcp": mcp_pkg, "mcp.client": client_pkg,
                      "mcp.client.session": session_mod,
                      "mcp.client.stdio": stdio_mod}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return {"ClientSession": ClientSession, "stdio_client": stdio_client,
            "StdioServerParameters": StdioServerParameters}


# --------------------------------------------------------------- 1. реальные пути

def test_sdk_available_with_real_sdk_structure_only(monkeypatch):
    """Фейк повторяет ТОЛЬКО реальную структуру SDK — и этого достаточно."""
    fake = _install_fake_sdk(monkeypatch)
    # фантазийного пути в фейке нет — старый импорт здесь бы упал
    assert "mcp.client.client" not in sys.modules
    assert mcp_runtime.sdk_available() is True

    ClientSession, stdio_client, Params = mcp_runtime.load_sdk()
    assert ClientSession is fake["ClientSession"]
    assert stdio_client is fake["stdio_client"]
    assert Params is fake["StdioServerParameters"]


def test_sdk_available_with_installed_package():
    """Если настоящий mcp установлен в окружении — рантайм обязан его видеть."""
    if importlib.util.find_spec("mcp") is None:
        pytest.skip("пакет mcp не установлен в этом окружении")
    assert mcp_runtime.sdk_available() is True
    trio = mcp_runtime.load_sdk()
    assert len(trio) == 3 and all(trio)


# --------------------------------------------------------------- 2. честный degrade

def test_sdk_absent_degrades_honestly(monkeypatch):
    """import mcp → ImportError ⇒ sdk_available() False, load_sdk() бросает."""
    _purge_mcp(monkeypatch)
    # None в sys.modules заставляет import mcp бросить ImportError
    monkeypatch.setitem(sys.modules, "mcp", None)
    assert mcp_runtime.sdk_available() is False
    with pytest.raises(ImportError):
        mcp_runtime.load_sdk()
