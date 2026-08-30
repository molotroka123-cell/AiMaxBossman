from .subsystem import build_subsystem

__all__ = ["build_subsystem", "router"]


def __getattr__(name):
    # Ленивая выдача router: _include_stage_routers() в api.py берёт его как
    # getattr(module, "router") на уровне пакета — без этого все ручки
    # /world_intelligence/* молча выпадали из приложения.
    if name == "router":
        from .routes import router
        return router
    raise AttributeError(name)
