"""Which source is actually running — one answer, given to the API and the UI.

The failure this exists to prevent: testing one checkout while believing another
SHA is running. An owner breaker session that cannot name the code under it
produces evidence nobody can bind to a commit, and a green run against the wrong
tree is worse than no run.

So the rule here is that an unproven identity is NAMED, never guessed:

    SOURCE_IDENTITY_UNKNOWN  != a SHA
    "probably the checkout"  != proof

`run_provenance.repository_sha()` already resolves this correctly for both
shapes (installed wheel via `bcc/_build.json`, clean checkout via git) and
returns NOT_CAPTURED when it cannot prove either. This module only turns that
into the owner-facing shape and keeps it cheap enough for a health endpoint.
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any

#: Что показывается вместо SHA, когда источник не доказан. Не пустая строка и
#: не «unknown» строчными: владелец должен видеть это как СОСТОЯНИЕ, а не как
#: отсутствующее поле, которое легко принять за косметику.
UNKNOWN = "SOURCE_IDENTITY_UNKNOWN"

_HEX40 = re.compile(r"[0-9a-f]{40}")

#: Живой чекаут и установленное колесо отвечают по-разному дорого: первое —
#: подпроцесс git, второе — чтение маленького json. `/health` опрашивают часто,
#: поэтому ответ живёт несколько секунд. TTL короткий намеренно: сдвинутый HEAD
#: обязан стать видимым в пределах одного взгляда владельца, а не после
#: перезапуска.
_TTL_SECONDS = 5.0
_lock = threading.Lock()
_cached: tuple[float, dict[str, Any]] | None = None


def _resolve() -> dict[str, Any]:
    from .run_provenance import NOT_CAPTURED, repository_sha

    installed = Path(__file__).with_name("_build.json").exists()
    try:
        sha = repository_sha()
    except Exception:                                   # pragma: no cover - защитный
        sha = NOT_CAPTURED
    # Длины мало: «zzz…» — сорок символов и не SHA. Резолвер ниже уже проверяет
    # форму, но личность источника не должна зависеть от чужой аккуратности.
    if not isinstance(sha, str) or sha == NOT_CAPTURED or not _HEX40.fullmatch(sha):
        # Доказать не удалось. Это ответ, а не пропуск поля.
        return {"build_sha": None, "build_sha_short": None,
                "source_identity": UNKNOWN,
                "source": "installed_build" if installed else "checkout",
                "detail": ("установленная сборка без bcc/_build.json"
                           if installed else
                           "рабочее дерево не является чистым git-чекаутом этого пакета")}
    return {"build_sha": sha, "build_sha_short": sha[:12],
            "source_identity": "PASS",
            "source": "installed_build" if installed else "checkout",
            "detail": ""}


def source_identity(*, fresh: bool = False) -> dict[str, Any]:
    """Личность работающего исходника. `fresh=True` обходит кэш."""
    global _cached
    now = time.monotonic()
    if not fresh:
        cached = _cached
        if cached is not None and now - cached[0] < _TTL_SECONDS:
            return dict(cached[1])
    value = _resolve()
    with _lock:
        _cached = (now, value)
    return dict(value)


def reset_cache() -> None:
    """Для тестов: следующий вызов пересчитает личность."""
    global _cached
    with _lock:
        _cached = None
