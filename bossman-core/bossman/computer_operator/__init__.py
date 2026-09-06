"""Оператор компьютера: чистые модели и политика — и отдельно всё остальное.

`routes` тянет FastAPI, а `subsystem` через `approvals`/`db` — asyncpg. Пока они
импортировались здесь безусловно, любой импорт ЧИСТОЙ модели
(`from bossman.computer_operator.models import ActionKind`) требовал и веб-фреймворка,
и драйвера базы: корневой набор тестов ставит только общие контракты и падал на
сборе с ModuleNotFoundError.

Тяжёлые имена отдаются лениво (PEP 562), поэтому
`from bossman.computer_operator import router, build_subsystem, ComputerOperatorManager`
работает как раньше, а модель остаётся моделью.
"""
from .models import *
from .policy import ComputerPolicy

_LAZY = {
    "router": (".routes", "router"),
    "build_subsystem": (".subsystem", "build_subsystem"),
    "ComputerOperatorManager": (".manager", "ComputerOperatorManager"),
}


def __getattr__(name: str):
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module
    return getattr(import_module(target[0], __name__), target[1])


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY})
