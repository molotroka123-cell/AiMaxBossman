"""Сторож настольного запуска: объясняет владельцу, что случилось с окном.

Флаг ``BOSSMAN_WATCHDOG_ENABLED`` (по умолчанию OFF) включает только
периодическую проверку и запись истории. Чтение (``GET /watchdog`` и
``GET /watchdog/history``) работает всегда: посмотреть на своё приложение —
не изменение состояния, а при выключенном флаге история просто пуста.

Зачем это вообще нужно. Жалобы владельца звучат так: «нажимаю на ярлык,
открывается только командная строка» и «окно закрылось само через три минуты».
Следы обоих случаев уже есть — ``desktop-run.log`` и ``desktop.lock`` в каталоге
данных пишет ``bcc.desktop``, — но читать их владелец не обязан и не будет.
Сторож читает те же следы и переводит их на человеческий язык.

Правило формулировок: поля ``problem``, ``evidence`` и ``fix`` пишутся для
человека, который наш код не видел, — там нет ни имён записей журнала, ни
служебных терминов. Машинные подробности живут в двух отдельных полях:
``code`` (для UI и тестов) и ``source`` (путь к файлу-улике, чтобы владельцу
было что переслать в поддержку).

Что сторож умеет находить:

  * прошлый запуск убит и не убрал за собой служебную отметку о работающем окне;
  * окно закрылось меньше чем через 10 секунд — второй Chrome на том же профиле
    или краш (порог тот же, что у ``desktop.run``, который печатает про это в
    консоль — но консоли под ярлыком нет, и предупреждение пропадало);
  * запуск начался и оборвался, не сказав ни слова о результате;
  * окно вообще не запустилось (не нашёлся браузер / не удалось его запустить);
  * сервер не поднялся;
  * на настроенном адресе отвечает чужое приложение;
  * на настроенном адресе не отвечает никто.

Здоровое состояние обязано давать ПУСТОЙ список: сторож, который всегда что-то
нашёл, владелец перестанет читать на второй день.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from fastapi import APIRouter, Request

# Раскладку файлов и проверку живости процесса берём у самого лаунчера: сторож
# обязан смотреть ровно туда же, куда пишет desktop.run, и считать процесс живым
# по тому же правилу (на Windows os.kill(pid, 0) не проверяет, а убивает).
from .. import desktop
from ..db import utcnow
from . import Feature

FLAG = "BOSSMAN_WATCHDOG_ENABLED"
router = APIRouter()

# Тот же порог, по которому desktop.run считает закрытие окна мгновенным.
SHORT_LIFE_SECONDS = 10.0
HISTORY_NAME = "watchdog-history.jsonl"
HISTORY_LIMIT = 200          # сколько записей храним; журнал сторожа не должен расти вечно
LOG_TAIL_BYTES = 200_000     # разбираем только хвост журнала запусков

CRITICAL, WARNING = "critical", "warning"
_SEVERITY_ORDER = {CRITICAL: 0, WARNING: 1, "info": 2}


def enabled() -> bool:
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes")


@dataclass
class Finding:
    """Одна находка. problem/evidence/fix — человеческий язык, без терминов кода."""

    code: str          # машинный идентификатор для UI и тестов
    severity: str      # critical | warning | info
    problem: str       # что случилось
    evidence: str      # на чём это видно
    fix: str           # что сделать владельцу
    source: str = ""   # путь к файлу-улике (переслать в поддержку)


# ── следы на диске ──────────────────────────────────────────────────────────

_LINE_RE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)\s+(.*)$")
_LIFETIME_RE = re.compile(r"lifetime=([0-9]+(?:\.[0-9]+)?)s")
# Записи, которыми запуск заканчивается. Всё остальное (очистка чужой отметки,
# команда браузера, подмена консоли) — середина запуска, а не его исход.
_OUTCOMES = ("browser-exit", "exit code=", "browser-launch-failed", "refused-second-window")


def _run_log_path(data_dir: Path) -> Path:
    return Path(data_dir) / "desktop-run.log"


def _lock_path(data_dir: Path) -> Path:
    return Path(data_dir) / "desktop.lock"


def _read_run_log(path: Path) -> list[tuple[str, str]]:
    """Хвост журнала запусков как пары (время, запись).

    Читаем с конца: журнал живёт годами, а интересен всегда последний запуск.
    Обрезанная первая строка не подойдёт под шаблон времени и просто отпадёт.
    """
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - LOG_TAIL_BYTES))
            raw = fh.read()
    except OSError:
        return []
    out: list[tuple[str, str]] = []
    for line in raw.decode("utf-8", "replace").splitlines():
        m = _LINE_RE.match(line.strip())
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def _last_session(entries: list[tuple[str, str]]) -> tuple[str, str | None] | None:
    """Последний запуск: время его начала и запись об исходе (или None, если исхода нет)."""
    start_at: str | None = None
    outcome: str | None = None
    for ts, msg in entries:
        if msg.startswith("start "):
            start_at, outcome = ts, None
        elif start_at is not None and msg.startswith(_OUTCOMES):
            outcome = msg
    return None if start_at is None else (start_at, outcome)


def _when(ts: str) -> str:
    """Метка времени журнала в вид, который читается вслух."""
    date, _, clock = ts.partition("T")
    return f"{date} в {clock}" if clock else ts


# ── кто отвечает по адресу ──────────────────────────────────────────────────

def _probe_port(base_url: str, timeout: float = 1.5) -> str:
    """Кто слушает адрес: "ours" (наш Command Center), "foreign" или "silent".

    Прокси из окружения здесь ядовит: адрес локальный, но прокси перехватит
    запрос и ответит за него — сторож обвинил бы чужое приложение в том, чего
    не было, или объявил бы мёртвым живой сервер. Поэтому opener строится с
    пустым ProxyHandler — тем же приёмом, что desktop.identify_server.
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(base_url.rstrip("/") + "/api/identity", timeout=timeout) as resp:
            body = resp.read(64_000)
    except urllib.error.HTTPError:
        return "foreign"       # ответило, но не тем — значит, там чужое приложение
    except (urllib.error.URLError, OSError, ValueError):
        return "silent"
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return "foreign"
    return "ours" if isinstance(data, dict) and data.get("app") == desktop.APP_IDENTITY else "foreign"


def _address(settings) -> str:
    return f"http://{settings.host}:{settings.port}/"


# ── сами находки ────────────────────────────────────────────────────────────

def _lock_owner_alive(data_dir: Path) -> bool | None:
    """Жив ли процесс, оставивший отметку о работающем окне.

    None — отметки нет или она нечитаема: рассказывать про неё нечего.
    """
    try:
        data = json.loads(_lock_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        owner = int(data.get("pid", 0))
    except (TypeError, ValueError):
        owner = 0
    return desktop._pid_alive(owner)


def _abandoned_marker_finding(data_dir: Path) -> Finding:
    """Отметка о работающем окне есть, а процесса, который её оставил, нет."""
    return Finding(
        code="previous_run_killed",
        severity=WARNING,
        problem="Прошлый раз BOSSMAN закрылся не сам, а был прерван снаружи: приложение не успело "
                "прибраться и оставило в каталоге данных отметку о якобы работающем окне.",
        evidence="Отметка о работающем окне на месте, а программы, которая её оставила, "
                 "в системе больше нет.",
        fix="Само по себе это окно не сломает: при следующем запуске BOSSMAN уберёт отметку. "
            "Но если так повторяется каждый раз — значит приложение закрывают через диспетчер "
            "задач или вместе с чёрным окном, которое открывается рядом. Закрывайте BOSSMAN "
            "крестиком его собственного окна.",
        source=str(_lock_path(data_dir)),
    )


def _log_findings(data_dir: Path, window_open: bool) -> list[Finding]:
    """Что рассказывает журнал запусков про самый последний запуск."""
    path = _run_log_path(data_dir)
    session = _last_session(_read_run_log(path))
    if session is None:
        return []              # приложение ещё ни разу не запускали — рассказывать нечего
    started_at, outcome = session
    log = str(path)

    if outcome is None:
        if window_open:
            return []          # запуск идёт прямо сейчас, исход ещё не наступил
        return [Finding(
            code="start_without_outcome",
            severity=WARNING,
            problem="Запуск BOSSMAN начался и оборвался на полуслове: чем он кончился, "
                    "приложение сказать не успело.",
            evidence=f"В журнале запусков есть начало запуска {_when(started_at)} и ни одной "
                     "записи о том, чем он закончился.",
            fix="Так выглядит закрытое чёрное окно, которое открывается рядом с BOSSMAN, или "
                "выключение компьютера в момент запуска. Запустите BOSSMAN заново и чёрное окно "
                "не закрывайте — вместе с ним закрывается и само приложение.",
            source=log,
        )]

    if outcome.startswith("browser-exit"):
        m = _LIFETIME_RE.search(outcome)
        life = float(m.group(1)) if m else None
        if life is not None and life < SHORT_LIFE_SECONDS:
            return [Finding(
                code="window_closed_instantly",
                severity=CRITICAL,
                problem=f"Окно BOSSMAN открылось и тут же закрылось само — оно прожило "
                        f"{life:.1f} секунды.",
                evidence=f"Запуск {_when(started_at)}: приложение записало, что окно закрылось "
                         f"через {life:.1f} с после открытия.",
                fix="Чаще всего так бывает, когда тот же браузер уже открыт с этим же профилем: "
                    "второе окно закрывается молча. Закройте все окна Chrome и запустите BOSSMAN "
                    "заново. Если повторится — пришлите в поддержку журнал запусков и файл "
                    "chrome_debug.log из каталога профиля окна.",
                source=log,
            )]
        return []

    if outcome.startswith("browser-launch-failed"):
        return [Finding(
            code="window_did_not_open",
            severity=CRITICAL,
            problem="Окно BOSSMAN не открылось совсем: программу окна не удалось запустить. "
                    "Владелец при этом видит только чёрное окно и больше ничего.",
            evidence=f"Запуск {_when(started_at)}: приложение записало отказ при попытке "
                     "открыть окно.",
            fix="Проверьте, что на компьютере установлен Chrome (или Edge), и укажите путь к нему "
                "явно: добавьте в ярлык BOSSMAN параметр --browser с путём к chrome.exe.",
            source=log,
        )]

    if outcome.startswith("exit code=2"):
        return [Finding(
            code="browser_not_found",
            severity=CRITICAL,
            problem="BOSSMAN не нашёл на компьютере ни одного браузера, в котором можно открыть "
                    "своё окно, и закрылся сразу после запуска.",
            evidence=f"Запуск {_when(started_at)}: приложение записало, что подходящей программы "
                     "для окна на компьютере нет.",
            fix="Установите Google Chrome или Microsoft Edge — либо укажите путь к уже "
                "установленному браузеру параметром --browser в ярлыке BOSSMAN. Пока браузера "
                "нет, BOSSMAN открывается обычной ссылкой в любом браузере.",
            source=log,
        )]

    if outcome.startswith("exit code=3"):
        return [Finding(
            code="server_did_not_start",
            severity=CRITICAL,
            problem="Сам BOSSMAN в прошлый раз не запустился: его внутренняя часть, которая "
                    "готовит страницы, не поднялась, и окно открывать было незачем.",
            evidence=f"Запуск {_when(started_at)}: приложение записало, что не смогло "
                     "подготовиться к работе, и назвало причину.",
            fix="Откройте журнал запусков (путь ниже) и посмотрите последнюю строку — там "
                "написана причина словами. Чаще всего адрес занят другой программой: тогда "
                "запустите BOSSMAN на другом номере адреса параметром --port.",
            source=log,
        )]

    return []


def _address_finding(base_url: str, verdict: str) -> Finding | None:
    if verdict == "ours":
        return None
    if verdict == "foreign":
        return Finding(
            code="address_taken_by_other_app",
            severity=CRITICAL,
            problem=f"По адресу, на котором должен работать BOSSMAN ({base_url}), отвечает другая "
                    "программа. Окно, открытое по этому адресу, покажет её, а не BOSSMAN.",
            evidence=f"На запрос по адресу {base_url} пришёл ответ, но это ответ не BOSSMAN.",
            fix="Закройте программу, которая заняла этот адрес, либо запустите BOSSMAN на другом "
                "номере адреса: добавьте в ярлык параметр --port с другим числом.",
        )
    return Finding(
        code="address_silent",
        severity=CRITICAL,
        problem=f"По адресу, который записан у BOSSMAN в настройках ({base_url}), не отвечает "
                "никто. Ярлык на рабочем столе откроет пустое окно.",
        evidence=f"На запрос по адресу {base_url} не ответили вообще — соединение не состоялось.",
        fix="Запустите BOSSMAN заново. Если адрес в настройках менялся вручную, верните прежний: "
            "окно ищет приложение именно по нему.",
    )


def collect(data_dir: Path, base_url: str) -> list[Finding]:
    """Все находки на текущий момент. Пустой список = всё в порядке."""
    data_dir = Path(data_dir)
    owner_alive = _lock_owner_alive(data_dir)
    findings: list[Finding] = []
    if owner_alive is False:
        findings.append(_abandoned_marker_finding(data_dir))
    # Живая отметка означает «запуск идёт прямо сейчас»: журнал без записи об
    # исходе тогда не беда, а нормальная середина работающего окна.
    findings += _log_findings(data_dir, owner_alive is True)
    address = _address_finding(base_url, _probe_port(base_url))
    if address is not None:
        findings.append(address)
    findings.sort(key=lambda f: _SEVERITY_ORDER.get(f.severity, 3))
    return findings


# ── история ─────────────────────────────────────────────────────────────────

def _history_path(data_dir: Path) -> Path:
    return Path(data_dir) / HISTORY_NAME


def read_history(data_dir: Path, limit: int = 50) -> list[dict]:
    try:
        lines = _history_path(data_dir).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for line in lines[-max(1, limit):]:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def _append_history(data_dir: Path, findings: list[Finding]) -> bool:
    """Пишем, только когда картина изменилась.

    Одна и та же беда каждую минуту превращает историю в шум, в котором не
    видно, когда именно она началась. Возвращает True, если запись добавлена.
    """
    codes = sorted(f.code for f in findings)
    previous = read_history(data_dir, limit=1)
    if previous and previous[-1].get("codes") == codes:
        return False
    entry = {"at": utcnow().isoformat(), "codes": codes,
             "findings": [asdict(f) for f in findings]}
    path = _history_path(data_dir)
    try:
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        return False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > HISTORY_LIMIT:
            path.write_text("\n".join(lines[-HISTORY_LIMIT:]) + "\n", encoding="utf-8")
    except OSError:
        pass
    return True


# ── ручки и периодическая проверка ──────────────────────────────────────────

@router.get("/watchdog")
async def watchdog(request: Request):
    """Что со стартовым окном прямо сейчас. Читающая ручка: работает и при OFF."""
    svc = request.app.state.svc
    # Проверка адреса блокирует поток на время ожидания ответа — держать на этом
    # цикл событий нельзя, иначе одна зависшая проверка тормозит всё приложение.
    findings = await asyncio.to_thread(collect, Path(svc.settings.data_dir), _address(svc.settings))
    return {"enabled": enabled(), "checked_at": utcnow().isoformat(),
            "address": _address(svc.settings), "healthy": not findings,
            "findings": [asdict(f) for f in findings]}


@router.get("/watchdog/history")
async def history(request: Request, limit: int = 50):
    """Что сторож находил раньше. История копится только при включённом флаге."""
    svc = request.app.state.svc
    entries = read_history(Path(svc.settings.data_dir), limit=limit)
    return {"enabled": enabled(), "entries": entries, "count": len(entries)}


async def _tick(svc):
    """Периодическая проверка. При выключенном флаге не делает ничего вообще."""
    if not enabled():
        return
    data_dir = Path(svc.settings.data_dir)
    findings = await asyncio.to_thread(collect, data_dir, _address(svc.settings))
    if not await asyncio.to_thread(_append_history, data_dir, findings):
        return                 # картина та же, что и в прошлый раз — молчим
    for f in findings:
        await svc.bus.emit("watchdog.finding", code=f.code, severity=f.severity, problem=f.problem)
    if not findings:
        await svc.bus.emit("watchdog.clear")


FEATURE = Feature(name="watchdog", router=router, tick=_tick, tick_seconds=60.0)
