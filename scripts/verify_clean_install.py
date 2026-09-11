#!/usr/bin/env python3
"""Проверка того, что продукт МОЖНО чисто поставить, запустить и им пользоваться.

Не юнит-тесты и не список в документе: скрипт собирает дистрибутивы из чистого
экспорта ТЕКУЩЕГО коммита, ставит их в пустой venv, поднимает установленный
продукт без чекаута на пути импорта и прогоняет владельческие сценарии по HTTP.

Так были найдены два дефекта, которых в чекауте не существует, поэтому ни один
юнит-тест их не видел:

* установленный продукт отвечал 404 на `/` — интерфейс лежит вне пакета `bcc`
  и в колесо не попадал: сервер поднимался, а пользоваться им было нечем;
* данные владельца по умолчанию писались внутрь `site-packages`.

    python scripts/verify_clean_install.py            проверить
    python scripts/verify_clean_install.py --keep     оставить окружение

Коды выхода: 0 — всё прошло, 1 — есть отказ.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DISTRIBUTIONS = (".", "bossman-core", "command-center")


def _console_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _run(cmd: list[str], *, timeout: int = 1800, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout, **kw)


def pristine_export(target: Path) -> Path:
    """Ровно то, что закоммичено: без build/, кэшей и локального мусора.

    Локальный `build/` уже однажды сделал проверку ложно-зелёной: setuptools
    переиспользовал прошлую сборку, и колесо получало интерфейс, которого в
    коммите не было.
    """
    target.mkdir(parents=True, exist_ok=True)
    archive = _run(["git", "-C", str(REPO), "archive", "HEAD"])
    if archive.returncode:
        raise SystemExit(f"git archive не отработал: {archive.stderr[-2000:]!r}")
    unpack = subprocess.run(["tar", "-x", "-C", str(target)], input=archive.stdout, timeout=600)
    if unpack.returncode:
        raise SystemExit("не удалось распаковать экспорт коммита")
    return target


def install(source: Path, venv: Path) -> Path:
    _run([sys.executable, "-m", "venv", str(venv)])
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    tooling = _run([str(python), "-m", "pip", "install", "--upgrade", "pip", "wheel"])
    if tooling.returncode:
        raise SystemExit(f"pip не обновился:\n{tooling.stderr.decode('utf-8', 'replace')[-2000:]}")
    done = _run([str(python), "-m", "pip", "install",
                 *[str(source / d) for d in DISTRIBUTIONS]])
    if done.returncode:
        raise SystemExit("чистая установка не прошла:\n"
                         + done.stderr.decode("utf-8", "replace")[-4000:])
    return python


class Client:
    """HTTP-клиент владельца: cookie-сессия и CSRF, как в браузере."""

    def __init__(self, port: int) -> None:
        self.base = f"http://127.0.0.1:{port}"
        self.csrf: str | None = None
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def __call__(self, path: str, method: str = "GET", payload=None,
                 expected: int | None = 200, raw: bool = False):
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
        if expected is not None and response.status != expected:
            raise AssertionError(f"{method} {path} -> {response.status}: {body[:400]}")
        return body if raw else json.loads(body)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start(python: Path, work: Path) -> tuple[subprocess.Popen, Client]:
    """Запуск установленного продукта. Репозитория на пути импорта нет."""
    port = _free_port()
    env = dict(os.environ, BCC_DATA_DIR=str(work / "data"), BCC_TOKEN_STDOUT="0",
               PYTHONUNBUFFERED="1", PYTHONUTF8="1")
    env.pop("PYTHONPATH", None)
    log = (work / "server.log").open("ab")
    process = subprocess.Popen([str(python), "-m", "bcc", "--host", "127.0.0.1",
                                "--port", str(port)], env=env, cwd=work,
                               stdout=log, stderr=subprocess.STDOUT)
    client = Client(port)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError("сервер упал при старте:\n"
                                 + (work / "server.log").read_text(errors="replace")[-4000:])
        try:
            client("/api/identity")
            return process, client
        except Exception:
            time.sleep(0.3)
    process.terminate()
    raise AssertionError("установленный продукт не поднялся за 120 секунд")


def stop(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=25)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
        raise AssertionError("продукт не остановился за 25 секунд")


def login(client: Client, work: Path) -> str:
    client("/api/system", expected=401)
    token = (work / "data" / "token").read_text(encoding="utf-8").strip()
    client("/api/login", method="POST", payload={"token": "неверный"}, expected=401)
    client.csrf = client("/api/login", method="POST", payload={"token": token})["csrf"]
    return token


def run_checks(python: Path, work: Path) -> int:
    results: list[tuple[str, str, str]] = []
    state: dict = {}

    def check(name: str, fn) -> None:
        try:
            results.append((name, "PASS", fn() or ""))
        except Exception as exc:  # проверка обязана назвать причину, а не упасть молча
            results.append((name, "FAIL", f"{type(exc).__name__}: {exc}"[:600]))

    process, c = start(python, work)
    try:
        token = login(c, work)

        def ui() -> str:
            body = c("/", raw=True).decode("utf-8", "replace").lower()
            assert "<html" in body, "интерфейс не отдаётся"
            return "index.html отдан"
        check("интерфейс отдаётся установленным продуктом", ui)

        def health() -> str:
            deadline = time.monotonic() + 40
            while True:
                health = c("/api/system")["health"]
                need = ["db", "queue_worker", "scheduler", "metrics"]
                need += [k for k in health if k.startswith("tick:")]
                bad = {k: health[k]["status"] for k in need if health[k]["status"] != "ok"}
                if not bad:
                    return f"{len(need)} подсистем ok"
                assert time.monotonic() < deadline, f"не поднялось: {bad}"
                time.sleep(0.5)
        check("фоновые циклы здоровы", health)

        def agent() -> str:
            created = c("/api/agents", method="POST",
                        payload={"name": "clean-install-probe", "enabled": False})
            state["agent"] = created["id"]
            assert any(a["id"] == created["id"] for a in c("/api/agents"))
            return f"агент {created['id']}"
        check("агент создаётся и читается", agent)

        def terminal() -> str:
            """Полный owner-цикл: запрос → подтверждение владельца → реальный запуск."""
            marker = "bossman-clean-install-9713"
            payload = {"mode": "project_host", "command": f"echo {marker}",
                       "cwd": str(work / "data")}
            first = json.loads(c("/api/terminal/run", method="POST", payload=payload,
                                 expected=None, raw=True))
            answer = first
            if "session_id" not in first:
                approval = first["error"]["approval_id"]
                c(f"/api/approvals/{approval}", method="POST",
                  payload={"approve": True, "by": "owner"})
                answer = c("/api/terminal/run", method="POST",
                           payload=dict(payload, approval_id=approval))
            session = answer["session_id"]
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                status = c(f"/api/terminal/sessions/{session}")
                if status.get("finished"):
                    assert marker in json.dumps(status, ensure_ascii=False), (
                        f"вывод команды не вернулся: {status}")
                    assert status.get("exit_code") == 0, status
                    return "подтверждение → запуск, вывод получен, exit=0"
                time.sleep(0.3)
            raise AssertionError("команда не завершилась за 90 секунд")
        check("терминал: команда реально выполняется", terminal)

        def no_self_approval() -> str:
            """`approved: true` в теле запроса — не подтверждение (F-015)."""
            body = c("/api/terminal/run", method="POST", expected=None, raw=True, payload={
                "mode": "project_host", "command": "rm -rf /",
                "cwd": str(work / "data"), "approved": True}).decode("utf-8", "replace")
            assert "session_id" not in body, f"действие выполнено по самоутверждению: {body[:300]}"
            return "самоутверждённый флаг отклонён"
        check("нет действия без подтверждения владельца", no_self_approval)

        def designer() -> str:
            project = c("/api/web-designer/projects", method="POST",
                        payload={"name": "clean-install", "template": "blank"})
            state["pid"] = int(project["meta"]["id"])
            state["version"] = project["meta"]["version"]
            assert "<html" in project["code"].lower(), "проект создан без кода"
            return f"проект {state['pid']}"
        check("Web Designer: проект создаётся", designer)

        def apply_edit() -> str:
            html = ('<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">'
                    '<title>clean</title></head><body><h1 id="probe">ok</h1></body></html>')
            out = c(f"/api/web-designer/projects/{state['pid']}/code", method="PUT",
                    payload={"html": html, "note": "проверка",
                             "base_version": state["version"]})
            state["version"] = out["meta"]["version"]
            assert 'id="probe"' in c(f"/api/web-designer/projects/{state['pid']}")["code"]
            return f"версия {state['version']}"
        check("Web Designer: Apply сохраняется", apply_edit)

        def stale_edit() -> str:
            """Правка поверх устаревшей версии не смеет затирать актуальную."""
            c(f"/api/web-designer/projects/{state['pid']}/code", method="PUT", expected=None,
              raw=True, payload={"html": "<!DOCTYPE html><html><body><h1>устаревшая</h1></body></html>",
                                 "note": "устаревшая", "base_version": 0})
            assert 'id="probe"' in c(f"/api/web-designer/projects/{state['pid']}")["code"], (
                "устаревшая правка затёрла актуальный код")
            return "рассинхрон отклонён"
        check("Web Designer: защита от рассинхрона", stale_edit)

        check("Coding: сессии отвечают",
              lambda: f"сессий: {len(c('/api/coding-sessions'))}")
        check("Задачи: список отвечает", lambda: f"задач: {len(c('/api/tasks'))}")
    finally:
        try:
            stop(process)
        except AssertionError as exc:
            results.append(("продукт останавливается штатно", "FAIL", str(exc)))

    # Перезапуск: состояние владельца обязано пережить остановку.
    process, c = start(python, work)
    try:
        again = (work / "data" / "token").read_text(encoding="utf-8").strip()
        check("токен пережил перезапуск",
              lambda: "тот же" if again == token else _fail("токен сменился"))
        c.csrf = c("/api/login", method="POST", payload={"token": again})["csrf"]
        check("агент пережил перезапуск",
              lambda: f"агент {state['agent']}"
              if any(a["id"] == state["agent"] for a in c("/api/agents"))
              else _fail("агент пропал"))
        check("правка Web Designer пережила перезапуск",
              lambda: "код на месте"
              if 'id="probe"' in c(f"/api/web-designer/projects/{state['pid']}")["code"]
              else _fail("правка потеряна"))
    finally:
        stop(process)

    width = max(len(name) for name, _, _ in results)
    for name, verdict, detail in results:
        print(f"[{verdict}] {name.ljust(width)}  {detail}")
    failed = sum(1 for _, verdict, _ in results if verdict == "FAIL")
    print(f"\nCLEAN_INSTALL={'PASS' if not failed else 'FAIL'} "
          f"({len(results) - failed} PASS, {failed} FAIL)")
    return 1 if failed else 0


def _fail(message: str):
    raise AssertionError(message)


def main(argv: list[str] | None = None) -> int:
    _console_utf8()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keep", action="store_true", help="не удалять временное окружение")
    args = parser.parse_args(argv)

    scratch = Path(tempfile.mkdtemp(prefix="bossman-clean-install-"))
    try:
        print("чистый экспорт коммита...", flush=True)
        source = pristine_export(scratch / "source")
        print("установка в пустой venv...", flush=True)
        python = install(source, scratch / "venv")
        work = scratch / "run"
        work.mkdir()
        print("запуск установленного продукта...\n", flush=True)
        return run_checks(python, work)
    finally:
        if args.keep:
            print(f"окружение оставлено: {scratch}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
