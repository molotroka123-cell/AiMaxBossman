"""Подключение модуля к рантайму. Тест на «код есть, но его никто не зовёт».

Модуль, который не вызывается ниоткуда, — это DEAD_OR_UNWIRED, каким бы
качественным он ни был. Здесь проверяется именно наличие точки вызова.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def test_core_api_includes_the_trading_router():
    """bossman.api действительно подключает роутер модуля."""
    api_src = (REPO / "bossman-core" / "bossman" / "api.py").read_text(encoding="utf-8")
    assert '"bossman.trading_learning",' in api_src
    mod = importlib.import_module("bossman.trading_learning")
    router = getattr(mod, "router", None)
    assert router is not None
    paths = {r.path for r in router.routes}
    assert "/trading-lab/status" in paths
    assert "/trading-lab/benchmark" in paths


def test_no_route_can_create_an_order():
    """Ни одна ручка не создаёт ордер: исполнение недостижимо через HTTP."""
    from bossman.trading_learning.routes import router
    for route in router.routes:
        for method in getattr(route, "methods", set()):
            if method in ("POST", "PUT", "PATCH", "DELETE"):
                # Единственный не-GET маршрут — ingest, и он отказной.
                assert route.path.endswith("/ingest"), route.path


@pytest.mark.asyncio
async def test_ingest_route_refuses_without_the_approvals_queue():
    from fastapi import HTTPException
    from bossman.trading_learning.routes import ingest
    with pytest.raises(HTTPException) as exc:
        await ingest()
    assert exc.value.status_code == 409
    assert "owner approval" in str(exc.value.detail)


def test_command_center_feature_is_registered():
    """Фича Command Center подхватывается загрузчиком и отдаёт роуты."""
    cc = REPO / "command-center"
    module_path = cc / "bcc" / "features" / "trading_lab.py"
    assert module_path.exists()
    src = module_path.read_text(encoding="utf-8")
    assert "FEATURE = Feature(name=\"trading_lab\"" in src
    for path in ("/trading-lab/status", "/trading-lab/seed",
                 "/trading-lab/benchmark", "/trading-lab/memory"):
        assert path in src


def test_command_center_page_is_registered_in_the_index():
    ui = REPO / "command-center" / "ui" / "pages"
    index = (ui / "index.js").read_text(encoding="utf-8")
    assert "import TradingLabPage from './trading_lab.js';" in index
    assert "  TradingLabPage," in index
    page = (ui / "trading_lab.js").read_text(encoding="utf-8")
    for endpoint in ("/api/trading-lab/status", "/api/trading-lab/seed",
                     "/api/trading-lab/benchmark", "/api/trading-lab/memory"):
        assert endpoint in page


def _strip_comments(source: str) -> str:
    """Убрать комментарии: проверяем то, что видит пользователь, а не пояснения."""
    source = re.sub(r"/\*.*?\*/", " ", source, flags=re.S)
    return re.sub(r"^\s*//.*$", " ", source, flags=re.M)


def test_ui_never_promises_profit_or_unconditional_readiness():
    """Экран не имеет права утверждать прибыльность или готовность сам от себя."""
    page = (REPO / "command-center" / "ui" / "pages" / "trading_lab.js").read_text(encoding="utf-8")
    code = _strip_comments(page)
    assert "profitable" not in code.lower()
    # Слово о прибыли допустимо только в отрицании — иначе это обещание.
    for match in re.finditer(r"[Пп]рибыльност\w*", code):
        window = code[match.start(): match.start() + 60]
        assert "не доказан" in window, f"unconditional profit claim: {window!r}"
    # READY появляется только как значение вердикта, пришедшее с сервера.
    for match in re.finditer(r"READY", code):
        window = code[max(0, match.start() - 40): match.start() + 10]
        assert "verdict" in window or "NOT_READY" in window, window
    assert "report.verdict === 'READY'" in code
