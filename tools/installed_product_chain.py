#!/usr/bin/env python3
"""Цепочка УСТАНОВЛЕННОГО продукта, звено за звеном, без рабочей копии.

    чистая установка → доктор → запуск → HTTP готов → UI готов
                     → сценарий → перезапуск → возобновление

Запускается ИНТЕРПРЕТАТОРОМ УСТАНОВКИ и только им::

    <venv>/bin/python -I tools/installed_product_chain.py --json out.json

`-I` обязателен: он выключает PYTHONPATH и каталог пользователя, поэтому
`bcc`, `bossman` и `bossman_shared` могут прийти ТОЛЬКО из установки. Первое
звено это не предполагает, а ДОКАЗЫВАЕТ и умеет из-за этого покраснеть — см.
`editable_problems`. До сих пор ни один прогон этого не измерял: чистые
установки полагались на то, что `PYTHONPATH` удалён, и сообщали PASS, ни разу
не назвав, откуда пришёл `bcc`.

Чем это НЕ является и что здесь не повторяется:

* `tools/verify_installed_product.py` — детерминированная загрузка установки,
  выдача статики и сохранность после ОСТАНОВКИ. Его улика об интерфейсе —
  `<html` в ответе на `/`, то есть двести на корень; звено `UI готов` здесь
  существует ровно потому, что страница — это не двести на корень;
* `tools/bundle_evening_test.py` — вечерняя приёмка СКАЧАННОГО АРХИВА. Схема
  отчёта доктора берётся оттуда импортом, а не переписывается рядом;
* `scripts/windows_owner_tasks.py` — три владельческие задачи и аудит
  подтверждённых команд после жёсткого убийства. Здесь убийство тоже жёсткое,
  но мерится ДРУГОЕ: продолжился ли начатый документ с той же версии или
  начался сначала.

Коды выхода: 0 — PASS, 1 — FAIL, 2 — OWNER_HARDWARE_REQUIRED.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import importlib
import importlib.metadata as md
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import sysconfig
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PASS, FAIL, OWNER_HW, NOT_RUN = "PASS", "FAIL", "OWNER_HARDWARE_REQUIRED", "NOT_RUN"
EXIT_CODES = {PASS: 0, FAIL: 1, OWNER_HW: 2}
HEX40 = re.compile(r"[0-9a-f]{40}\Z")

#: Пакеты продукта. Ни один из них не имеет права прийти из чекаута.
PRODUCT_MODULES = ("bossman_shared", "bossman", "bossman_v3", "bcc")
#: Дистрибутивы, у которых проверяется СПОСОБ установки.
PRODUCT_DISTRIBUTIONS = ("bossman-shared", "bossman-core", "bossman-command-center")
#: Подсистемы, за которые продукт отвечает САМ. Тот же список, что и у
#: владельческого прогона: расширять его здесь значило бы завести свой, более
#: строгий критерий и покрасить в красный честный ответ продукта.
CONTRACTED = ("db", "queue_worker", "scheduler", "metrics")
#: Каталоги рабочей копии, присутствие которых на пути импорта означает, что
#: проверяется чекаут, а не поставка.
CHECKOUT_MARKERS = (("command-center", "bcc"), ("bossman-core", "bossman"))
#: Сколько доступных органов управления ЛЕЖИТ В РАЗМЕТКЕ `#shell` поставляемого
#: index.html: 6 кнопок, 1 ссылка, 1 поле ввода. Число не выдумано и не
#: «на глаз» — оно посчитано в самом файле, и
#: tests/test_installed_product_chain.py пересчитывает его заново, поэтому
#: молча разойтись с поставкой оно не может.
#:
#: Это ПОЛ, а не цель. Всё остальное отрисовывает app.js, и сколько именно —
#: заранее неизвестно; поэтому настоящая проверка «страница поднялась» тут не
#: счётчик, а `#view`: в поставляемой разметке главная область ПУСТА
#: (`<main class="view" id="view"></main>`), и текст в ней появляется, только
#: если приложение действительно отрисовало страницу.
SHIPPED_SHELL_CONTROLS = 8


def _console_utf8() -> None:
    """Печать не имеет права падать на кириллице.

    `-I` подразумевает `-E`, поэтому PYTHONUTF8 и PYTHONIOENCODING
    игнорируются, а на Windows поток получает кодировку локали. Так закончился
    прогон 132 на настоящей Windows — UnicodeEncodeError в печати отчёта.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


# =========================================================================
# ЧИСТЫЕ ФУНКЦИИ ВЕРДИКТА
# Вынесены отдельно, потому что именно они выносят приговор, и их самих надо
# уметь провалить: tests/test_installed_product_chain.py подсовывает каждой
# настоящий плохой случай и требует, чтобы она его назвала. Проверка, которая
# не может провалиться, ничего не проверяет.
# =========================================================================

def editable_problems(*, module_paths: dict, prefix: str, direct_urls: dict,
                      site_entries: list, sys_path: list) -> list:
    """Всё, что делает «установку» пересказом рабочей копии.

    Возвращает список причин; пустой список — установка настоящая.

    Проверяются ЧЕТЫРЕ независимых признака, потому что каждый по отдельности
    обходится: модуль вне префикса; `direct_url.json` с ``editable: true``;
    хвост ``__editable__*`` в site-packages (так setuptools реализует правку на
    месте); каталог рабочей копии на пути импорта.
    """
    problems: list = []
    root = Path(prefix).resolve()
    for name, location in sorted(module_paths.items()):
        if location is None:
            problems.append(f"{name}: пакет не импортируется из установки вообще")
            continue
        path = Path(location).resolve()
        if not path.is_relative_to(root):
            problems.append(f"{name}: импортирован мимо установки — {path}")
    for name, info in sorted(direct_urls.items()):
        if not info:
            continue
        if bool((info.get("dir_info") or {}).get("editable")):
            problems.append(f"{name}: установлен как editable (direct_url.json)")
    for entry in sorted(site_entries):
        if str(entry).startswith("__editable__"):
            problems.append(f"в site-packages лежит хвост editable-установки: {entry}")
    for entry in sys_path:
        if not entry:
            continue
        candidate = Path(entry)
        if any((candidate / outer / inner).is_dir() for outer, inner in CHECKOUT_MARKERS):
            problems.append(f"на пути импорта лежит рабочая копия: {entry}")
    return problems


def build_json_problems(build: dict, expected_sha: str | None) -> list:
    """``bcc/_build.json`` — единственная привязка установки к коммиту."""
    problems: list = []
    sha = build.get("source_sha")
    if not isinstance(sha, str) or not HEX40.fullmatch(sha):
        problems.append(f"установка не называет свой коммит: source_sha={sha!r}")
    if build.get("source_dirty") is not False:
        problems.append("установка собрана из грязного дерева: "
                        f"source_dirty={build.get('source_dirty')!r}")
    if expected_sha and sha != expected_sha:
        problems.append(f"установка собрана из {sha!r}, а улика требуется для {expected_sha!r}")
    return problems


def ui_problems(*, login_visible: bool, shell_visible: bool, build_sha_text: str,
                build_sha_title: str, expected_sha: str | None, controls: int,
                min_controls: int, view_text: str, console_errors: list,
                failed_responses: list) -> list:
    """Что делает отданную страницу НЕ готовым интерфейсом.

    Двести на корень сюда не проходит по построению: `#login` и `#shell` в
    index.html объявлены ``hidden``, снять этот атрибут может только
    отработавший app.js. Страница, на которой скрипт не выполнился, покажет
    ровно ничего — и здесь это красное, а не «сервер ответил».

    Подсказка у `#build-sha` заполняется из `/api/identity` и прямо различает
    «установленная сборка» и «рабочий чекаут». Граница между поставкой и
    рабочей копией проверяется ТЕМ ЖЕ, что видит владелец глазами.
    """
    problems: list = []
    if not login_visible:
        problems.append("экран входа не появился: app.js не снял hidden с #login")
    if not shell_visible:
        problems.append("оболочка не появилась после входа: #shell остался hidden")
    if not build_sha_text.startswith("сборка "):
        problems.append(f"интерфейс не назвал работающую сборку: #build-sha = {build_sha_text!r}")
    if "установленная сборка" not in build_sha_title:
        problems.append("интерфейс показывает рабочий чекаут, а не установленную сборку: "
                        f"подсказка #build-sha = {build_sha_title!r}")
    if expected_sha and expected_sha not in build_sha_title:
        problems.append(f"интерфейс называет чужой коммит: подсказка {build_sha_title!r}")
    if controls < min_controls:
        problems.append(f"на отрисованной оболочке {controls} доступных органов управления "
                        f"при пороге {min_controls}: страница поднялась не целиком")
    if not view_text.strip():
        problems.append("главная область #view пуста: оболочку показали, но страницу так и не "
                        "отрисовали — в поставляемой разметке #view пуст, текст в нём появляется "
                        "только от отработавшего приложения")
    for message in console_errors:
        problems.append(f"ошибка в консоли страницы: {message}")
    for item in failed_responses:
        problems.append(f"страница не получила свой ресурс: {item}")
    return problems


def resume_problems(*, version_before: int, version_after_restart: int,
                    body_before: str, body_after_restart: str,
                    version_continued: int, stale_write_status: int | None) -> list:
    """Работа ПРОДОЛЖИЛАСЬ, а не началась сначала.

    Мерится счётчиком версий документа владельца, который продукт ведёт сам:

    * после перезапуска версия та же, что была до убийства — ничего не
      откатилось и не обнулилось;
    * тело документа то же — перезапуск не потерял последнюю правку;
    * следующая правка даёт версию+1, а не 1 — счётчик продолжился;
    * запись по УСТАРЕВШЕЙ версии по-прежнему отвергается. Без этой пары
      «счётчик растёт» доказывался бы счётчиком, который принимает что угодно,
      и проверка не смогла бы провалиться.
    """
    problems: list = []
    if version_after_restart != version_before:
        problems.append(f"после перезапуска версия документа {version_after_restart}, "
                        f"до убийства была {version_before}: работа не продолжилась")
    if body_after_restart != body_before:
        problems.append("после перезапуска тело документа отличается от последней правки "
                        "до убийства")
    if version_continued != version_before + 1:
        problems.append(f"следующая правка дала версию {version_continued}, ожидалась "
                        f"{version_before + 1}: продукт начал документ сначала")
    if stale_write_status is None:
        problems.append("отрицательный контроль не выполнялся: запись по устаревшей версии "
                        "не пробовали")
    elif stale_write_status < 400:
        problems.append(f"запись по УСТАРЕВШЕЙ версии принята с кодом {stale_write_status}: "
                        "счётчик версий ничего не сторожит, и рост версии ничего не доказывает")
    return problems


def chain_verdict(links: list) -> str:
    """Один вердикт из звеньев. NOT_RUN в обязательном звене — это FAIL.

    Незапущенная проверка строже не становится; её молчание читается как
    «замечаний нет». Единственные звенья, которым законно не запуститься,
    объявлены ``owner_hardware=True``.
    """
    if any(link["status"] == FAIL for link in links):
        return FAIL
    for link in links:
        if link["status"] == NOT_RUN and not link.get("owner_hardware"):
            return FAIL
    if any(link["status"] in (OWNER_HW, NOT_RUN) for link in links):
        return OWNER_HW
    return PASS


# =========================================================================
# КЛИЕНТ И ПРОЦЕСС
# =========================================================================

class Client:
    """HTTP-клиент владельца: cookie-сессия и CSRF, как в браузере."""

    def __init__(self, port: int) -> None:
        self.base = f"http://127.0.0.1:{port}"
        self.csrf: str | None = None
        self.status: int | None = None
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def __call__(self, path, method="GET", payload=None, expected=200, raw=False):
        headers = {}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if self.csrf:
            headers["X-BCC-CSRF"] = self.csrf
        request = urllib.request.Request(
            self.base + path, method=method, headers=headers,
            data=json.dumps(payload).encode() if payload is not None else None)
        try:
            response = self.opener.open(request, timeout=30)
        except urllib.error.HTTPError as exc:
            response = exc
        body = response.read()
        self.status = response.status
        if expected is not None and response.status != expected:
            raise AssertionError(f"{method} {path} -> {response.status}: {body[:400]!r}")
        return body if raw else json.loads(body)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _server_env(work: Path) -> dict:
    env = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME", "BCC_UI_DIR", "DATABASE_URL"):
        env.pop(key, None)
    env.update(BCC_DATA_DIR=str(work / "data"), BCC_TOKEN_STDOUT="0",
               PYTHONUNBUFFERED="1", PYTHONUTF8="1")
    return env


def start_installed(work: Path, timeout: float = 120.0):
    """Поднять установленный продукт. Рабочей копии на пути импорта нет."""
    port = _free_port()
    log = (work / "server.log").open("ab")
    process = subprocess.Popen(
        [sys.executable, "-I", "-m", "bcc", "--host", "127.0.0.1", "--port", str(port)],
        env=_server_env(work), cwd=work, stdout=log, stderr=subprocess.STDOUT)
    client = Client(port)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            log.close()
            raise AssertionError("установленный продукт упал при старте:\n"
                                 + (work / "server.log").read_text(errors="replace")[-4000:])
        try:
            client("/api/identity")
            return process, client, log
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            time.sleep(0.2)
    process.kill()
    log.close()
    raise AssertionError(f"установленный продукт не поднялся за {timeout:g} с")


def login(client: Client, work: Path) -> str:
    """Полный владельческий вход. Токен НИКОГДА не попадает в отчёт и журнал."""
    client("/api/system", expected=401)
    token = (work / "data" / "token").read_text(encoding="utf-8").strip()
    client("/api/login", method="POST", payload={"token": "installed-chain-probe"}, expected=401)
    client.csrf = client("/api/login", method="POST", payload={"token": token})["csrf"]
    return token


def wait_healthy(client: Client, timeout: float = 60.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        health = client("/api/system")["health"]
        need = [*CONTRACTED, *(k for k in health if k.startswith("tick:"))]
        bad = {k: (health[k].get("status") if isinstance(health.get(k), dict) else "missing")
               for k in need}
        bad = {k: v for k, v in bad.items() if v != "ok"}
        if not bad:
            return health
        if time.monotonic() >= deadline:
            raise AssertionError(f"обещанные подсистемы не поднялись за {timeout:g} с: {bad}")
        time.sleep(0.5)


def hard_kill(process, log, port: int, timeout: float = 30.0) -> dict:
    """Убить процесс, а не попросить его выйти.

    POSIX — SIGKILL, Windows — TerminateProcess. Обработчиков нет ни там, ни
    там: сервер не получает шанса аккуратно закрыть базу, и именно это надо
    пережить. `terminate()` на POSIX — это SIGTERM, то есть вежливая просьба,
    и перезапуск после неё доказывает существенно меньше.

    Смерть проверяется ДВУМЯ способами, потому что первый есть не везде:
    сигналом нулевой длины по PID (только POSIX) и тем, что прежний порт
    больше никто не слушает (везде). На Windows остаётся только второй, и
    отчёт не имеет права делать вид, что проверил оба.
    """
    pid = process.pid
    process.kill()
    code = process.wait(timeout=timeout)
    log.close()
    alive = None
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            alive = True
        except OSError:
            alive = False
    deadline = time.monotonic() + 20
    port_open = True
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(1.0)
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                port_open = False
                break
        time.sleep(0.2)
    return {"pid": pid, "код_выхода": code, "жив_после_убийства": alive,
            "прежний_порт_слушают": port_open,
            "как_проверена_смерть": ("сигнал нулевой длины по PID и молчание прежнего порта"
                                     if alive is not None else "молчание прежнего порта")}


# =========================================================================
# ЗВЕНЬЯ
# =========================================================================

class Chain:
    def __init__(self, evidence: Path) -> None:
        self.links: list = []
        self.evidence = evidence

    def record(self, name, status, note, **extra) -> dict:
        link = {"звено": name, "status": status, "улика": note, **extra}
        self.links.append(link)
        print(f"[{status:24}] {name}: {note}", flush=True)
        return link

    def run(self, name, fn, *, owner_hardware=False) -> bool:
        try:
            note = fn()
        except Exception as exc:
            self.record(name, FAIL, f"{type(exc).__name__}: {exc}"[:1500],
                        owner_hardware=owner_hardware)
            return False
        status = PASS
        if isinstance(note, tuple):
            status, note = note
        self.record(name, status, note, owner_hardware=owner_hardware)
        return status != FAIL

    def skip_rest(self, names, reason) -> None:
        for name in names:
            self.record(name, NOT_RUN, reason)


def link_clean_install(expected_sha):
    """ЗДЕСЬ проходит граница между поставкой и рабочей копией — и измеряется."""
    module_paths, direct_urls = {}, {}
    for name in PRODUCT_MODULES:
        try:
            module_paths[name] = importlib.import_module(name).__file__
        except Exception:
            module_paths[name] = None
    for name in PRODUCT_DISTRIBUTIONS:
        try:
            raw = md.distribution(name).read_text("direct_url.json")
            direct_urls[name] = json.loads(raw) if raw else None
        except Exception:
            direct_urls[name] = None
    purelib = sysconfig.get_paths().get("purelib")
    site_entries = []
    if purelib and Path(purelib).is_dir():
        site_entries = [p.name for p in Path(purelib).iterdir()]
    problems = editable_problems(module_paths=module_paths, prefix=sys.prefix,
                                 direct_urls=direct_urls, site_entries=site_entries,
                                 sys_path=list(sys.path))
    build: dict = {}
    build_path = Path(module_paths["bcc"]).with_name("_build.json") if module_paths["bcc"] else None
    if build_path is not None and build_path.is_file():
        build = json.loads(build_path.read_text(encoding="utf-8"))
    else:
        problems.append("у установки нет bcc/_build.json — она не называет свой источник")
    problems += build_json_problems(build, expected_sha)
    if problems:
        raise AssertionError("; ".join(problems))
    return (f"{len(PRODUCT_MODULES)} пакета продукта импортированы из {sys.prefix}, "
            f"{len(PRODUCT_DISTRIBUTIONS)} дистрибутива не editable, "
            f"{len(site_entries)} записей в site-packages без хвостов __editable__, "
            f"установка названа коммитом {build.get('source_sha')} "
            f"(dirty={build.get('source_dirty')})")


def link_doctor(doctor: Path, evidence: Path):
    """Доктор установленного продукта. Схема отчёта — из вечерней приёмки.

    Список обязательных проверок и разделение «виновата машина владельца» /
    «виновата поставка» не переписываются здесь заново: два списка одного
    правила неизбежно разойдутся. Берутся импортом из bundle_evening_test.
    """
    spec = importlib.util.spec_from_file_location(
        "bundle_evening_test", Path(__file__).resolve().with_name("bundle_evening_test.py"))
    evening = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(evening)
    done = subprocess.run([sys.executable, "-I", str(doctor), "--json"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=evening.DOCTOR_TIMEOUT)
    (evidence / "doctor.json").write_text(done.stdout or "", encoding="utf-8")
    (evidence / "doctor.log").write_text(done.stderr or "", encoding="utf-8")
    try:
        report = json.loads(done.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise AssertionError(
            f"доктор вышел с кодом {done.returncode} и не напечатал свой отчёт: "
            f"{type(exc).__name__}; stderr: {(done.stderr or '')[-800:]}") from None
    schema = evening.doctor_schema_problems(report)
    if schema:
        raise AssertionError("отчёт доктора не является отчётом доктора: " + "; ".join(schema))
    checks = {c["name"]: c["status"] for c in report["checks"]}
    blocked = sorted(n for n, s in checks.items() if s == "BLOCKED")
    product_blocked = [n for n in blocked if n not in evening.OWNER_MACHINE_CHECKS]
    if product_blocked:
        raise AssertionError(f"доктор заблокировал то, за что отвечает поставка: {product_blocked}")
    warned = sorted(n for n, s in checks.items() if s == "WARN")
    note = (f"{len(checks)} проверок доктора, все обязательные имена на месте; "
            f"BLOCKED только машинные: {blocked or 'нет'}; WARN: {warned or 'нет'}")
    if blocked:
        return OWNER_HW, note + " — снять блокировку может только владелец на своей машине"
    return note


def link_ui_ready(base, token, expected_sha, *, require_browser, min_controls,
                  evidence: Path, browser_executable=None):
    """Настоящая страница в настоящем Chromium, запущенном РАНТАЙМОМ УСТАНОВКИ.

    Ни рабочей копии, ни editable-контроллера: playwright берётся из той же
    установки, что и `bcc`. Единственная браузерная улика о продукте, которая
    существовала до этого, добывается вторым интерпретатором, в который
    репозиторий поставлен `pip install -e .`; здесь страницу открывает сама
    установка.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        if require_browser:
            raise AssertionError(f"playwright недоступен в установке: {type(exc).__name__}: {exc}")
        return OWNER_HW, f"playwright нет в установке ({type(exc).__name__}); страница НЕ проверена"
    console_errors: list = []
    failed: list = []
    with sync_playwright() as pw:
        try:
            # `--browser-executable` не ослабляет проверку: страница всё равно
            # должна отрисоваться в НАСТОЯЩЕМ браузере. Он нужен там, где
            # браузер уже стоит в системе — на машине владельца и в средах, где
            # загрузка сборок playwright закрыта политикой сети.
            browser = (pw.chromium.launch(executable_path=str(browser_executable))
                       if browser_executable else pw.chromium.launch())
        except Exception as exc:
            if require_browser:
                raise AssertionError(f"Chromium не запустился: {type(exc).__name__}: {exc}")
            return OWNER_HW, f"Chromium недоступен ({type(exc).__name__}); страница НЕ проверена"
        try:
            page = browser.new_page()
            page.on("console",
                    lambda m: console_errors.append(m.text[:300]) if m.type == "error" else None)
            page.on("pageerror", lambda e: console_errors.append(str(e)[:300]))
            page.on("response",
                    lambda r: failed.append(f"{r.status} {r.url[len(base):] or '/'}")
                    if r.url.startswith(base) and r.status >= 400 else None)
            page.goto(base, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector("#login:not([hidden])", timeout=30000)
            login_visible = True
            # Вход делается ФОРМОЙ, как его делает владелец. Токен в отчёт и в
            # журнал не попадает ни здесь, ни ниже.
            page.fill("#login-token", token)
            page.click("#login-submit")
            page.wait_for_selector("#shell:not([hidden])", timeout=30000)
            shell_visible = True
            handle = page.wait_for_function(
                "() => { const e = document.getElementById('build-sha');"
                " return e && e.textContent && e.textContent !== 'сборка…'"
                " ? [e.textContent, e.title] : null; }", timeout=30000)
            build_sha_text, build_sha_title = handle.json_value()
            controls = page.locator(
                "#shell button:not([disabled]), #shell a[href], "
                "#shell input:not([disabled]), #shell select:not([disabled])").count()
            # В поставляемой разметке #view пуст. Текст в нём — единственное
            # доказательство, что приложение отрисовало СТРАНИЦУ, а не только
            # сняло hidden с оболочки.
            page.wait_for_function(
                "() => { const v = document.getElementById('view');"
                " return v && v.innerText.trim().length > 0; }", timeout=30000)
            view_text = page.inner_text("#view")
            page.screenshot(path=str(evidence / "ui-ready.png"))
            (evidence / "ui-ready.html").write_text(page.content(), encoding="utf-8")
        finally:
            browser.close()
    problems = ui_problems(
        login_visible=login_visible, shell_visible=shell_visible,
        build_sha_text=build_sha_text, build_sha_title=build_sha_title,
        expected_sha=expected_sha, controls=controls, min_controls=min_controls,
        view_text=view_text, console_errors=sorted(set(console_errors)),
        failed_responses=sorted(set(failed)))
    if problems:
        raise AssertionError("; ".join(problems))
    return (f"страница отрисована и вход выполнен формой: #login показан, #shell открыт, "
            f"#build-sha = {build_sha_text!r}, {controls} доступных органов управления "
            f"(пол {min_controls} — столько их в поставляемой разметке), "
            f"в #view отрисовано {len(view_text.strip())} символов, "
            f"0 ошибок в консоли, 0 неотданных ресурсов; скриншот ui-ready.png")


def link_desktop_window(evidence: Path):
    """OWNER_HARDWARE_REQUIRED: окна на раннере не бывает — и это ЕДИНСТВЕННОЕ,
    что здесь не проверяется.

    Интерактивного сеанса рабочего стола GitHub не даёт физически, поэтому
    «окно открылось» доказать нечем. Всё, ЧТО ПРОВЕРИТЬ МОЖНО, проверяется и
    не прячется за этой пометкой: точки входа установлены, запускаются и
    отвечают. Звено не имеет права уронить прогон и не имеет права быть
    покрашенным в зелёный.
    """
    bindir = Path(sys.prefix) / ("Scripts" if os.name == "nt" else "bin")
    suffixes = (".exe", ".cmd", "") if os.name == "nt" else ("",)
    found = {}
    for entry in ("bcc", "bcc-desktop", "bcc-open", "bossman", "bossman-gateway"):
        path = next((bindir / (entry + s) for s in suffixes if (bindir / (entry + s)).is_file()),
                    None)
        if path is None:
            raise AssertionError(f"установка не положила команду {entry} в {bindir}")
        argv = [str(path), "--help"]
        if path.suffix == ".cmd":
            argv = [os.environ.get("COMSPEC", "cmd.exe"), "/c", *argv]
        done = subprocess.run(argv, capture_output=True, timeout=60)
        if done.returncode:
            detail = (done.stderr or done.stdout).decode("utf-8", "replace")
            raise AssertionError(f"установленная команда {entry} --help вышла с "
                                 f"{done.returncode}:\n{detail[-2000:]}")
        found[entry] = str(path)
    display = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    reason = ("на Windows-раннере GitHub нет интерактивного сеанса рабочего стола"
              if os.name == "nt" else f"нет графического сеанса: DISPLAY={display!r}")
    (evidence / "desktop-entrypoints.json").write_text(
        json.dumps({"команды": found, "почему_окно_не_проверяется": reason},
                   indent=2, ensure_ascii=False), encoding="utf-8")
    return (OWNER_HW,
            f"{len(found)} точек входа установлены и отвечают на --help; открытие ОКНА "
            f"не проверяется и не заявляется: {reason}. Проверяется на машине владельца.")


def link_scenario(client, state: dict):
    """Владельческая работа, которую видно ПОСЛЕ перезапуска.

    Документ владельца с версионированием: счётчик версий продукт ведёт сам, и
    именно он отличает «продолжили» от «начали сначала».
    """
    project = client("/api/web-designer/projects", method="POST",
                     payload={"name": "installed-chain-document", "template": "blank"})
    state["project"] = project["meta"]["id"]
    version = client(f"/api/web-designer/projects/{state['project']}/visual")["meta"]["version"]
    for step in range(1, 4):
        body = f'<h1 id="chain">правка {step} до убийства</h1>'
        client(f"/api/web-designer/projects/{state['project']}/visual", method="PUT",
               payload={"body": body, "css": "h1{color:#123456}", "base_version": version})
        # Независимое перечитывание: ответ движка — наблюдение, а не факт.
        version = client(f"/api/web-designer/projects/{state['project']}/visual")["meta"]["version"]
        state["body"] = body
    state["version"] = version
    agent = client("/api/agents", method="POST",
                   payload={"name": "installed-chain-agent", "enabled": False})
    state["agent"] = agent["id"]
    return (f"документ {state['project']} доведён до версии {version} тремя правками, "
            f"каждая перечитана отдельным GET; заведён агент {state['agent']}")


def link_restart(state: dict):
    facts = state["kill"]
    if facts["жив_после_убийства"]:
        raise AssertionError(f"процесс {facts['pid']} пережил убийство")
    if facts["прежний_порт_слушают"]:
        raise AssertionError(f"прежний порт {state['port_before']} всё ещё слушают — "
                             "сервер не умер")
    if facts["код_выхода"] == 0:
        raise AssertionError("процесс вышел с нулём — это штатный выход, а не убийство")
    if state["pid_after"] == facts["pid"]:
        raise AssertionError("после перезапуска тот же PID — процесс не поднимался заново")
    if state["started_before"] == state["started_after"]:
        raise AssertionError("продукт сообщает прежнее время старта — процесс тот же")
    return (f"процесс {facts['pid']} убит жёстко (код выхода {facts['код_выхода']}; "
            f"смерть проверена: {facts['как_проверена_смерть']}), поднят заново как "
            f"{state['pid_after']}, started_at сменился")


def link_resume(client, state: dict):
    project = state["project"]
    version_after = client(f"/api/web-designer/projects/{project}/visual")["meta"]["version"]
    code_after = client(f"/api/web-designer/projects/{project}")["code"]
    # Отрицательный контроль: запись по УСТАРЕВШЕЙ версии обязана быть отвергнута.
    # Без него «версия выросла» доказывалось бы счётчиком, принимающим что угодно.
    client(f"/api/web-designer/projects/{project}/visual", method="PUT", expected=None,
           payload={"body": "<h1>подмена по устаревшей версии</h1>", "css": "",
                    "base_version": 1})
    stale_status = client.status
    client(f"/api/web-designer/projects/{project}/visual", method="PUT",
           payload={"body": '<h1 id="chain">правка 4 после убийства</h1>',
                    "css": "h1{color:#123456}", "base_version": version_after})
    continued = client(f"/api/web-designer/projects/{project}/visual")["meta"]["version"]
    problems = resume_problems(
        version_before=state["version"], version_after_restart=version_after,
        body_before=state["body"],
        body_after_restart=state["body"] if state["body"] in code_after else code_after,
        version_continued=continued, stale_write_status=stale_status)
    if not any(a["id"] == state["agent"] for a in client("/api/agents")):
        problems.append(f"агент {state['agent']} исчез после жёсткого убийства")
    if problems:
        raise AssertionError("; ".join(problems))
    return (f"версия {state['version']} пережила убийство без отката, следующая правка дала "
            f"{continued} (а не 1), запись по устаревшей версии отвергнута кодом "
            f"{stale_status}, агент {state['agent']} на месте")


# =========================================================================
# СБОРКА ЦЕПОЧКИ
# =========================================================================

def run_chain(work: Path, evidence: Path, *, expected_sha, doctor, require_browser,
              min_controls, browser_executable=None) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    evidence.mkdir(parents=True, exist_ok=True)
    chain = Chain(evidence)
    later = ["запуск", "HTTP готов", "UI готов", "окно рабочего стола",
             "сценарий", "перезапуск", "возобновление"]

    if not chain.run("чистая установка", lambda: link_clean_install(expected_sha)):
        chain.skip_rest(["доктор", *later],
                        "установка не доказана настоящей — остальное измеряло бы чужой код")
        return finish(chain, evidence, expected_sha)
    chain.run("доктор", lambda: link_doctor(doctor, evidence))

    state: dict = {}
    process = log = None
    try:
        def boot():
            nonlocal process, log
            process, client, log = start_installed(work)
            state["client"] = client
            state["pid_before"] = process.pid
            state["started_before"] = client("/api/identity").get("started_at")
            state["base"] = client.base
            return f"процесс {process.pid} ответил на /api/identity, started_at записан"

        if not chain.run("запуск", boot):
            chain.skip_rest(later[1:], "продукт не поднялся")
            return finish(chain, evidence, expected_sha)
        client = state["client"]

        def http_ready():
            identity = client("/api/identity")
            assert identity["app"] == "bossman-command-center", identity
            assert identity.get("source") == "installed_build", (
                f"работающий код называет себя {identity.get('source')!r}, а не установленной "
                f"сборкой: {identity.get('detail')!r}")
            assert identity.get("source_identity") == "PASS", identity
            if expected_sha:
                assert identity.get("build_sha") == expected_sha, (
                    identity.get("build_sha"), expected_sha)
            state["token"] = login(client, work)
            health = wait_healthy(client)
            need = [*CONTRACTED, *(k for k in health if k.startswith("tick:"))]
            return (f"/api/identity назвал установленную сборку {identity.get('build_sha')}, "
                    f"неавторизованный /api/system = 401, неверный токен = 401, вход выполнен, "
                    f"{len(need)} подсистем в ok")

        if not chain.run("HTTP готов", http_ready):
            chain.skip_rest(later[2:], "HTTP-готовности не было")
            return finish(chain, evidence, expected_sha)

        chain.run("UI готов", lambda: link_ui_ready(
            state["base"], state["token"], expected_sha, require_browser=require_browser,
            min_controls=min_controls, evidence=evidence,
            browser_executable=browser_executable))
        chain.run("окно рабочего стола", lambda: link_desktop_window(evidence),
                  owner_hardware=True)

        if not chain.run("сценарий", lambda: link_scenario(client, state)):
            chain.skip_rest(later[5:], "сценарий не выполнен — продолжать нечему")
            return finish(chain, evidence, expected_sha)

        def restart():
            nonlocal process, log
            state["port_before"] = int(state["base"].rsplit(":", 1)[1])
            state["kill"] = hard_kill(process, log, state["port_before"])
            process, new_client, log = start_installed(work)
            state["client"] = new_client
            state["pid_after"] = process.pid
            state["started_after"] = new_client("/api/identity").get("started_at")
            login(new_client, work)
            wait_healthy(new_client)
            return link_restart(state)

        if not chain.run("перезапуск", restart):
            chain.skip_rest(["возобновление"], "перезапуска не было")
            return finish(chain, evidence, expected_sha)

        chain.run("возобновление", lambda: link_resume(state["client"], state))
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=30)
        if log is not None and not log.closed:
            log.close()
    return finish(chain, evidence, expected_sha)


def finish(chain: Chain, evidence: Path, expected_sha) -> dict:
    report = {
        "schema_version": 1,
        "status": chain_verdict(chain.links),
        "source_sha": expected_sha,
        "платформа": f"{sys.platform} python {sys.version.split()[0]}",
        "префикс_установки": sys.prefix,
        # Привязка улики к прогону (OA-02): отчёт без неё — не улика кандидата.
        "привязка": {"archive_sha256": os.environ.get("BOSSMAN_ARCHIVE_SHA256") or None,
                     "run_id": os.environ.get("GITHUB_RUN_ID") or None,
                     "harness_sha": os.environ.get("BOSSMAN_HARNESS_SHA") or None},
        "когда": datetime.now(timezone.utc).isoformat(),
        "звенья": chain.links,
    }
    (evidence / "installed-product-chain.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main(argv=None) -> int:
    _console_utf8()
    parser = argparse.ArgumentParser(description="Цепочка установленного продукта")
    parser.add_argument("--workdir", type=Path, help="рабочий каталог (по умолчанию временный)")
    parser.add_argument("--evidence", type=Path, help="куда класть улики")
    parser.add_argument("--json", type=Path, help="куда положить отчёт")
    parser.add_argument("--expected-sha", help="коммит, для которого собирается улика")
    parser.add_argument("--doctor", type=Path, help="путь к bossman_doctor.py")
    parser.add_argument("--require-browser", action="store_true",
                        help="отсутствие Chromium — отказ, а не OWNER_HARDWARE_REQUIRED")
    parser.add_argument("--browser-executable", type=Path,
                        help="запускать страницу этим браузером (например, уже стоящим в "
                             "системе Chrome) вместо скачанного playwright")
    parser.add_argument("--min-controls", type=int, default=SHIPPED_SHELL_CONTROLS,
                        help="пол по числу доступных органов управления на оболочке; "
                             "по умолчанию столько, сколько их в поставляемой разметке")
    args = parser.parse_args(argv)
    if args.expected_sha and not HEX40.fullmatch(args.expected_sha):
        parser.error("--expected-sha должен быть полным 40-символьным SHA")

    doctor = args.doctor
    if doctor is None:
        shipped = Path(__file__).resolve().with_name("bossman_doctor.py")
        beside = Path(__file__).resolve().parent.parent / "scripts" / "bossman_doctor.py"
        doctor = shipped if shipped.is_file() else beside
    if not Path(doctor).is_file():
        parser.error(f"доктор не найден: {doctor}")

    temporary = None
    if args.workdir:
        work = args.workdir.resolve()
    else:
        temporary = tempfile.TemporaryDirectory(prefix="installed-chain-")
        work = Path(temporary.name)
    evidence = (args.evidence or (work / "evidence")).resolve()
    try:
        report = run_chain(work, evidence, expected_sha=args.expected_sha,
                           doctor=Path(doctor), require_browser=args.require_browser,
                           min_controls=args.min_controls,
                           browser_executable=args.browser_executable)
    finally:
        if temporary is not None:
            try:
                temporary.cleanup()
            except OSError:
                pass
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=True))
    print(f"INSTALLED_PRODUCT_CHAIN={report['status']}", flush=True)
    return EXIT_CODES[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
