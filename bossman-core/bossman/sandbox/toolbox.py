"""Stage 8 — инструменты САМОЙ песочницы (shell / git / files).

Это не инструменты агента снаружи (те живут в `sandbox/tools.py`), а операции
ВНУТРИ уже созданной песочницы, ограниченные её рабочей областью.

Границы, которые нельзя обойти:
- всё исполняется рантаймом песочницы, а не на хосте: те же uid, rlimits,
  сетевой барьер и одноразовая копия;
- команда — ТОЛЬКО массив argv; строка отвергается (иначе shell-инъекция);
- git ограничен безопасным подмножеством: НИКАКИХ push/remote/config —
  публикация остаётся действием владельца (non-negotiable #8);
- файловые операции не выходят за рабочую область (та же проверка containment,
  что и в ArtifactGate);
- браузер внутри песочницы обязан использовать ОТДЕЛЬНЫЙ профиль, продовый не
  переиспользуется (non-negotiable #9) — здесь только контракт, реализация
  подключается вместе с браузерным рантаймом.
"""
from __future__ import annotations

from pathlib import Path

from .. import errors
from .models import SandboxSession

# git-подкоманды, безопасные внутри одноразовой копии. Всё, что публикует или
# меняет远 настройки, отсутствует НАМЕРЕННО.
GIT_ALLOWED = frozenset({
    "status", "diff", "log", "show", "add", "commit", "checkout", "branch",
    "stash", "restore", "rev-parse", "ls-files",
})
GIT_FORBIDDEN = frozenset({
    "push", "remote", "config", "fetch", "pull", "clone", "submodule",
    "tag", "am", "apply", "format-patch", "request-pull",
})

# Исполняемые, допустимые для shell-шага. Строка целиком не принимается никогда.
SHELL_ALLOWED = ("python", "python3", "pytest", "pip", "node", "npm", "npx",
                 "go", "cargo", "make", "ls", "cat", "grep", "find", "echo",
                 "/usr/bin/env", "sh", "bash")


def _workdir(session: SandboxSession, runtime) -> Path:
    wd = getattr(runtime, "workdir", None)
    path = wd(session) if callable(wd) else None
    if path is None:
        raise errors.BossmanError("sandbox workdir is unknown")
    return Path(path)


def contained(session: SandboxSession, runtime, rel_path: str) -> Path:
    """Путь внутри рабочей области песочницы или отказ. Та же защита, что и в
    ArtifactGate: абсолютные пути, '..' и symlink-выход наружу запрещены."""
    root = _workdir(session, runtime).resolve()
    p = Path(rel_path)
    if p.is_absolute() or ".." in p.parts:
        raise errors.PolicyDenied(f"path escapes sandbox workspace: {rel_path}")
    real = (root / p).resolve()
    try:
        real.relative_to(root)
    except ValueError as exc:
        raise errors.PolicyDenied(
            f"path escapes sandbox workspace: {rel_path}", extra={"resolved": str(real)}) from exc
    return real


def shell_argv(argv) -> tuple[str, ...]:
    """Проверить команду shell-шага. Только массив и только знакомое исполняемое."""
    if isinstance(argv, str):
        raise errors.PolicyDenied(
            "shell command must be an argument array, not a string "
            "(a string would be a shell injection)")
    if not isinstance(argv, (list, tuple)) or not argv:
        raise errors.PolicyDenied("empty shell command")
    out = [str(a) for a in argv]
    head = out[0].rsplit("/", 1)[-1]
    allowed = {x.rsplit("/", 1)[-1] for x in SHELL_ALLOWED}
    if head not in allowed:
        raise errors.PolicyDenied(f"executable not allowed in sandbox shell: {head}",
                                  extra={"allowed": sorted(allowed)})
    # sh/bash допустимы, но НЕ с «-c <строка>»: это тот же обход через шелл.
    if head in ("sh", "bash") and any(a == "-c" for a in out):
        raise errors.PolicyDenied("sh -c '<string>' is a shell injection vector; pass argv")
    return tuple(out)


def git_argv(args) -> tuple[str, ...]:
    """Проверить git-команду. Публикующие подкоманды отсутствуют намеренно:
    push/PR — действие ВЛАДЕЛЬЦА, а не песочницы."""
    if isinstance(args, str):
        raise errors.PolicyDenied("git command must be an argument array, not a string")
    if not isinstance(args, (list, tuple)) or not args:
        raise errors.PolicyDenied("empty git command")
    out = [str(a) for a in args]
    sub = out[0]
    if sub in GIT_FORBIDDEN:
        raise errors.PolicyDenied(
            f"git '{sub}' is not available inside the sandbox: publishing and remote "
            f"configuration stay with the owner", extra={"subcommand": sub})
    if sub not in GIT_ALLOWED:
        raise errors.PolicyDenied(f"git '{sub}' is not in the allowed subset",
                                  extra={"allowed": sorted(GIT_ALLOWED)})
    return ("git", *out)


def browser_profile_dir(session: SandboxSession, runtime) -> Path:
    """Отдельный профиль браузера ВНУТРИ песочницы. Продовый профиль
    (`toolkit/browser.py`) не переиспользуется никогда (non-negotiable #9)."""
    return _workdir(session, runtime).parent / "browser-profile"
