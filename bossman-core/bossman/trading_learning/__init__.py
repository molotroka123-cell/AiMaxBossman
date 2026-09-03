"""K1MBA_TRADING_LEARNING_LAB — обучение на материалах трейдера с проверкой данными.

Замысел: K1mba — источник учебного материала и ГИПОТЕЗ, а не оракул. Модуль
превращает материал в типизированные claim'ы, а затем сам проверяет, где логика
работает статистически, а где это красивое объяснение постфактум.

Режим безопасности (safety.py): TRADING_EXECUTION=OFF, PAPER_TRADING_ONLY=true,
OWNER_APPROVAL_REQUIRED=true, EXTERNAL_WRITE_ACTIONS=DENY. Клиента биржи на
запись в модуле нет физически.

Импорты ленивые (как в bossman.cost_control): подключение модуля к API не
должно тянуть cv2 и историю в момент старта процесса.
"""
from __future__ import annotations

from .safety import (EXTERNAL_WRITE_ACTIONS, OWNER_APPROVAL_REQUIRED,
                     PAPER_TRADING_ONLY, TRADING_EXECUTION, EvidenceClass,
                     LiveExecutionForbidden, OwnerApproval, OwnerApprovalRequired,
                     UnknownProviderPrice, assert_no_live_execution)

__all__ = [
    "TRADING_EXECUTION", "PAPER_TRADING_ONLY", "OWNER_APPROVAL_REQUIRED",
    "EXTERNAL_WRITE_ACTIONS", "EvidenceClass", "OwnerApproval",
    "LiveExecutionForbidden", "OwnerApprovalRequired", "UnknownProviderPrice",
    "assert_no_live_execution", "router", "pipeline_status", "run_benchmark",
]


def __getattr__(name: str):
    if name == "router":
        from .routes import router
        return router
    if name == "pipeline_status":
        from .routes import pipeline_status
        return pipeline_status
    if name == "run_benchmark":
        from .benchmark import run_benchmark
        return run_benchmark
    raise AttributeError(name)
