"""Точка расширения V2 (контракты §8): каждая из 15 функций — модуль этого пакета.

Модуль экспортирует FEATURE = Feature(...):
  name          — короткое имя (совпадает с именем файла)
  router        — APIRouter с endpoint'ами фичи (или None); монтируется под /api
                  с токен-auth автоматически
  setup(svc)    — async-инициализация на старте: регистрация хуков engine,
                  подписка на шину и т.п. (или None)
  tick(svc)     — периодическая фоновая работа (Governor, Healing, истечение
                  резервов); зовётся каждые tick_seconds (0 = не звать)

svc — Services из bcc.api: db, bus, engine, registry, scheduler, approvals,
metrics, vault, settings. Общие файлы фичи не трогают — только свой модуль.
"""
from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from fastapi import APIRouter


@dataclass
class Feature:
    name: str
    router: APIRouter | None = None
    setup: Callable[[Any], Awaitable[None]] | None = None
    tick: Callable[[Any], Awaitable[None]] | None = None
    tick_seconds: float = 0.0


def load_features() -> list[Feature]:
    """Импортирует все модули пакета и собирает их FEATURE. Падение одного
    модуля не прячется — интеграция должна видеть ошибку сразу."""
    features: list[Feature] = []
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        feature = getattr(module, "FEATURE", None)
        if isinstance(feature, Feature):
            features.append(feature)
    features.sort(key=lambda f: f.name)
    return features
