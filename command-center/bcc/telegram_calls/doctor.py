"""Doctor of the Telegram calls module: ``bossman call doctor`` / dashboard / scripts/bossman_doctor.py.

``run_checks(data_dir) -> list[dict]``; every item is ``{"id", "status": PASS|WARN|BLOCKED, "message", "hint"}``
(Russian; ``hint`` is the remedy, empty for PASS). Guarantees: never loads a Whisper / Piper / Silero model, never
places a call, never contacts Telegram, never prints a secret (credentials and login are reported as booleans, phone
numbers / codes / session are never read into a message). The only subprocesses are two short ``python -I`` import
probes (worker importable, ntgcalls loads), both injectable for tests.
"""
from __future__ import annotations

import importlib.metadata as md
import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from . import addons

Probe = Callable[[list[str]], tuple[int, str]]
PASS, WARN, BLOCKED = "PASS", "WARN", "BLOCKED"


def _item(id_: str, status: str, message: str, hint: str = "", **extra: Any) -> dict[str, Any]:
    return {"id": id_, "status": status, "message": message, "hint": "" if status == PASS else hint, **extra}


def _run_probe(argv: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)  # noqa: S603
        if p.returncode == 0:
            return 0, "ok"
        lines = (p.stderr or "").strip().splitlines()
        return p.returncode, (lines[-1][:120] if lines else "error")
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except OSError as exc:
        return 127, type(exc).__name__


def _addon_version(data_dir: Path, dist: str) -> str | None:
    """Version of ``dist`` from the add-on dir first, then from the running interpreter. Nothing is imported."""
    pk = addons.addon_path(data_dir)
    for path in ([str(pk)] if pk.is_dir() else []) + [None]:
        try:
            return md.version(dist) if path is None else next(iter(md.distributions(name=dist, path=[path]))).version
        except (md.PackageNotFoundError, StopIteration):
            continue
    return None


def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _level(name: str) -> str:
    return {"PASS": PASS, "WARN": WARN}.get(name, BLOCKED)


def run_checks(data_dir: Path | str, *, probe: Probe | None = None) -> list[dict[str, Any]]:
    data_dir = Path(data_dir)
    probe = probe or _run_probe
    out: list[dict[str, Any]] = []

    # 1. interpreter
    v = sys.version_info
    if (v.major, v.minor) == (3, 12) and sys.platform == "win32":
        out.append(_item("python", PASS, f"Python {v.major}.{v.minor}.{v.micro} (совпадает с колесом ntgcalls cp312 win_amd64)."))
    elif (v.major, v.minor) >= (3, 11):
        out.append(_item("python", WARN, f"Python {v.major}.{v.minor} на {sys.platform}: закреплённое колесо ntgcalls — cp312 win_amd64.",
                         "Настоящий звонок проверяется на Windows с Python 3.12 (встроенный Python Bossman)."))
    else:
        out.append(_item("python", BLOCKED, f"Python {v.major}.{v.minor} слишком старый.", "Нужен Python 3.11+ (встроенный Python Bossman — 3.12)."))

    # 2. add-on / calls extra
    versions = {d: _addon_version(data_dir, d) for d in ("py-tgcalls", "ntgcalls", "telethon")}
    missing = [d for d, ver in versions.items() if ver is None]
    if not missing:
        out.append(_item("calls_packages", PASS, "Установлены: " + ", ".join(f"{d} {ver}" for d, ver in versions.items()) + "."))
    else:
        out.append(_item("calls_packages", BLOCKED, "Не установлены: " + ", ".join(missing) + ".",
                         "Выполните: bossman call install (или pip install \"bossman-command-center[calls]\"). "
                         "Установщик принимает только файлы с проверенным sha256."))
    pinned = {"py-tgcalls": "3.0.0", "ntgcalls": "3.0.0", "telethon": "1.45.0"}
    off = [f"{d} {ver} (нужна {pinned[d]})" for d, ver in versions.items() if ver is not None and ver != pinned[d]]
    if off:
        out.append(_item("calls_versions", WARN, "Версии отличаются от проверенных: " + ", ".join(off) + ".",
                         "Поведение проверено только на закреплённых версиях: bossman call install."))

    # 3. ntgcalls native module loads (subprocess, never in the Command Center process)
    if versions["ntgcalls"] is None:
        out.append(_item("ntgcalls_loads", BLOCKED, "ntgcalls не установлен, проверка загрузки пропущена.", "Сначала: bossman call install."))
    else:
        pk = addons.addon_path(data_dir)
        code = ("import sys; p=%r; sys.path.insert(0,p) if p else None; import ntgcalls" % (str(pk) if pk.is_dir() else ""))
        rc, why = probe([sys.executable, "-I", "-c", code])
        out.append(_item("ntgcalls_loads", PASS, "Модуль ntgcalls загружается.") if rc == 0 else
                   _item("ntgcalls_loads", BLOCKED, f"ntgcalls не загружается ({why}).",
                         "Проверьте версию Python/платформу (нужен cp312 win_amd64) и переустановите: bossman call install."))

    # 4-7. speech engines, brain routes, VAD (the factory never loads a model here)
    from .speech import factory
    try:
        fd = factory.doctor(data_dir)
        items = {i["name"]: i for i in fd["items"]}
    except Exception as exc:  # noqa: BLE001
        items = {}
        out.append(_item("speech", BLOCKED, f"Проверка речевых модулей не выполнена ({type(exc).__name__}).", "Смотрите журнал Command Center."))
    cfg = factory.SpeechConfig(data_dir=data_dir)
    models = cfg.models
    if items:
        s = items["stt"]
        info = s.get("info") or {}
        out.append(_item("faster_whisper", PASS if info.get("package_installed") else BLOCKED,
                         "Пакет faster-whisper установлен." if info.get("package_installed") else "Пакет faster-whisper не установлен.",
                         "Установите расширение speech (входит в runtime-установку Bossman)."))
        out.append(_item("whisper_model", _level(s["level"]),
                         "Модель Whisper найдена." if s["ok"] else "Локальная модель Whisper не найдена.",
                         f"Положите готовую модель faster-whisper в {models / 'whisper' if models else 'addons/telegram-calls/models/whisper'} "
                         "или задайте BOSSMAN_WHISPER_MODEL_PATH. Скачивание из сети автоматически не выполняется."))
        t = items["tts"]
        tinfo = t.get("info") or {}
        out.append(_item("piper", PASS if tinfo.get("package_installed") else BLOCKED,
                         "Пакет Piper установлен." if tinfo.get("package_installed") else "Пакет Piper (piper-tts) не установлен.",
                         "Установите piper-tts (bossman call install после закрепления хеша) или используйте готовый пакет."))
        voice = str(tinfo.get("voice") or "")
        ru = voice.lower().startswith("ru") or str(tinfo.get("language") or "").lower().startswith("ru")
        if not t["ok"]:
            out.append(_item("piper_voice", BLOCKED, "Русский голос Piper не найден.",
                             f"Положите русский голос Piper (.onnx + .onnx.json) в {models / 'piper' if models else 'addons/telegram-calls/models/piper'} "
                             "или задайте BOSSMAN_PIPER_VOICE_PATH."))
        elif not ru:
            out.append(_item("piper_voice", WARN, f"Голос Piper найден ({voice or 'без имени'}), но он не помечен как русский.",
                             "Выберите русский голос (имя начинается с ru_)."))
        else:
            out.append(_item("piper_voice", PASS, f"Русский голос Piper найден ({voice})."))
        vad = items["vad"]
        out.append(_item("silero_vad", _level(vad["level"]),
                         "Silero VAD доступен." if vad["level"] == "PASS" else "Silero VAD не установлен: используется упрощённый детектор речи.",
                         "Установите pysilero-vad (модель входит в пакет); без него точность конца фразы ниже."))
        b = items["brain"]
        binfo = b.get("info") or {}
        routes = ", ".join(f"{r.get('route')}: {r.get('model')}" for r in binfo.get("routes", [])) or "нет"
        out.append(_item("companion_routes", _level(b["level"]),
                         f"Локальные модели разговора (маршруты Telegram-компаньона): {routes}." if b["ok"] else
                         "Локальные модели разговора не настроены.",
                         "Задайте локальные модели в разделе Telegram (быстрая/лучшая); облачный запасной вариант не используется."))

    # 8. login / credentials state (booleans only)
    try:
        from .account.credentials import CredentialStore
        st = CredentialStore(data_dir).status()
        out.append(_item("credentials", PASS if st["has_credentials"] else WARN,
                         "api_id и api_hash сохранены." if st["has_credentials"] else "api_id и api_hash ещё не сохранены.",
                         "Получите их на my.telegram.org и введите на экране подключения (сами, локально)."))
        out.append(_item("login", PASS if st["has_session"] else WARN,
                         "Аккаунт подключён (сессия есть)." if st["has_session"] else "Аккаунт ещё не подключён.",
                         "Пройдите вход на экране «Telegram-звонки»: номер, код, пароль 2FA — вводите только сами."))
    except Exception as exc:  # noqa: BLE001 - e.g. the Vault cannot be opened; class name only
        out.append(_item("credentials", WARN, f"Состояние учётных данных не прочитано ({type(exc).__name__}).",
                         "Проверьте хранилище Vault Command Center."))

    # 9. settings: enabled, peer
    try:
        from .settings import SettingsStore
        s = SettingsStore(data_dir).load()
        out.append(_item("calls_enabled", PASS, "Звонки включены." if s.enabled else "Звонки выключены (так и задумано по умолчанию)."))
        if s.peer is not None and s.peer_confirmed:
            out.append(_item("peer", PASS, "Тестовый собеседник выбран и подтверждён."))
        elif s.peer is not None:
            out.append(_item("peer", WARN, "Тестовый собеседник выбран, но не подтверждён.", "Подтвердите выбор второго аккаунта на экране «Telegram-звонки»."))
        else:
            out.append(_item("peer", WARN, "Тестовый собеседник не выбран.", "Выберите ВТОРОЙ аккаунт и подтвердите выбор."))
    except Exception as exc:  # noqa: BLE001
        out.append(_item("peer", WARN, f"Настройки звонков не прочитаны ({type(exc).__name__}).", "Проверьте каталог данных Command Center."))

    # 10. STOP
    from .stopflag import StopFlag
    sf = StopFlag(data_dir).info()
    if sf["global_stop"] or sf["call_stop"]:
        out.append(_item("stop_flag", BLOCKED, "Действует STOP: " + ("общий" if sf["global_stop"] else "звонков") + ", звонки заблокированы.",
                         "Снимите STOP вручную («Продолжить»), затем повторите."))
    else:
        out.append(_item("stop_flag", PASS, "STOP не активен."))

    # 11. worker spawnable
    if not Path(sys.executable).is_file():
        out.append(_item("worker", BLOCKED, "Интерпретатор Python не найден.", "Переустановите Bossman."))
    else:
        rc, why = probe([sys.executable, "-I", "-c", "import bcc.telegram_calls.call.worker"])
        out.append(_item("worker", PASS, "Процесс звонков запускается (python -I, импорт worker).") if rc == 0 else
                   _item("worker", BLOCKED, f"Процесс звонков не запускается ({why}).",
                         "Запустите Bossman из установленной копии (не из исходников) и повторите bossman call doctor."))
    return out


def verdict(items: list[dict[str, Any]]) -> str:
    levels = {i["status"] for i in items}
    return BLOCKED if BLOCKED in levels else WARN if WARN in levels else PASS
