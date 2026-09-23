"""Slash commands of `bossman chat` — parsing only (pure, unit-tested).

A line that starts with "/" is a command; anything else is a message to
Bossman. "//text" sends "/text" literally. Unknown commands are reported, never
sent to the model by accident.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass, field

#: name -> (usage, one-line help). Order = order in /help.
COMMANDS: dict[str, tuple[str, str]] = {
    "help": ("/help", "список команд"),
    "status": ("/status", "подключение, сборка, модель, агент, режим, бюджеты"),
    "tasks": ("/tasks [N]", "последние задачи Bossman"),
    "models": ("/models [use <id|alias>]", "модели; use — сменить модель текущего агента"),
    "agent": ("/agent [<id|имя>]", "агенты; с аргументом — выбрать"),
    "skills": ("/skills [запрос]", "навыки; с запросом — какие подошли бы к задаче"),
    "tools": ("/tools", "инструменты текущего агента и их политика"),
    "memory": ("/memory <запрос>", "поиск по памяти и фактам"),
    "diff": ("/diff [coding-task-id]", "diff последней coding-задачи"),
    "code": ("/code --allow <путь> [--verify <тест>] <задача>", "coding task через coding path"),
    "approve": ("/approve [id]", "одобрить ожидающее разрешение"),
    "deny": ("/deny [id]", "отклонить ожидающее разрешение"),
    "approvals": ("/approvals", "ожидающие разрешения"),
    "pause": ("/pause [task]", "пауза задачи"),
    "stop": ("/stop [task|all]", "остановить задачу; all — глобальный STOP"),
    "resume": ("/resume [task]", "продолжить задачу после паузы"),
    "computer": ("/computer [status|stop|resume]", "управление компьютером"),
    "evolve": ("/evolve [status|pause|resume|stop|report]", "цикл самоулучшения 1.1"),
    "keys": ("/keys [set <vendor>|remove <vendor>|import-env]", "ключи облачных моделей"),
    "panel": ("/panel", "панель контекста: workspace, задача, инструменты, память, бюджет"),
    "expand": ("/expand [N]", "развернуть свёрнутый блок (мысли модели, вывод инструмента)"),
    "history": ("/history [on|off]", "история ввода: показать состояние / включить / выключить"),
    "clear": ("/clear", "очистить экран"),
    "exit": ("/exit", "выйти (задачи продолжают работу в Bossman)"),
}
ALIASES = {"quit": "exit", "q": "exit", "model": "models", "evolution": "evolve", "?": "help",
           "agents": "agent", "approval": "approvals"}


@dataclass
class Parsed:
    kind: str                    # "message" | "command" | "empty" | "unknown"
    name: str = ""
    args: list[str] = field(default_factory=list)
    text: str = ""               # the message, or the raw argument string
    error: str = ""


def parse(line: str) -> Parsed:
    raw = line.rstrip("\r\n")
    if not raw.strip():
        return Parsed("empty")
    stripped = raw.lstrip()
    if stripped.startswith("//"):
        return Parsed("message", text=stripped[1:])
    if not stripped.startswith("/"):
        return Parsed("message", text=raw)
    body = stripped[1:]
    name, _, rest = body.partition(" ")
    name = name.strip().lower()
    name = ALIASES.get(name, name)
    if name not in COMMANDS:
        return Parsed("unknown", name=name, text=rest.strip(),
                      error=f"неизвестная команда /{name}; /help — список")
    try:
        # Backslashes are Windows path separators here, not escapes:
        # `/code --allow src\calc.py` must keep `src\calc.py`.
        args = shlex.split(rest.replace("\\", "\\\\"), posix=True) if rest.strip() else []
    except ValueError:
        args = rest.split()
    return Parsed("command", name=name, args=args, text=rest.strip())


def completions() -> list[str]:
    return ["/" + n for n in COMMANDS]
