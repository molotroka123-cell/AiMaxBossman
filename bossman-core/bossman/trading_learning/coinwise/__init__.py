"""Наблюдение за дашбордом Coinwise — только чтение.

Модуль умеет ровно одно: прочитать дашборд, который владелец открыл сам, и
сказать, что на нём видно, а чего не видно. Он не торгует, не входит в
аккаунт, не обходит ограничения сайта и не отправляет ничего наружу.

Импорты ленивые, как во всём trading_learning: подключение к API не должно
тянуть OCR в момент старта процесса.
"""
from __future__ import annotations

COINWISE_MODE = "READ_ONLY"
TRADING_EXECUTION = "OFF"
PAPER_TRADING_ONLY = True
OWNER_APPROVAL_REQUIRED = True
CLOUD_VISION_DEFAULT = False

__all__ = ["COINWISE_MODE", "TRADING_EXECUTION", "PAPER_TRADING_ONLY",
           "OWNER_APPROVAL_REQUIRED", "CLOUD_VISION_DEFAULT",
           "CoinwiseObservation", "MarketState", "MarketRead", "Snapshot",
           "Binding", "ObservationRefused", "observe", "classify", "remember"]


# Куда за каким именем идти. Таблица, а не цепочка `from . import`: подмодуль
# `classify` и функция `classify` называются одинаково, и ленивый импорт по
# имени модуля уходил бы в бесконечную рекурсию через этот же __getattr__.
_SOURCES: dict[str, tuple[str, str]] = {
    "CoinwiseObservation": (".schema", "CoinwiseObservation"),
    "MarketState": (".classify", "MarketState"),
    "MarketRead": (".classify", "MarketRead"),
    "classify": (".classify", "classify"),
    "Snapshot": (".observer", "Snapshot"),
    "observe": (".observer", "observe"),
    "remember": (".observer", "remember"),
    "Binding": (".gate", "Binding"),
    "ObservationRefused": (".gate", "ObservationRefused"),
}


def __getattr__(name: str):
    target = _SOURCES.get(name)
    if target is None:
        raise AttributeError(name)
    from importlib import import_module

    module, attribute = target
    return getattr(import_module(module, __name__), attribute)
