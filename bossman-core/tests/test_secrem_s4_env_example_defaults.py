"""S4 (P1) — .env.example не должен раздавать небезопасную пару по умолчанию.

REPRO: пример вёз `SANDBOX_MODE=local` ВМЕСТЕ с `BOSSMAN_UNSAFE_LOCAL_EXEC=1` —
ровно ту пару, которая исполняет команды модели прямо на хосте без изоляции.
Код по умолчанию корректен (config.py: sandbox_mode="docker", флаг пустой),
но документированный путь запуска — `cp .env.example .env`, то есть каждый,
кто прошёл по инструкции, получал хостовый exec.
"""
from __future__ import annotations

import re
from pathlib import Path

from bossman.config import Settings

EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"


def _assignments(text: str) -> dict[str, str]:
    """Только РАСКОММЕНТИРОВАННЫЕ присваивания: закомментированная строка —
    это подсказка владельцу, а не значение, которое приедет в .env."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Z0-9_]+)\s*=\s*(.*?)\s*(?:#.*)?$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def test_env_example_exists():
    assert EXAMPLE.is_file()


def test_env_example_does_not_ship_unisolated_host_exec():
    """REPRO S4: SANDBOX_MODE=local + BOSSMAN_UNSAFE_LOCAL_EXEC=1."""
    env = _assignments(EXAMPLE.read_text(encoding="utf-8"))
    assert env.get("SANDBOX_MODE") == "docker", \
        "пример раздаёт песочницу без изоляции"
    assert env.get("BOSSMAN_UNSAFE_LOCAL_EXEC", "").lower() not in ("1", "true", "yes"), \
        "пример включает хостовый exec без изоляции"


def test_env_example_matches_the_safe_code_default():
    """Пример и код должны сходиться: docker + выключенный unsafe-флаг."""
    env = _assignments(EXAMPLE.read_text(encoding="utf-8"))
    assert env.get("SANDBOX_MODE") == Settings().sandbox_mode == "docker"


def test_unsafe_flag_is_only_mentioned_as_a_commented_hint():
    """Флаг может присутствовать как подсказка — но только закомментированным."""
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        if "BOSSMAN_UNSAFE_LOCAL_EXEC" in line and not line.strip().startswith("#"):
            assert line.split("=", 1)[1].split("#")[0].strip() == "", line
