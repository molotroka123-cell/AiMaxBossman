"""authored_by_lane (opsplug, 2026-10-06): shared helpers for the test_leaf_* route tests.

Same technique as test_core_auth_perimeter.py: the real core app, a real DeviceService with an
in-memory store, real bearer tokens with chosen scopes; no mock of the router under test.
"""
from __future__ import annotations

import httpx
from fastapi import FastAPI

from bossman.remote_client import DeviceService, InMemoryDeviceStore, reset_service, set_service


def new_app() -> FastAPI:
    import importlib

    import bossman.api as api
    importlib.reload(api)
    return api.app


def client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def bearer(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


class Devices:
    """Context manager: a fresh DeviceService installed as the process service."""

    def __enter__(self) -> "Devices":
        self.svc = DeviceService(InMemoryDeviceStore())
        set_service(self.svc)
        return self

    def __exit__(self, *exc) -> None:
        reset_service()

    async def token(self, *scopes: str) -> str:
        _, raw = await self.svc.enroll("leaf-test", set(scopes))
        return raw
