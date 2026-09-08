"""§18 — где сайдкар и что он такое. Манифест пиннирован; бинарь — отдельно.

Разница, которую этот модуль обязан удержать: `integration.json` содержит
закреплённый upstream SHA, но это утверждение о ДОКУМЕНТАЦИИ. Что за код
собрали в тот исполняемый файл, который сейчас лежит на машине владельца, —
другой вопрос, и ответ на него честно называется VERSION_UNVERIFIED, пока
бинарь не докажет своё происхождение. Назвать сборку пиннированной потому, что
пиннирован манифест, — это подменить проверку ссылкой на самих себя.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from .models import BinaryStatus, VERSION_UNVERIFIED

#: Переменная окружения с явным путём к исполняемому файлу (§18).
EXECUTABLE_ENV = "AIFS_EXECUTABLE"
#: Каталог, куда владелец ставит внешнюю интеграцию (§19). Внутрь Bossman
#: upstream не кладётся никогда.
INSTALL_DIR_ENV = "AIFS_INSTALL_DIR"
#: Явный путь к INI сайдкара, если он лежит не там, где ожидается.
CONFIG_ENV = "AIFS_CONFIG"

_MANIFEST_PATH = Path(__file__).resolve().parents[3] / "integrations" / \
    "ai-file-sorter" / "integration.json"


@lru_cache(maxsize=1)
def manifest() -> dict[str, Any]:
    """Манифест интеграции. Единственное место, где записан закреплённый SHA."""
    try:
        return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def pinned_sha() -> str:
    return str(manifest().get("pinned_sha") or "")


def expected_binary() -> str:
    return str(manifest().get("expected_binary") or "aifilesorter")


@dataclass
class Discovery:
    """Что доктор знает о сайдкаре. Каждое поле — наблюдение или честное «нет»."""
    status: BinaryStatus
    resolved_executable: str = ""
    binary_version: str = VERSION_UNVERIFIED
    binary_sha256: str = ""
    pinned_upstream_sha_expected: str = ""
    protocol_version: int | None = None
    version_verified: bool = False
    detail: str = ""
    config_path: str = ""
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "resolved_executable": self.resolved_executable,
            "binary_version": self.binary_version,
            "binary_sha256": self.binary_sha256,
            "pinned_upstream_sha_expected": self.pinned_upstream_sha_expected,
            "protocol_version": self.protocol_version,
            "version_verified": self.version_verified,
            "detail": self.detail,
            "config_path": self.config_path,
            "notes": list(self.notes),
        }


def _candidate_paths() -> list[Path]:
    """Порядок поиска: явная настройка → каталог интеграции → PATH.

    Совпадение в PATH принимается, но записывается: «нашёлся какой-то
    aifilesorter» и «владелец указал вот этот» — разные факты, и в квитанции
    они должны различаться.
    """
    candidates: list[Path] = []
    explicit = os.environ.get(EXECUTABLE_ENV, "").strip()
    if explicit:
        candidates.append(Path(explicit))
    install_dir = os.environ.get(INSTALL_DIR_ENV, "").strip()
    name = expected_binary()
    if install_dir:
        for sub in ("", "bin", "app"):
            candidates.append(Path(install_dir) / sub / name)
    found = shutil.which(name)
    if found:
        candidates.append(Path(found))
    return candidates


def _sha256_of(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _probe(executable: Path, *, timeout: float = 10.0) -> tuple[bool, str]:
    """Спросить у бинаря его headless-справку — проверка, что протокол живой.

    Это проба ПРОТОКОЛА, а не версии: `--headless-help` существует у upstream и
    отвечает без побочных эффектов. Если она не отвечает как ожидается,
    состояние — PROTOCOL_FAILED, а не «наверное, всё в порядке».
    """
    try:
        result = subprocess.run([str(executable), "--headless-help"],
                                capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return False, "executable disappeared between discovery and probe"
    except subprocess.TimeoutExpired:
        return False, "sidecar did not answer --headless-help within the timeout"
    except OSError as exc:
        return False, f"sidecar could not be executed: {exc.strerror}"
    text = f"{result.stdout}\n{result.stderr}".lower()
    if "--headless" not in text:
        return False, "sidecar did not describe the expected headless contract"
    return True, ""


def default_config_path() -> Path | None:
    explicit = os.environ.get(CONFIG_ENV, "").strip()
    if explicit:
        return Path(explicit)
    home = Path.home()
    for candidate in (
        home / ".config" / "ai-file-sorter" / "ai-file-sorter.ini",
        home / ".ai-file-sorter" / "ai-file-sorter.ini",
        Path(os.environ.get("APPDATA", "")) / "AI File Sorter" / "ai-file-sorter.ini"
        if os.environ.get("APPDATA") else None,
    ):
        if candidate is not None and candidate.is_file():
            return candidate
    return None


def discover(*, probe: bool = True) -> Discovery:
    """Найти сайдкар и честно описать, что именно найдено."""
    expected = pinned_sha()
    config = default_config_path()
    config_text = str(config) if config else ""

    for candidate in _candidate_paths():
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            continue
        notes: list[str] = []
        if not os.environ.get(EXECUTABLE_ENV, "").strip():
            notes.append(
                "resolved from PATH or the integration directory; set "
                f"{EXECUTABLE_ENV} to pin the exact executable")
        if probe:
            ok, why = _probe(candidate)
            if not ok:
                return Discovery(
                    status=BinaryStatus.PROTOCOL_FAILED,
                    resolved_executable=str(candidate),
                    binary_sha256=_sha256_of(candidate),
                    pinned_upstream_sha_expected=expected,
                    detail=why, config_path=config_text, notes=notes)
        # Бинарь не сообщает upstream SHA, из которого собран, и придумывать его
        # нельзя. Это и есть VERSION_UNVERIFIED: файл найден и говорит на нужном
        # протоколе, а происхождение сборки не доказано.
        notes.append(
            "the executable does not report the upstream commit it was built from; "
            "the pin in integration.json describes the documented integration, not "
            "this build")
        return Discovery(
            status=BinaryStatus.AVAILABLE,
            resolved_executable=str(candidate),
            binary_version=VERSION_UNVERIFIED,
            binary_sha256=_sha256_of(candidate),
            pinned_upstream_sha_expected=expected,
            protocol_version=int(manifest().get("supported_contract_version") or 1),
            version_verified=False,
            config_path=config_text,
            notes=notes,
        )

    return Discovery(
        status=BinaryStatus.NOT_INSTALLED,
        pinned_upstream_sha_expected=expected,
        config_path=config_text,
        detail=(f"no {expected_binary()!r} executable found; set {EXECUTABLE_ENV} or "
                f"{INSTALL_DIR_ENV} after installing it outside the Bossman tree"),
    )
