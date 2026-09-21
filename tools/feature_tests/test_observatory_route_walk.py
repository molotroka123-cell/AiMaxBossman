"""Route-introspection regression; lazy-wrapper fixtures and real FastAPI routes."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from bcc.features.observatory import get_routes


@pytest.mark.parametrize('low_priority', [False, True])
def test_effective_nested_candidates_are_not_missing(low_priority):
    async def handler():
        return {'ok': True}
    leaf = SimpleNamespace(path='/api/video-studio/commands', methods={'POST'}, endpoint=handler)
    ignored = SimpleNamespace(path='/private', methods={'GET'}, endpoint=handler)
    inner = SimpleNamespace(effective_candidates=lambda: [leaf, ignored])
    wrapper = SimpleNamespace(**{
        'effective_low_priority_routes' if low_priority else 'effective_candidates': lambda: [inner]})
    request = Request({'type': 'http', 'app': SimpleNamespace(routes=[wrapper])})
    rows = asyncio.run(get_routes(request))['routes']
    assert len(rows) == 1
    assert rows[0]['path'] == '/api/video-studio/commands'
    assert rows[0]['methods'] == ['POST']
    assert rows[0]['evidence'] == 'DECLARED_ROUTE_NOT_EXECUTION'


def test_catalogue_agrees_with_real_prefixed_router():
    app = FastAPI()
    router = APIRouter()
    @router.get('/ready')
    async def ready():
        return {'ready': True}
    app.include_router(router, prefix='/api/probe')
    with TestClient(app) as client:
        assert client.get('/api/probe/ready').json() == {'ready': True}
    request = Request({'type': 'http', 'app': app})
    rows = asyncio.run(get_routes(request))['routes']
    assert any(row['path'] == '/api/probe/ready' and row['methods'] == ['GET'] for row in rows)
