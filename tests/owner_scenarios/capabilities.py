"""Честные пробы способностей среды: что в этом прогоне ЕСТЬ на самом деле.

Ни одна проба не «чинит» отсутствие способности и не подставляет заглушку. Её
единственная задача — назвать блокер словами владельца, чтобы сценарий получил
честный вердикт вместо синтетического PASS.

Отдельная и важная проба — ЧИСТОТА ВЕТКИ. В этом рабочем каталоге установленные
через editable пакеты (`bcc`, `bossman_shared`) умеют подтягивать подмодули из
ДРУГОГО рабочего каталога с другим коммитом. Тогда сценарий «зеленел» бы на
коде, которого на этой ветке нет. Такой импорт считается ОТСУТСТВИЕМ
способности на ветке, а не её наличием.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: Куда сваливается каждая недостающая способность. Пустая строка запрещена:
#: каждый блокер обязан иметь адресата — владелец, железо или сама среда.
MISSING_LEVEL = {
    "ai_key": "OWNER_REQUIRED",
    "postgres": "OWNER_REQUIRED",
    "browser": "OWNER_HARDWARE_REQUIRED",
    "desktop": "OWNER_HARDWARE_REQUIRED",
    "ffprobe": "OWNER_HARDWARE_REQUIRED",
    "bossman_core": "INSUFFICIENT_EVIDENCE",
    "command_center": "INSUFFICIENT_EVIDENCE",
    "telegram_companion": "INSUFFICIENT_EVIDENCE",
    "gateway_router": "FAIL",
}


@dataclass(frozen=True)
class Capability:
    name: str
    present: bool
    detail: str

    @property
    def missing_level(self) -> str:
        return MISSING_LEVEL[self.name]


def _add_product_paths() -> None:
    """Пути ЭТОЙ ветки идут первыми: чужой рабочий каталог продуктом не считается."""
    for part in (str(ROOT), str(ROOT / "bossman-core"), str(ROOT / "command-center")):
        if part not in sys.path:
            sys.path.insert(0, part)


def _module_on_branch(name: str) -> tuple[bool, str]:
    """Импортируется ли модуль И лежит ли он в ЭТОМ рабочем каталоге.

    Модуль, приехавший из другого checkout, — не способность этой ветки.
    """
    _add_product_paths()
    try:
        module = importlib.import_module(name)
    except BaseException as exc:  # noqa: BLE001 — любая поломка импорта есть отсутствие
        return False, f"{type(exc).__name__}: {str(exc)[:160]}"
    origin = getattr(module, "__file__", "") or ""
    if not origin:
        return False, f"{name}: у модуля нет файла"
    try:
        Path(origin).resolve().relative_to(ROOT)
    except ValueError:
        return False, (f"{name} загружен НЕ с этой ветки ({origin}); "
                       "на release/bossman-owner этой способности нет")
    return True, origin


def _ai_key() -> Capability:
    sys.path.insert(0, str(ROOT / "tools"))
    from ci_ai_provider import KEY_ENV_VARS
    present = any((os.environ.get(name) or "").strip() for name in KEY_ENV_VARS)
    return Capability("ai_key", present,
                      "ключ найден" if present
                      else "ключ ИИ не задан ни в одной из: " + ", ".join(KEY_ENV_VARS))


def _binary(name: str, cap: str) -> Capability:
    path = shutil.which(name)
    return Capability(cap, bool(path), path or f"бинарь {name} не найден в PATH")


def _browser() -> Capability:
    try:
        if importlib.util.find_spec("playwright") is None:
            return Capability("browser", False, "playwright не установлен")
    except BaseException as exc:  # noqa: BLE001 — отсутствие движка это отсутствие
        return Capability("browser", False, f"playwright недоступен: {type(exc).__name__}")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            path = p.chromium.executable_path
    except BaseException as exc:  # noqa: BLE001
        return Capability("browser", False, f"браузер недоступен: {type(exc).__name__}: {exc}"[:180])
    if not path or not Path(path).exists():
        return Capability("browser", False,
                          "движок playwright установлен, но бинарь браузера не скачан")
    return Capability("browser", True, path)


def _desktop() -> Capability:
    display = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    if os.name == "nt":
        return Capability("desktop", False, "рабочий стол Windows недоступен из CI-контейнера")
    return Capability("desktop", bool(display),
                      display or "нет DISPLAY/WAYLAND_DISPLAY: настоящего рабочего стола нет")


def _postgres() -> Capability:
    url = os.environ.get("BOSSMAN_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""
    if not url:
        return Capability("postgres", False,
                          "BOSSMAN_DATABASE_URL/DATABASE_URL не заданы: живой PostgreSQL владельца недоступен")
    return Capability("postgres", True, "строка подключения задана окружением")


def _module_capability(cap: str, module: str) -> Capability:
    ok, detail = _module_on_branch(module)
    return Capability(cap, ok, detail)


_PROBES = {
    "ai_key": _ai_key,
    "browser": _browser,
    "desktop": _desktop,
    "postgres": _postgres,
    "ffprobe": lambda: _binary("ffprobe", "ffprobe"),
    "bossman_core": lambda: _module_capability("bossman_core", "bossman.toolkit.files"),
    "command_center": lambda: _module_capability("command_center", "bcc.approvals"),
    "telegram_companion": lambda: _module_capability("telegram_companion", "bcc.telegram_companion"),
    "gateway_router": lambda: _module_capability("gateway_router", "bossman.gateway.router"),
}

_CACHE: dict[str, Capability] = {}


def probe(name: str) -> Capability:
    """Проба НИКОГДА не роняет прогон.

    Упавшая проба — это «способности нет», а не крушение табло: иначе
    отсутствие одной библиотеки уносило бы вместе с собой все двадцать
    сценариев, включая те, которым она не нужна.
    """
    if name not in _PROBES:
        raise KeyError(f"неизвестная способность: {name}")
    if name not in _CACHE:
        try:
            _CACHE[name] = _PROBES[name]()
        except BaseException as exc:  # noqa: BLE001 — fail-closed, см. docstring
            _CACHE[name] = Capability(name, False,
                                      f"проба не выполнилась: {type(exc).__name__}: {str(exc)[:120]}")
    return _CACHE[name]


def missing(names) -> list[Capability]:
    """Недостающие способности в порядке объявления — блокеры сценария."""
    return [c for c in (probe(n) for n in names) if not c.present]


def inventory() -> dict[str, dict]:
    """Полная опись среды для отчёта: что есть, чего нет и почему."""
    return {name: {"present": probe(name).present, "detail": probe(name).detail}
            for name in sorted(_PROBES)}
