from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from typing import Literal, Any

Transport = Literal["stdio", "http"]

_CYRILLIC_TO_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E", "Ё": "Yo",
    "Ж": "Zh", "З": "Z", "И": "I", "Й": "Y", "К": "K", "Л": "L", "М": "M",
    "Н": "N", "О": "O", "П": "P", "Р": "R", "С": "S", "Т": "T", "У": "U",
    "Ф": "F", "Х": "Kh", "Ц": "Ts", "Ч": "Ch", "Ш": "Sh", "Щ": "Shch",
    "Ъ": "", "Ы": "Y", "Ь": "", "Э": "E", "Ю": "Yu", "Я": "Ya",
}

def transliterate_cyrillic(value: str) -> str:
    return "".join(_CYRILLIC_TO_LATIN.get(ch, ch) for ch in value)

def normalize_name(value: str) -> str:
    transliterated = transliterate_cyrillic(str(value or ""))
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", transliterated).strip("_") or "tool"

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
        errors.extend(_manifest_errors(self))
        return errors


# ----------------------------------------------------------- манифест сервера
# Границы манифеста. Новые ошибки — сразу по-русски (их показывают владельцу);
# три старых английских кода выше переводит `features/skills._MCP_RU`.
NAME_MAX = 80
TIMEOUT_MIN_S = 1
TIMEOUT_MAX_S = 600
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_NAME_BAD_RE = re.compile(r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2060-\u2064\ufeff]")


def _url_error(url: str) -> str:
    from urllib.parse import urlsplit
    raw = str(url or "")
    if any(c.isspace() for c in raw) or _NAME_BAD_RE.search(raw):
        return "адрес MCP-сервера содержит пробелы или управляющие символы"
    try:
        parts = urlsplit(raw)
        _ = parts.port
    except ValueError:
        return "адрес MCP-сервера не разбирается как URL"
    if parts.scheme not in ("http", "https"):
        return (f"адрес MCP-сервера: разрешены только http/https, "
                f"а не {parts.scheme or 'пусто'!r} (file://, ftp:// и т.п. запрещены)")
    if not parts.hostname:
        return "в адресе MCP-сервера нет хоста"
    if parts.username or parts.password:
        return ("адрес MCP-сервера с логином/паролем запрещён: секрет подставляется "
                "из vault шаблоном {secret:ИМЯ}, а не хранится в адресе")
    return ""


def _manifest_errors(spec: "MCPServerSpec") -> list[str]:
    errors: list[str] = []
    for value in {str(spec.id), str(spec.name)}:
        if not value.strip():
            errors.append("имя MCP-сервера пустое")
        elif len(value) > NAME_MAX:
            errors.append(f"имя MCP-сервера длиннее {NAME_MAX} символов")
        elif _NAME_BAD_RE.search(value):
            errors.append("имя MCP-сервера содержит управляющие/невидимые символы")
    if spec.transport == "http" and spec.url:
        why = _url_error(spec.url)
        if why:
            errors.append(why)
    t = spec.timeout_seconds
    if isinstance(t, bool) or not isinstance(t, (int, float)) or not (TIMEOUT_MIN_S <= t <= TIMEOUT_MAX_S):
        errors.append(f"таймаут MCP-сервера должен быть от {TIMEOUT_MIN_S} до {TIMEOUT_MAX_S} с, "
                      f"получено {t!r}")
    bad_keys = [str(k) for k in (spec.env_keys or []) if not _ENV_KEY_RE.match(str(k))]
    if bad_keys:
        errors.append("недопустимые имена переменных окружения: " + ", ".join(bad_keys[:5]))
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
NODE24_ENV = "BOSSMAN_NODE24"                  # абсолютный путь к node.exe отдельного Node 24 (runtime/node24)
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
        # Отдельный Node 24 для MCP-серверов, которым мало системного Node 20: только явный АБСОЛЮТНЫЙ путь,
        # добавляется к встроенному списку, не заменяет его.
        node24 = os.environ.get(NODE24_ENV, "").strip()
        if node24 and os.path.isabs(node24):
            names.append(node24)
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
        if item in flags or any(
            item.startswith(f + "=") or (f in ("-c", "-e") and item.startswith(f) and len(item) > 2 and not item.startswith("--"))
            for f in flags
        ):
            return (f"флаг {item!r} выполняет код из аргумента — allowlist разрешает "
                    f"бинарник, а не произвольный код внутри него.") + tail
    return ""


# Important:
# Protocol execution must use the official MCP SDK in the integration layer.
# Do not hand-roll JSON-RPC framing here. This file intentionally defines the
# canonical BOSSMAN naming/config contract independent of transport SDK details.
