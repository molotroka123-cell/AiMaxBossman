"""HTTP-поверхность приложения. Ровно то, на что есть чем ответить.

Манифест обещает семнадцать операций контракта. Реализованы из них те, за
которыми стоит настоящая величина: здоровье процесса, каталог возможностей и
счётчики. Остальные объявлены и отвечают `501` с именем операции.

Почему `501`, а не `404` и не заглушка с бодрым ответом. `404` говорит «такого
адреса нет» — неправда: адрес объявлен контрактом, и плоскость управления
вправе его звать. Заглушка с пустым успехом хуже вдвойне: экран покажет
работающую операцию там, где её нет, и владелец узнает об этом в момент, когда
она была нужна. `501` — единственный честный ответ: операция существует и в
этой сборке не выполняется.

Само приложение при этом ПОДНИМАЕТСЯ. До этого модуля `social_farm.api` был
пустым, `social_farm.main` не существовал вовсе, и запуск через Bossman
порождал процесс, умиравший с `ModuleNotFoundError` за доли секунды: владелец
видел приложение, которое «не открывается», без единой причины.
"""
from __future__ import annotations

import os
import time
from typing import Any

from ..domain.capability import CapabilityStatus, explain, is_actionable
from ..domain.safety import CAPABILITY_SAFETY, default_decision

# Операции контракта из `app.manifest.yaml`, которые эта сборка не выполняет.
# Список объявлен здесь, а не выведен из отсутствия обработчика: пропущенный
# по забывчивости маршрут иначе выглядел бы как сознательно неподдержанный.
NOT_IMPLEMENTED: tuple[tuple[str, str, str], ...] = (
    ("accounts.list", "GET", "/api/accounts"),
    ("accounts.capabilities", "GET", "/api/accounts/{account_id}/capabilities"),
    ("policy.evaluate", "POST", "/api/policy/evaluate"),
    ("content.create", "POST", "/api/content"),
    ("content.revisions", "GET", "/api/content/{content_id}/revisions"),
    ("approvals.list", "GET", "/api/approvals"),
    ("approvals.decide", "POST", "/api/approvals/{approval_id}/decide"),
    ("jobs.create", "POST", "/api/jobs"),
    ("jobs.status", "GET", "/api/jobs/{job_id}"),
    ("jobs.cancel", "POST", "/api/jobs/{job_id}/cancel"),
    ("jobs.list", "GET", "/api/jobs"),
    ("inbox.list", "GET", "/api/inbox"),
    ("insights.read", "GET", "/api/accounts/{account_id}/insights"),
)

STARTED_AT = time.time()


def capability_catalogue() -> list[dict[str, Any]]:
    """Каталог возможностей с классом безопасности и решением по умолчанию.

    Настоящие данные: и класс, и решение читаются из доменного каталога, а не
    перечисляются здесь второй раз.
    """
    return [{"capability": name, "safety_class": safety.value,
             "default_decision": default_decision(name)}
            for name, safety in sorted(CAPABILITY_SAFETY.items())]


def build_app():  # noqa: C901 — маршруты объявляются линейно
    """Собрать приложение. FastAPI ставится дополнительно (`[api]`).

    Отсутствие FastAPI — не загадочный сбой импорта, а названная причина с
    командой, которой её чинят.
    """
    try:
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse
    except ImportError as exc:            # pragma: no cover - зависит от установки
        raise RuntimeError(
            "HTTP-поверхность требует дополнительной установки: "
            "pip install -e 'apps/social-farm[api]'") from exc

    app = FastAPI(title="BOSSMAN Social Farm", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        """Процесс жив. Ничего сверх этого здесь не утверждается."""
        return {"status": "ok", "app": "social-farm", "version": "0.1.0",
                "uptime_seconds": round(time.time() - STARTED_AT, 3),
                "implemented": ["health", "capabilities", "metrics"],
                "not_implemented": [op for op, _, _ in NOT_IMPLEMENTED]}

    @app.get("/api/capabilities")
    async def capabilities() -> dict[str, Any]:
        return {"capabilities": capability_catalogue(),
                "statuses": [{"status": s.value, "actionable": is_actionable(s),
                              "means": explain(s)} for s in CapabilityStatus]}

    @app.get("/api/metrics")
    async def metrics() -> dict[str, Any]:
        """Счётчики экрана. Ноль здесь означал бы «ни одного аккаунта», а
        правда — «хранилище в этой сборке не подключено». Разница видна."""
        return {"accounts": None, "pending_approvals": None,
                "measured": False,
                "reason": ("хранилище аккаунтов в этой сборке не подключено; "
                           "ноль здесь был бы неправдой")}

    def _refuse(operation: str):
        # Обработчик БЕЗ параметров, и это важнее, чем выглядит. С
        # `*args, **kwargs` FastAPI считает их обязательными параметрами
        # запроса и отвечает 422 — «вы неправильно позвали» вместо «операции
        # здесь нет». С `request: Request` то же самое: из-за
        # `from __future__ import annotations` аннотация остаётся строкой, а
        # `Request` импортирован внутри функции и на уровне модуля не
        # разрешается. Оба варианта выглядели правильно и оба отвечали 422;
        # поймано живым запросом, а не чтением кода.
        async def handler():
            return JSONResponse(
                status_code=501,
                content={"code": "NOT_IMPLEMENTED", "operation": operation,
                         "message": (f"операция {operation} объявлена контрактом "
                                     f"и в этой сборке не выполняется")})
        return handler

    for operation, method, path in NOT_IMPLEMENTED:
        app.add_api_route(path, _refuse(operation), methods=[method],
                          name=operation.replace(".", "_"),
                          include_in_schema=True)

    return app


__all__ = ["NOT_IMPLEMENTED", "build_app", "capability_catalogue"]
