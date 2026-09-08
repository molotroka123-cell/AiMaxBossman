#!/usr/bin/env python3
"""`bossman doctor` — предполётная проверка перед реальным прогоном владельца.

Зачем отдельный инструмент: до сих пор единственным способом узнать, запустится
ли система на этой машине, был запуск системы. Половина вечерних проблем — не
дефекты кода, а отсутствующий ffmpeg, занятый порт или каталог состояния, в
который нельзя писать. Такие вещи обязаны диагностироваться за секунды и ДО
приёмки, а не всплывать на пятом тесте.

Три состояния, и различие между ними — суть инструмента:

  PASS     — проверено сейчас, на этой машине.
  WARN     — работать можно, но часть возможностей отключится (и здесь честно
             сказано, какая именно).
  BLOCKED  — приёмку начинать нельзя: это точно сломается.

Никаких «вероятно» и «должно работать»: каждая строка — наблюдение с деталью,
которую можно проверить руками. Выход 0 — блокеров нет, 1 — есть.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

PASS, WARN, BLOCKED = "PASS", "WARN", "BLOCKED"
REPO = Path(__file__).resolve().parents[1]
MIN_PYTHON = (3, 11)


@dataclass
class Check:
    name: str
    status: str
    detail: str
    remedy: str = ""
    facts: dict[str, Any] = field(default_factory=dict)


def _run(cmd: list[str], timeout: float = 6.0) -> tuple[int, str]:
    """Внешняя команда без оболочки. Отсутствие бинарника — обычный результат."""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                             encoding="utf-8", errors="replace")
        return out.returncode, (out.stdout or "") + (out.stderr or "")
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------- checks


def check_python() -> Check:
    v = sys.version_info
    if (v.major, v.minor) < MIN_PYTHON:
        return Check("python", BLOCKED,
                     f"Python {v.major}.{v.minor}.{v.micro}; требуется >= 3.11",
                     "Установите Python 3.11+ и пересоздайте .venv",
                     {"version": platform.python_version()})
    return Check("python", PASS, f"Python {platform.python_version()} ({sys.executable})",
                 facts={"version": platform.python_version(), "executable": sys.executable})


def check_python_packages() -> Check:
    """Импорт, а не `pip show`: важно, что пакет РАБОТАЕТ в этом интерпретаторе."""
    required = {"fastapi": "HTTP-слой Command Center", "uvicorn": "сервер",
                "pydantic": "контракты", "httpx": "исходящие запросы",
                "sqlalchemy": "хранилище", "aiosqlite": "SQLite-драйвер",
                "yaml": "планы проектов", "cryptography": "подписи и TLS"}
    optional = {"psutil": "метрики памяти/процессов в телеметрии",
                "playwright": "живой браузер (TEST 2/3 вечерней приёмки)",
                "jsonschema": "валидация схем в тестах"}
    missing = [f"{m} ({why})" for m, why in required.items() if not _importable(m)]
    absent = [f"{m} ({why})" for m, why in optional.items() if not _importable(m)]
    if missing:
        return Check("python-packages", BLOCKED,
                     "не импортируются обязательные пакеты: " + ", ".join(missing),
                     "python -m pip install -e . -e command-center -e bossman-core",
                     {"missing": missing, "optional_missing": absent})
    if absent:
        return Check("python-packages", WARN,
                     "нет необязательных пакетов: " + ", ".join(absent),
                     "python -m pip install psutil playwright jsonschema",
                     {"optional_missing": absent})
    return Check("python-packages", PASS, "все обязательные и необязательные пакеты импортируются")


def _importable(module: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def check_bossman_packages() -> Check:
    """Импортируются ли САМИ пакеты продукта — свежий клон без установки не работал."""
    probe = (
        "import sys;"
        f"sys.path[:0]=[{str(REPO)!r},{str(REPO / 'bossman-core')!r},{str(REPO / 'command-center')!r}];"
        "import bossman_shared, bossman_v3.memory.journal, bossman_v3.execution, bcc.config;"
        "print('ok')"
    )
    code, out = _run([sys.executable, "-c", probe], timeout=60)
    if code != 0:
        return Check("bossman-packages", BLOCKED,
                     "пакеты продукта не импортируются: " + out.strip().splitlines()[-1:][0]
                     if out.strip() else "пакеты продукта не импортируются",
                     "python -m pip install -e . -e command-center -e bossman-core")
    return Check("bossman-packages", PASS, "bossman_shared, bossman_v3 и bcc импортируются")


def check_node() -> Check:
    node = shutil.which("node")
    if not node:
        return Check("node", WARN, "Node.js не найден; UI-тесты на JS не запустятся",
                     "Установите Node 20+ (только для тестов UI, само приложение работает без него)")
    code, out = _run([node, "--version"])
    return Check("node", PASS if code == 0 else WARN, f"Node {out.strip() or 'неизвестной версии'}",
                 facts={"path": node})


def check_ffmpeg() -> Check:
    """Video Studio без ffmpeg честно отключается, но вечерний TEST 6 без него невозможен."""
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        missing = [n for n, p in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)) if not p]
        return Check("ffmpeg", WARN,
                     f"нет {', '.join(missing)}: Video Studio откажет честно (FFMPEG_MISSING), "
                     "но TEST 6 вечерней приёмки выполнить нельзя",
                     "Windows: winget install Gyan.FFmpeg | Linux: apt install ffmpeg | macOS: brew install ffmpeg",
                     {"ffmpeg": ffmpeg, "ffprobe": ffprobe})
    code, out = _run([ffmpeg, "-version"])
    version = out.splitlines()[0] if out.splitlines() else ""
    return Check("ffmpeg", PASS if code == 0 else WARN, version or "ffmpeg найден",
                 facts={"ffmpeg": ffmpeg, "ffprobe": ffprobe})


def check_port(port: int) -> Check:
    """Занятый порт — самая частая причина «приложение не открылось»."""
    host = os.environ.get("BCC_HOST", "127.0.0.1")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        if sock.connect_ex((host, port)) == 0:
            return Check("port", WARN, f"{host}:{port} уже занят — вероятно, Bossman уже запущен",
                         f"Закройте прежний экземпляр или задайте BCC_PORT=<другой порт>",
                         {"host": host, "port": port, "in_use": True})
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
    except OSError as exc:
        return Check("port", BLOCKED, f"нельзя занять {host}:{port}: {exc}",
                     "Задайте BCC_PORT=<свободный порт>", {"host": host, "port": port})
    finally:
        probe.close()
    return Check("port", PASS, f"{host}:{port} свободен", facts={"host": host, "port": port})


def check_state_dir() -> Check:
    """Каталог состояния проверяется РЕАЛЬНОЙ записью и fsync, а не флагом доступа."""
    target = Path(os.environ.get("BCC_DATA_DIR") or (REPO / "command-center" / "data")).expanduser()
    try:
        target.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".doctor-", dir=target)
        try:
            os.write(fd, b"doctor")
            os.fsync(fd)
        finally:
            os.close(fd)
            os.unlink(tmp)
    except OSError as exc:
        return Check("state-dir", BLOCKED, f"нельзя писать в {target}: {exc}",
                     "Выберите доступный каталог через BCC_DATA_DIR", {"path": str(target)})
    free_gb = round(shutil.disk_usage(target).free / 1024 ** 3, 2)
    if free_gb < 2:
        return Check("state-dir", WARN, f"{target}: свободно всего {free_gb} ГБ",
                     "Экспорт видео и журналы требуют места; освободите диск",
                     {"path": str(target), "free_gb": free_gb})
    return Check("state-dir", PASS, f"{target} доступен на запись, свободно {free_gb} ГБ",
                 facts={"path": str(target), "free_gb": free_gb})


def check_evidence_key() -> Check:
    """Без ключа подписи улик журнал fail-closed: ни один шаг не закроется."""
    sys.path[:0] = [str(REPO)]
    try:
        from bossman_shared import evidence
    except Exception as exc:  # noqa: BLE001
        return Check("evidence-key", BLOCKED, f"bossman_shared.evidence недоступен: {exc}",
                     "python -m pip install -e .")
    path = evidence.key_path()
    try:
        key = evidence.load_or_create_key()
    except Exception as exc:  # noqa: BLE001
        return Check("evidence-key", BLOCKED, f"ключ подписи улик недоступен: {exc}",
                     f"Проверьте права на {path.parent}")
    fields = evidence.sign_fields({"doctor": True}, signer=next(iter(evidence.TRUSTED_SIGNERS)))
    ok = evidence.verify_signed({"doctor": True, **fields})
    if not ok:
        return Check("evidence-key", BLOCKED, "подпись улики не проверяется собственным ключом",
                     f"Удалите повреждённый ключ {path} и перезапустите")
    return Check("evidence-key", PASS, f"ключ подписи улик рабочий ({len(key)} байт, {path})",
                 facts={"path": str(path)})


def check_journal_anchor() -> Check:
    """Монотонный якорь журнала: каталог якорей обязан быть доступен на запись."""
    sys.path[:0] = [str(REPO), str(REPO / "bossman-core")]
    try:
        from bossman_v3.memory import journal as J
    except Exception as exc:  # noqa: BLE001
        return Check("journal-anchor", BLOCKED, f"журнал не импортируется: {exc}",
                     "python -m pip install -e bossman-core")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            j = J.TaskJournal.start(task_id="doctor-probe", plan=[("s1", "проба")], root=Path(tmp))
            j.begin("s1", by="doctor")
            anchor = J.read_anchor(Path(tmp), "doctor-probe")
            J.TaskJournal.load(task_id="doctor-probe", root=Path(tmp))
        except Exception as exc:  # noqa: BLE001
            return Check("journal-anchor", BLOCKED, f"журнал не работает на этой машине: {exc}",
                         "Проверьте права на каталог состояния и часы системы")
    return Check("journal-anchor", PASS,
                 f"журнал пишется, читается и защищён от отката (anchor seq={anchor['seq']})",
                 facts={"anchor_root_env": J.ENV_ANCHOR_ROOT})


def check_browser_runtime() -> Check:
    """TEST 2/3 вечерней приёмки требуют НАСТОЯЩИЙ браузер. Фейковый адаптер не считается."""
    candidates = ["chrome", "chromium", "chromium-browser", "google-chrome", "msedge"]
    found = next((shutil.which(c) for c in candidates if shutil.which(c)), None)
    if not found and os.name == "nt":
        for guess in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                      r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"):
            if Path(guess).exists():
                found = guess
                break
    playwright = _importable("playwright")
    if not found and not playwright:
        return Check("browser", BLOCKED,
                     "не найден ни системный Chromium/Chrome/Edge, ни playwright: "
                     "окно Command Center открыть нечем",
                     "Установите Chrome/Edge или `pip install playwright && playwright install chromium`")
    if not found:
        return Check("browser", WARN, "системный браузер не найден, но playwright установлен",
                     "playwright install chromium", {"playwright": True})
    return Check("browser", PASS, f"браузер для окна приложения: {found}",
                 facts={"browser": found, "playwright": playwright})


def check_model_endpoint() -> Check:
    """Локальная модель. Отсутствие — WARN, а не BLOCKED: облачный провайдер тоже вариант."""
    host = os.environ.get("OLLAMA_HOST", "").strip()
    url = host or "http://127.0.0.1:11434"
    if "://" not in url:
        url = f"http://{url}"
    try:
        import urllib.request
        with urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=3) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
        names = [m.get("name", "") for m in body.get("models", [])][:10]
        if not names:
            return Check("model-endpoint", WARN, f"{url} отвечает, но ни одной модели не загружено",
                         "ollama pull <модель>", {"endpoint": url, "models": []})
        return Check("model-endpoint", PASS, f"{url}: {len(names)} моделей ({', '.join(names[:3])}…)",
                     facts={"endpoint": url, "models": names})
    except Exception as exc:  # noqa: BLE001
        return Check("model-endpoint", WARN,
                     f"локальная модель на {url} недоступна ({type(exc).__name__})",
                     "Запустите Ollama, либо настройте облачного провайдера в Command Center",
                     {"endpoint": url})


def check_openhands() -> Check:
    """OpenHands (Coding → задача агенту) — WARN, не BLOCKED: приёмка без него
    возможна, но страница Coding честно скажет, что агент недоступен.

    Аудит владельца 2026-09-08 (F4): до OpenHands из интерфейса было не
    дотянуться, и никто не говорил почему. Здесь называются обе предпосылки:
    рантайм bossman-core импортируется и команда сайдкара настроена."""
    facts: dict[str, Any] = {"runtime": _importable("bossman.apprentice.openhands_client"),
                             "command_env": "BOSSMAN_OPENHANDS_COMMAND",
                             "command_set": bool(os.environ.get("BOSSMAN_OPENHANDS_COMMAND", "").strip())}
    if not facts["runtime"]:
        return Check("openhands", WARN, "рантайм OpenHands (bossman.apprentice) не импортируется",
                     "pip install -e ./bossman-core рядом с Command Center", facts)
    if not facts["command_set"]:
        return Check("openhands", WARN, "команда сайдкара OpenHands не настроена — Coding покажет «агент недоступен»",
                     "задайте BOSSMAN_OPENHANDS_COMMAND (путь к сайдкару) и перезапустите Bossman", facts)
    return Check("openhands", PASS, "рантайм OpenHands и команда сайдкара на месте", facts=facts)


def check_hardware() -> Check:
    """Наблюдение, не рекомендация. Решение про железо принимает real_workload_audit
    по РЕАЛЬНЫМ задачам, а не этот снимок."""
    facts: dict[str, Any] = {"platform": platform.platform(), "machine": platform.machine(),
                             "logical_cpus": os.cpu_count()}
    total_gb = None
    if _importable("psutil"):
        import psutil
        total_gb = round(psutil.virtual_memory().total / 1024 ** 3, 2)
        facts["available_gb"] = round(psutil.virtual_memory().available / 1024 ** 3, 2)
    elif platform.system() == "Linux":
        try:
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("MemTotal:"):
                    total_gb = round(int(line.split()[1]) / 1024 ** 2, 2)
                    break
        except (OSError, ValueError, IndexError):
            pass
    facts["ram_gb"] = total_gb
    if total_gb is None:
        return Check("hardware", WARN, f"{os.cpu_count()} CPU; объём памяти не определён",
                     "pip install psutil для наблюдения памяти", facts)
    if total_gb < 8:
        return Check("hardware", WARN,
                     f"{os.cpu_count()} CPU, {total_gb} ГБ RAM — для локальной модели и рендера мало",
                     "Ожидайте OOM на TEST 6/11; они это зафиксируют честно", facts)
    return Check("hardware", PASS, f"{os.cpu_count()} CPU, {total_gb} ГБ RAM", facts=facts)


def check_windows_specific() -> Check:
    if os.name != "nt":
        return Check("windows", PASS, f"не Windows ({platform.system()}); проверка неприменима")
    problems: list[str] = []
    if not shutil.which("sh") and not os.environ.get("COMSPEC"):
        problems.append("нет ни sh, ни COMSPEC: хостовые команды выполнить нечем")
    try:
        (REPO / "command-center" / "data").mkdir(parents=True, exist_ok=True)
        probe = REPO / "command-center" / "data" / ("x" * 120)
        probe.write_text("длинный путь", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        problems.append(f"длинные пути не работают ({exc}); включите LongPathsEnabled")
    if problems:
        return Check("windows", WARN, "; ".join(problems),
                     "См. docs/release/EVENING_ACCEPTANCE_2026-09-06.md, раздел Windows")
    return Check("windows", PASS, f"Windows {platform.release()}: базовые предпосылки на месте")


# Наблюдение и действия оператора на Windows: pywinauto/pywin32 дают UIA-обход и
# окно переднего плана, pyautogui+Pillow — ввод и скриншот. Без них оператор
# запускается и «работает», но не видит ничего.
_WINDOWS_OPERATOR_DEPS = {"pywinauto": "обход UIA и окно переднего плана",
                          "win32api": "pywin32: разрешение окна и ввод",
                          "pyautogui": "клик, ввод, прокрутка, скриншот",
                          "PIL": "Pillow: сохранение скриншота"}


def check_computer_operator_deps() -> Check:
    """Оператор компьютера на Windows: слепой оператор хуже отсутствующего.

    Живой прогон владельца (20260906): наблюдение возвращало ModuleNotFoundError,
    планировщик выжигал бюджет и задача падала. Диагноз стоил часа; здесь он
    стоит секунды. На не-Windows проверка неприменима — адаптер туда не идёт.
    """
    if os.name != "nt":
        return Check("computer-operator", PASS,
                     f"не Windows ({platform.system()}); Windows-адаптер оператора неприменим")
    missing = {m: why for m, why in _WINDOWS_OPERATOR_DEPS.items() if not _importable(m)}
    if missing:
        return Check("computer-operator", BLOCKED,
                     "управление компьютером не заработает: нет " + ", ".join(
                         f"{m} ({why})" for m, why in missing.items()),
                     "python -m pip install -e bossman-core[windows]",
                     {"missing": sorted(missing)})
    return Check("computer-operator", PASS,
                 "наблюдение и ввод на месте: pywinauto, pywin32, pyautogui, Pillow",
                 facts={"missing": []})


def check_gateway_url() -> Check:
    """Адрес Gateway без версии превращает каждый ход планировщика в 404.

    Живой прогон владельца (20260906, GATEWAY-URL-V1): BOSSMAN_GATEWAY_URL был
    задан без `/v1`, и 21 перепланирование подряд заканчивалось «planner replan
    budget» без единого намёка на причину. Клиент теперь нормализует адрес, но
    владельцу всё равно полезно видеть, ЧТО именно будет использовано.
    """
    raw = os.environ.get("BOSSMAN_GATEWAY_URL", "").strip()
    sys.path[:0] = [str(REPO / "bossman-core")]
    try:
        from bossman.gateway.client import DEFAULT_BASE_URL, normalize_base_url
    except Exception as exc:  # noqa: BLE001
        return Check("gateway-url", WARN, f"клиент Gateway недоступен: {type(exc).__name__}: {exc}",
                     "python -m pip install -e bossman-core")
    if not raw:
        return Check("gateway-url", PASS,
                     f"BOSSMAN_GATEWAY_URL не задан; будет использован {DEFAULT_BASE_URL}",
                     facts={"effective": DEFAULT_BASE_URL, "configured": ""})
    effective = normalize_base_url(raw)
    if effective != raw.rstrip("/"):
        return Check("gateway-url", WARN,
                     f"BOSSMAN_GATEWAY_URL={raw} без версии; запросы пойдут на {effective}",
                     f"Задайте BOSSMAN_GATEWAY_URL={effective}, чтобы адрес совпадал с фактическим",
                     {"configured": raw, "effective": effective})
    return Check("gateway-url", PASS, f"Gateway: {effective}",
                 facts={"configured": raw, "effective": effective})


def _openrouter_env_conflict() -> str:
    """Один ключ под разными именами с РАЗНЫМИ значениями — гарантированный сюрприз."""
    sys.path[:0] = [str(REPO / "command-center")]
    try:
        from bcc.v2.openrouter_identity import env_credential
    except Exception:  # noqa: BLE001 — Command Center может быть не установлен
        return ""
    return env_credential().conflict_message or ""


def _disabled_cloud_backends() -> list[str]:
    """Облачные бэкенды, выключенные в yaml. Нет конфигурации — нечего и выключать."""
    try:
        from bossman.gateway.config import load_gateway_config
        path = os.environ.get("BOSSMAN_GATEWAY_CONFIG") or (REPO / "bossman-core" / "config" / "gateway.yaml")
        cfg = load_gateway_config(path)
    except Exception:  # noqa: BLE001 — доктор не падает из-за чужого конфига
        return []
    return sorted(name for name, b in cfg.backends.items()
                  if b.cloud and not b.enabled)


def check_cloud_providers() -> Check:
    """Облачные ключи: есть — облако подключится, нет — останутся локальные модели.

    Ключ проверяется по факту наличия, без похода в сеть и без вывода значения:
    доктор не имеет права ни расходовать чужую квоту, ни печатать секрет. Живой
    прогон 20260906 (KEY-EXPIRY) показал и обратное: отсутствие ключа обязано
    быть ВИДНО заранее, а не выясняться посреди задачи.
    """
    sys.path[:0] = [str(REPO / "bossman-core")]
    try:
        from bossman.gateway.config import (GLM_MODEL_ENV, OPENROUTER_BASE_URL_ENV,
                                            OPENROUTER_KEY_ENV, ZAI_KEY_ENV,
                                            glm_model_id, load_env_file)
    except Exception as exc:  # noqa: BLE001
        return Check("cloud-providers", WARN,
                     f"конфигурация Gateway недоступна: {type(exc).__name__}: {exc}",
                     "python -m pip install -e bossman-core")
    load_env_file()                       # .env владельца — такой же источник, как окружение
    present = [env for env in (OPENROUTER_KEY_ENV, ZAI_KEY_ENV) if os.environ.get(env, "").strip()]
    facts = {"configured": present, "glm_model": glm_model_id(),
             "openrouter_base_url": os.environ.get(OPENROUTER_BASE_URL_ENV, "") or "(по умолчанию)"}
    disabled = _disabled_cloud_backends()
    facts["disabled_in_config"] = disabled
    conflict = _openrouter_env_conflict()
    facts["credential_conflict"] = conflict or ""
    if conflict:
        # Расхождение имён одной переменной — исходная причина «дал ключ, ничего
        # не появилось» (audit-11, OR-003). Молчать о нём нельзя, а угадывать,
        # какое значение владелец имел в виду, — тем более.
        return Check("cloud-providers", WARN, conflict,
                     f"Оставьте одно значение; каноническое имя — {OPENROUTER_KEY_ENV}", facts)
    if present and disabled:
        # Ключ есть, а бэкенд в yaml выключен: Gateway подчиняется оператору и
        # молча остаётся без облака. Единственное место, где это видно заранее.
        return Check("cloud-providers", WARN,
                     f"ключ задан ({', '.join(present)}), но в конфигурации Gateway "
                     f"выключены бэкенды: {', '.join(disabled)}",
                     "Уберите enabled: false у этого бэкенда в config/gateway.yaml "
                     "(или удалите блок целиком — он поднимется по ключу)",
                     facts)
    if not present:
        return Check("cloud-providers", WARN,
                     f"облачных ключей нет ({OPENROUTER_KEY_ENV}, {ZAI_KEY_ENV} пусты); "
                     f"работать можно только на локальных моделях",
                     f"Задайте {OPENROUTER_KEY_ENV} в bossman-core/.env "
                     f"и проверьте: bossman models list --provider openrouter",
                     facts)
    return Check("cloud-providers", PASS,
                 f"ключи заданы: {', '.join(present)}; модель GLM у Z.ai: "
                 f"{glm_model_id()} ({GLM_MODEL_ENV})",
                 facts=facts)


def check_telemetry_corpus() -> Check:
    """Куда попадут выборки реальных нагрузок сегодня вечером."""
    sys.path[:0] = [str(REPO / "bossman-core"), str(REPO)]
    try:
        from bossman_v3.execution import telemetry as tm
    except Exception as exc:  # noqa: BLE001
        return Check("telemetry", BLOCKED, f"модуль телеметрии недоступен: {exc}",
                     "python -m pip install -e bossman-core")
    root = tm.corpus_root()
    try:
        path = tm.corpus_path(root)
    except Exception as exc:  # noqa: BLE001
        return Check("telemetry", BLOCKED, f"корпус нагрузок недоступен: {exc}",
                     f"Задайте {tm.ENV_ROOT}=<доступный каталог>")
    existing = 0
    if path.exists():
        existing = sum(1 for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
                       if line.strip())
    return Check("telemetry", PASS,
                 f"выборки реальных нагрузок пишутся в {path} (сейчас записей: {existing})",
                 facts={"path": str(path), "records": existing, "env": tm.ENV_ROOT})


CHECKS: list[Callable[[], Check]] = [
    check_python, check_python_packages, check_bossman_packages, check_node, check_ffmpeg,
    check_state_dir, check_evidence_key, check_journal_anchor, check_browser_runtime,
    check_model_endpoint, check_openhands, check_hardware, check_windows_specific, check_computer_operator_deps,
    check_gateway_url, check_cloud_providers, check_telemetry_corpus,
]


def run_checks(port: int) -> list[Check]:
    results: list[Check] = []
    for fn in CHECKS:
        try:
            results.append(fn())
        except Exception as exc:  # noqa: BLE001 — доктор не имеет права падать сам
            results.append(Check(getattr(fn, "__name__", "check"), BLOCKED,
                                 f"проверка упала: {type(exc).__name__}: {exc}",
                                 "Это дефект самого доктора; сообщите вместе с трассировкой"))
    results.append(check_port(port))
    return results


def render(results: list[Check]) -> str:
    icons = {PASS: "PASS ", WARN: "WARN ", BLOCKED: "BLOCK"}
    width = max(len(r.name) for r in results)
    lines = ["", "BOSSMAN DOCTOR", "=" * 62]
    for group in (BLOCKED, WARN, PASS):
        rows = [r for r in results if r.status == group]
        if not rows:
            continue
        lines.append("")
        for r in rows:
            lines.append(f"[{icons[r.status]}] {r.name.ljust(width)}  {r.detail}")
            if r.remedy and r.status != PASS:
                lines.append(f"{' ' * (width + 10)}→ {r.remedy}")
    blocked = sum(1 for r in results if r.status == BLOCKED)
    warned = sum(1 for r in results if r.status == WARN)
    passed = sum(1 for r in results if r.status == PASS)
    lines += ["", "=" * 62,
              f"PASS {passed}   WARN {warned}   BLOCKED {blocked}", ""]
    lines.append("Приёмку можно начинать." if not blocked
                 else "Приёмку начинать НЕЛЬЗЯ: сначала закройте BLOCKED.")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bossman doctor", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="машиночитаемый отчёт вместо таблицы")
    parser.add_argument("--json-out", type=Path, default=None, help="дополнительно записать JSON в файл")
    parser.add_argument("--port", type=int, default=int(os.environ.get("BCC_PORT", "8800")))
    args = parser.parse_args(argv)

    results = run_checks(args.port)
    report = {"schema_version": 1, "platform": platform.platform(),
              "python": platform.python_version(),
              "checks": [{"name": r.name, "status": r.status, "detail": r.detail,
                          "remedy": r.remedy, "facts": r.facts} for r in results],
              "blocked": sum(1 for r in results if r.status == BLOCKED),
              "warn": sum(1 for r in results if r.status == WARN),
              "pass": sum(1 for r in results if r.status == PASS)}
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                 encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False) if args.json else render(results))
    return 1 if report["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
