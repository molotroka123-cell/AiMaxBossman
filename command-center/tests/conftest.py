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


@pytest.fixture
async def env(tmp_path):
    """Приложение без фоновых циклов: тесты сами дёргают engine/scheduler."""
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    async with client_for(app, svc) as client:
        yield SimpleEnv(app=app, svc=svc, client=client, settings=settings)
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
