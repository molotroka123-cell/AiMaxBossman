"""Общие фикстуры: приложение на временной SQLite, клиент с токеном, фейковые адаптеры.

Сети в тестах нет: адаптеры либо подменяются, либо ходят через httpx.MockTransport.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from bcc.api import Services, create_app
from bcc.auth import HEADER
from bcc.config import Settings
from bcc.providers import ChatResult, Health, ProviderError


class FakeAdapter:
    """Адаптер без сети: отдаёт заранее заданный ответ или падает N первых раз."""

    def __init__(self, text: str = "готово", *, fail_times: int = 0,
                 error: str = "провайдер недоступен", on_chat=None,
                 tokens: tuple[int, int] = (7, 3)):
        self.text = text
        self.fail_times = fail_times
        self.error = error
        self.on_chat = on_chat
        self.tokens = tokens
        self.calls = 0

    async def chat(self, model: str, messages: list[dict], **kw: Any) -> ChatResult:
        self.calls += 1
        if self.on_chat is not None:
            await self.on_chat(self.calls, messages)
        if self.calls <= self.fail_times:
            raise ProviderError(self.error, kind="network")
        return ChatResult(text=self.text, tokens_in=self.tokens[0], tokens_out=self.tokens[1],
                          model=model)

    async def health(self) -> Health:
        return Health(status="ok", latency_ms=1)

    async def list_models(self) -> list[str]:
        return ["fake-model"]


def make_settings(tmp_path: Path) -> Settings:
    data = tmp_path / "data"
    return Settings(data_dir=data, database_url=f"sqlite+aiosqlite:///{data / 'bcc.db'}",
                    ui_dir=tmp_path / "no-ui")


async def start_app(settings: Settings, **kw) -> tuple[Any, Services]:
    app = create_app(settings, announce_token=False, **kw)
    svc: Services = app.state.svc
    await svc.start()
    return app, svc


def client_for(app, svc: Services) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://test", headers={HEADER: svc.auth.token})


@pytest.fixture(autouse=True)
def _fable_ledger_off_the_real_machine(tmp_path, monkeypatch):
    """Ни один тест не трогает боевой журнал жёсткого потолка Fable.

    Журнал — один durable-файл на машине владельца, и резерв, снятый тестом,
    съел бы настоящие деньги и остался бы съеденным. Перенаправление сделано
    патчем атрибута в процессе, а не настройкой: переменной окружения,
    двигающей журнал, нет намеренно — журнал, который можно перенести, это
    потолок, который можно поднять.
    """
    from bcc import fable_cap
    if fable_cap.LEDGER_AVAILABLE:
        from bossman_shared import fable_budget
        monkeypatch.setattr(fable_budget, "LEDGER_PATH", tmp_path / "fable_hard_cap.json")


@pytest.fixture
async def env(tmp_path, request):
    """Приложение без фоновых worker-циклов; тесты сами дёргают engine/scheduler.

    Golden Missions моделируют полноценную owner-сессию с несколькими последовательными
    approvals. Production Services держит ``approval_watcher`` живым всё время работы,
    а исторический тестовый harness создавал watcher только внутри ``_drain`` и отменял
    его ровно перед POST решения владельца. Событие ``approval.decided`` поэтому могло
    потеряться, после чего тест зависел от 60-секундного recovery sweep. На более
    медленном Python/runner это оставляло составную миссию в ``waiting_approval``.

    Для Golden Missions держим ровно ОДИН production-like watcher на всём lifetime env.
    Сам ``_drain`` по историческим причинам всё ещё создаёт watcher-задачу, поэтому на
    время этой фикстуры его метод заменяется бездействующим cancellable coroutine:
    события обрабатывает только постоянный исходный watcher. Worker-циклы всё ещё не
    запускаются автоматически; approval/review policy не меняется, а
    ``review_escalation`` по-прежнему никогда не auto-approved.
    """
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    persistent_approval_watcher = None
    original_approval_watcher = None
    node_path = getattr(request.node, "path", None) or getattr(request.node, "fspath", None)
    if node_path is not None and Path(str(node_path)).name == "test_golden_missions.py":
        original_approval_watcher = svc.engine.approval_watcher
        persistent_approval_watcher = asyncio.create_task(original_approval_watcher())
        # Дать подписчику зарегистрировать очередь до первого owner decision.
        await asyncio.sleep(0)

        async def _watcher_already_owned() -> None:
            # _drain() отменит эту задачу при выходе; живой production-like
            # подписчик выше остаётся на месте между последовательными approvals.
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                return

        svc.engine.approval_watcher = _watcher_already_owned  # type: ignore[method-assign]
    try:
        async with client_for(app, svc) as client:
            yield SimpleEnv(app=app, svc=svc, client=client, settings=settings)
    finally:
        if persistent_approval_watcher is not None:
            if original_approval_watcher is not None:
                svc.engine.approval_watcher = original_approval_watcher  # type: ignore[method-assign]
            persistent_approval_watcher.cancel()
            await asyncio.gather(persistent_approval_watcher, return_exceptions=True)
        await svc.stop()


class SimpleEnv:
    def __init__(self, app, svc, client, settings):
        self.app = app
        self.svc = svc
        self.client = client
        self.settings = settings


async def wait_for(check, timeout: float = 5.0, interval: float = 0.02):
    """Ждать условия (worker работает в фоне), но не дольше timeout."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        value = await check()
        if value:
            return value
        await asyncio.sleep(interval)
    raise AssertionError("условие не наступило за отведённое время")
