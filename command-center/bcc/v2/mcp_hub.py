from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from typing import Literal, Any

Transport = Literal["stdio", "http"]

def normalize_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_") or "tool"

def namespaced_tool(server_id: str, tool_name: str) -> str:
    return f"mcp:{normalize_name(server_id)}:{normalize_name(tool_name)}"

@dataclass(slots=True)
class MCPServerSpec:
    id: str
    name: str
    transport: Transport
    command: list[str] = field(default_factory=list)
    url: str = ""
    cwd: str = ""
    env_keys: list[str] = field(default_factory=list)
    enabled: bool = True
    timeout_seconds: int = 30

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.transport == "stdio" and not self.command:
            errors.append("stdio MCP requires command")
        if self.transport == "http" and not self.url:
            errors.append("http MCP requires url")
        if self.transport not in ("stdio", "http"):
            errors.append("invalid MCP transport")
        return errors

@dataclass(slots=True)
class MCPToolView:
    server_id: str
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)

    @property
    def bossman_name(self) -> str:
        return namespaced_tool(self.server_id, self.name)

# ----------------------------------------------------------- F-014: команда запуска
#
# Модель угрозы: владелец (или тот, кто получил его сессию) по ошибке
# регистрирует MCP-сервер с произвольной командой запуска — `bash -c "curl … | sh"`.
# Транспорт stdio ЗАПУСКАЕТ ПРОЦЕСС, поэтому argv — это граница исполнения кода,
# и она защищается allowlist'ом бинарников, а не доверием к вводу.
#
#   * встроенный allowlist: текущий интерпретатор (sys.executable) и известные
#     рантаймы MCP-серверов (python/node/npx/uvx), найденные на PATH;
#   * BCC_MCP_COMMAND_ALLOWLIST (os.pathsep-разделённые абсолютные пути или
#     имена, которые ДОЛЖНЫ резолвиться на PATH) — ЗАМЕНЯЕТ встроенный;
#   * сравнение по realpath: симлинк на разрешённый бинарник — тот же бинарник,
#     симлинк на чужой — чужой;
#   * оболочки и метасимволы оболочки отклоняются всегда: argv передаётся без
#     шелла, но `bash -c` превращает любой элемент обратно в шелл-строку.

COMMAND_ALLOWLIST_ENV = "BCC_MCP_COMMAND_ALLOWLIST"
DEFAULT_COMMAND_BINARIES: tuple[str, ...] = ("python3", "python", "node", "npx", "uvx", "uv")
_SHELL_NAMES = frozenset({"sh", "bash", "dash", "zsh", "ksh", "csh", "tcsh", "fish", "ash",
                          "busybox", "cmd", "cmd.exe", "powershell", "powershell.exe",
                          "pwsh", "pwsh.exe"})
# Метасимволы оболочки: без шелла они безвредны, но их присутствие означает, что
# строку либо вставили из шелла, либо рассчитывают на интерпретацию где-то дальше.
_SHELL_META = frozenset(";|&$`<>(){}\n\r\x00")
# Флаги «выполнить код из аргумента»: allowlist разрешает БИНАРНИК, а не
# произвольный код внутри него. `npx -c` — это ещё и настоящий шелл.
_INLINE_CODE_FLAGS: dict[str, frozenset[str]] = {
    "python": frozenset({"-c"}),
    "node": frozenset({"-e", "--eval", "-p", "--print"}),
    "npx": frozenset({"-c", "--call"}),
}


def _resolve_binary(name: str) -> str | None:
    """Абсолютный realpath исполняемого файла или None.

    Принимаются только абсолютные пути и «голые» имена (через PATH):
    относительный путь зависит от cwd процесса и не поддаётся allowlist'у."""
    if not name:
        return None
    if os.path.isabs(name):
        path: str | None = name
    elif os.sep in name or (os.altsep and os.altsep in name):
        return None
    else:
        path = shutil.which(name)
    if not path or not os.path.isfile(path) or not os.access(path, os.X_OK):
        return None
    return os.path.realpath(path)


def command_allowlist() -> tuple[set[str], str]:
    """(множество realpath'ов разрешённых бинарников, происхождение списка)."""
    raw = os.environ.get(COMMAND_ALLOWLIST_ENV, "")
    if raw.strip():
        names = [x.strip() for x in raw.split(os.pathsep) if x.strip()]
        origin = COMMAND_ALLOWLIST_ENV
    else:
        names = [sys.executable, *DEFAULT_COMMAND_BINARIES]
        origin = "встроенный"
    resolved: set[str] = set()
    for n in names:
        r = _resolve_binary(n)
        if r:
            resolved.add(r)
    return resolved, origin


def _interpreter_key(binary_realpath: str, argv0: str) -> str:
    """python3.11 → python, node22 → node: ключ для таблицы inline-флагов."""
    for cand in (os.path.basename(argv0), os.path.basename(binary_realpath)):
        base = cand.lower()
        if re.fullmatch(r"python\d*(\.\d+)?(\.exe)?", base):
            return "python"
        for key in ("node", "npx"):
            if base == key or base.startswith(key + ".") or base.startswith(key + "-"):
                return key
    return ""


def command_policy_refusal(argv: object) -> str:
    """Причина отказа запускать `argv` как stdio-MCP-сервер; "" = разрешено.

    Порядок проверок — от формы к существу: тип/пустота → метасимволы → оболочка
    → бинарник в allowlist → inline-код. Первое нарушение и есть ответ.
    """
    allow, origin = command_allowlist()
    tail = (f" Разрешены только бинарники из allowlist ({origin}: "
            f"{', '.join(sorted(allow)) or 'пусто'}).")
    if not isinstance(argv, (list, tuple)) or not argv:
        return "команда запуска MCP пуста или не является списком argv." + tail
    for i, item in enumerate(argv):
        if not isinstance(item, str):
            return (f"элемент argv[{i}] не строка ({type(item).__name__}) — "
                    f"аргументы MCP должны быть строками." + tail)
        bad = sorted({ch for ch in item if ch in _SHELL_META})
        if bad:
            return (f"argv[{i}] содержит метасимволы оболочки {''.join(bad)!r}: "
                    f"команда запуска передаётся без шелла, интерпретация запрещена." + tail)
    argv0 = argv[0].strip()
    if not argv0:
        return "argv[0] пуст." + tail
    base = os.path.basename(argv0).lower()
    if base in _SHELL_NAMES:
        return (f"оболочка {base!r} как команда запуска MCP запрещена "
                f"(например, bash -c …): allowlist разрешает конкретные бинарники.") + tail
    binary = _resolve_binary(argv0)
    if binary is None:
        return (f"бинарник {argv0!r} не найден как абсолютный путь или на PATH "
                f"(относительные пути не принимаются).") + tail
    if binary not in allow:
        return f"бинарник {argv0!r} → {binary} не входит в allowlist." + tail
    flags = _INLINE_CODE_FLAGS.get(_interpreter_key(binary, argv0), frozenset())
    for item in argv[1:]:
        if item in flags:
            return (f"флаг {item!r} выполняет код из аргумента — allowlist разрешает "
                    f"бинарник, а не произвольный код внутри него.") + tail
    return ""


# Important:
# Protocol execution must use the official MCP SDK in the integration layer.
# Do not hand-roll JSON-RPC framing here. This file intentionally defines the
# canonical BOSSMAN naming/config contract independent of transport SDK details.
