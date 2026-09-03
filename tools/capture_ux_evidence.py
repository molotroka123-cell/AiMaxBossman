#!/usr/bin/env python3
"""Снимки интерфейса для приёмки V2: постоянное доказательство, а не /tmp.

Поднимает настоящий Command Center на временных данных, наполняет его небольшим
правдоподобным состоянием (живая задача, упавшая задача, решение в очереди) и
снимает страницы в docs/ux/evidence/final/.

Запуск:  python tools/capture_ux_evidence.py [--out docs/ux/evidence/final]
Снимки — JPEG (меньше вес), размер окна 1440x900 и 390x844.
"""
from __future__ import annotations

import argparse
import asyncio
import socket
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "command-center"))

import sqlalchemy as sa  # noqa: E402
from bcc.app import create_app  # noqa: E402
from bcc.config import Settings  # noqa: E402
from bcc.db import task_runs as runs_t, tasks as tasks_t, utcnow  # noqa: E402

DESKTOP = [
    ("home-v3", "01-home"), ("missions", "02-missions"), ("agents", "03-agents"),
    ("approvals", "04-approvals"), ("models", "05-models"), ("tasks", "06-tasks"),
    ("skills", "07-skills"), ("builder", "08-builder"), ("images", "09-images"),
]
MOBILE = [("home-v3", "m1-home"), ("missions", "m2-missions"), ("approvals", "m3-approvals")]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, tmp: Path) -> None:
        import uvicorn

        data = tmp / "data"
        self.settings = Settings(data_dir=data, database_url=f"sqlite+aiosqlite:///{data / 'bcc.db'}",
                                 ui_dir=REPO / "command-center" / "ui")
        self.app = create_app(self.settings, announce_token=False, start_workers=False)
        self.svc = self.app.state.svc
        self.port = _free_port()
        self.loop = asyncio.new_event_loop()
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port,
                                                    log_level="warning", loop="none",
                                                    timeout_graceful_shutdown=1))
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.server.serve())

    def start(self) -> "Server":
        import httpx

        self.thread.start()
        deadline = time.time() + 25
        while time.time() < deadline:
            try:
                # trust_env=False: прокси из окружения предназначен для внешних
                # адресов; применённый к 127.0.0.1 он ломает проверку готовности.
                with httpx.Client(trust_env=False, timeout=1) as client:
                    if client.get(self.url).status_code < 500:
                        return self
            except httpx.HTTPError:
                time.sleep(0.15)
        raise RuntimeError("сервер не поднялся")

    def call(self, factory, timeout: float = 10.0):
        return asyncio.run_coroutine_threadsafe(factory(), self.loop).result(timeout=timeout)

    def emit(self, kind: str, **data) -> None:
        self.call(lambda: self.svc.bus.emit(kind, **data))

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"


def seed(srv: Server) -> None:
    """Немного правдоподобного состояния — пустые экраны ничего не доказывают."""
    async def go():
        async with srv.svc.db.session() as s:
            res = await s.execute(sa.insert(tasks_t).values(
                title="Разбор входящей почты", prompt="разобрать почту", status="running",
                priority=5, max_retries=1, created_at=utcnow(), updated_at=utcnow()))
            running = int(res.inserted_primary_key[0])
            await s.execute(sa.insert(runs_t).values(
                task_id=running, attempt=1, status="running", model_alias="qwen-14b",
                checkpoint={"step": 2, "note": "", "messages": []}, started_at=utcnow()))
            res = await s.execute(sa.insert(tasks_t).values(
                title="Ночной отчёт по расходам", prompt="собрать отчёт", status="failed",
                priority=5, max_retries=1, created_at=utcnow(), updated_at=utcnow()))
            failed = int(res.inserted_primary_key[0])
            await s.execute(sa.insert(runs_t).values(
                task_id=failed, attempt=1, status="failed", model_alias="qwen-14b",
                error="провайдер недоступен", finished_at=utcnow()))
            await s.commit()
            return running

    running = srv.call(go)
    srv.call(lambda: srv.svc.approvals.create("terminal.run", "rm -rf ./tmp-artifacts", task_id=running))
    for kind, payload in [
        ("task.started", {"run_id": 1, "task_id": running, "title": "Разбор входящей почты", "model": "qwen-14b"}),
        ("task.progress", {"run_id": 1, "task_id": running, "step": 2, "max_steps": 5, "model": "qwen-14b"}),
        ("tool.called", {"run_id": 1, "task_id": running, "tool": "fs.read", "duration_ms": 118, "ok": True}),
        ("router.fallback", {"run_id": 1, "task_id": running, "model_id": "qwen-14b", "reason": "таймаут"}),
        ("run.log", {"run_id": 1, "task_id": running, "level": "info", "message": "шаг 2 выполнен"}),
    ]:
        srv.emit(kind, **payload)


def shoot(page, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(path), type="jpeg", quality=72)
    print(f"  {path.relative_to(REPO)}  {path.stat().st_size // 1024} КБ")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/ux/evidence/final")
    args = ap.parse_args()
    out = REPO / args.out

    import tempfile

    from playwright.sync_api import sync_playwright

    try:
        from bcc.features.browser import CHROMIUM
    except Exception:  # noqa: BLE001
        CHROMIUM = ""

    with tempfile.TemporaryDirectory() as tmp:
        srv = Server(Path(tmp)).start()
        seed(srv)
        errors: list[str] = []
        try:
            with sync_playwright() as pw:
                browser = (pw.chromium.launch(executable_path=CHROMIUM) if CHROMIUM and Path(CHROMIUM).exists()
                           else pw.chromium.launch())
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(srv.url, wait_until="domcontentloaded")
                page.fill("#login-token", srv.svc.auth.token)
                page.click("#login-submit")
                page.wait_for_selector("#shell:not([hidden])", timeout=15000)

                print("Рабочий стол 1440x900:")
                for page_id, name in DESKTOP:
                    page.goto(f"{srv.url}#/{page_id}", wait_until="domcontentloaded")
                    page.wait_for_function(
                        "!document.querySelector('#view .skeleton') && document.getElementById('view').childElementCount > 0",
                        timeout=20000)
                    page.wait_for_timeout(350)
                    shoot(page, out / f"desktop-{name}.jpg")

                page.goto(f"{srv.url}#/home-v3", wait_until="domcontentloaded")
                page.wait_for_timeout(400)
                page.evaluate("window.__bxThinking.open()")
                page.wait_for_selector("#think-pane:not([hidden])", timeout=10000)
                page.wait_for_timeout(400)
                shoot(page, out / "desktop-10-thinking-process.jpg")

                srv.stop()                       # снимок состояния «связь потеряна»
                page.wait_for_selector("#stale-banner:not([hidden])", timeout=20000)
                page.wait_for_timeout(600)
                shoot(page, out / "desktop-11-disconnected.jpg")
                browser.close()

            # мобильный проход поднимает сервер заново
            srv2 = Server(Path(tmp) / "m").start()
            seed(srv2)
            with sync_playwright() as pw:
                browser = (pw.chromium.launch(executable_path=CHROMIUM) if CHROMIUM and Path(CHROMIUM).exists()
                           else pw.chromium.launch())
                page = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(srv2.url, wait_until="domcontentloaded")
                page.fill("#login-token", srv2.svc.auth.token)
                page.click("#login-submit")
                page.wait_for_selector("#shell:not([hidden])", timeout=15000)
                print("Телефон 390x844:")
                for page_id, name in MOBILE:
                    page.goto(f"{srv2.url}#/{page_id}", wait_until="domcontentloaded")
                    page.wait_for_function(
                        "!document.querySelector('#view .skeleton') && document.getElementById('view').childElementCount > 0",
                        timeout=20000)
                    page.wait_for_timeout(350)
                    shoot(page, out / f"mobile-{name}.jpg")
                page.evaluate("window.__bxThinking.open()")
                page.wait_for_selector("#think-pane:not([hidden])", timeout=10000)
                page.wait_for_timeout(300)
                shoot(page, out / "mobile-m4-thinking-process.jpg")
                browser.close()
            srv2.stop()
        finally:
            srv.stop()

        if errors:
            print("ОШИБКИ СТРАНИЦЫ:", *errors, sep="\n  ")
            return 1
    print("готово")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
