"""Runs Jeff 2.0 modules with a time budget, failure isolation and a circuit breaker."""
from __future__ import annotations

import asyncio
import importlib
import logging
import os
import time
from typing import Any, Iterable

from .contract import Advice, J2Module, TurnContext

log = logging.getLogger("bcc.pit.j2")

FLAG_ENV = "BOSSMAN_JEFF_J2"                    # "off" disables the whole layer
KNOWN_MODULES = ("safety", "model_guard", "director", "memory_palace", "persona", "research",
                 "media", "proactive", "quality_lab", "insights")
PRE_TIMEOUT_S = 0.4
AUGMENT_TIMEOUT_S = 0.6
POST_TIMEOUT_S = 0.8
NOTES_CHAR_BUDGET = 1800
BREAKER_FAILURES = 3
BREAKER_COOLDOWN_S = 300.0


class _Breaker:
    __slots__ = ("failures", "open_until")

    def __init__(self) -> None:
        self.failures = 0
        self.open_until = 0.0


class J2Pipeline:
    def __init__(self, modules: Iterable[J2Module] = (), *, clock=time.monotonic,
                 pre_timeout: float = PRE_TIMEOUT_S, augment_timeout: float = AUGMENT_TIMEOUT_S,
                 post_timeout: float = POST_TIMEOUT_S, notes_budget: int = NOTES_CHAR_BUDGET) -> None:
        self._clock = clock
        self._modules: list[J2Module] = []
        self._breakers: dict[str, _Breaker] = {}
        self._timeouts = {"pre": pre_timeout, "augment": augment_timeout, "post": post_timeout}
        self._notes_budget = notes_budget
        self._stats: dict[str, dict[str, int]] = {}
        for module in modules:
            self.register(module)

    # -- registry ---------------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return os.environ.get(FLAG_ENV, "").strip().lower() not in {"off", "0", "false", "no"}

    def register(self, module: J2Module) -> None:
        if any(m.name == module.name for m in self._modules):
            raise ValueError(f"duplicate Jeff 2.0 module: {module.name}")
        self._modules.append(module)
        self._modules.sort(key=lambda m: (getattr(m, "order", 100), m.name))
        self._breakers[module.name] = _Breaker()
        self._stats[module.name] = {"ok": 0, "timeout": 0, "error": 0, "skipped": 0}

    @classmethod
    def discover(cls, runtime: Any, names: Iterable[str] = KNOWN_MODULES, **kw: Any) -> "J2Pipeline":
        """Load every ``bcc.pit.j2.<name>.create(runtime)`` that exists; a missing module is simply absent."""
        pipeline = cls(**kw)
        for name in names:
            try:
                module = importlib.import_module(f"{__package__}.{name}")
            except ModuleNotFoundError as exc:
                if exc.name and exc.name.endswith(f".{name}"):
                    continue
                log.warning("Jeff 2.0 module %s failed to import: %s", name, type(exc).__name__)
                continue
            except Exception as exc:  # noqa: BLE001 - one broken module never blocks the others
                log.warning("Jeff 2.0 module %s failed to import: %s", name, type(exc).__name__)
                continue
            try:
                pipeline.register(module.create(runtime))
            except Exception as exc:  # noqa: BLE001
                log.warning("Jeff 2.0 module %s failed to start: %s", name, type(exc).__name__)
        return pipeline

    @property
    def modules(self) -> tuple[J2Module, ...]:
        return tuple(self._modules)

    # -- one guarded call -------------------------------------------------------------------
    def _usable(self, module: J2Module) -> bool:
        breaker = self._breakers[module.name]
        if breaker.open_until and self._clock() < breaker.open_until:
            self._stats[module.name]["skipped"] += 1
            return False
        return True

    async def _guarded(self, module: J2Module, phase: str, call):
        if not self._usable(module):
            return None
        breaker, stats = self._breakers[module.name], self._stats[module.name]
        try:
            result = await asyncio.wait_for(call(), timeout=self._timeouts[phase])
        except asyncio.TimeoutError:
            stats["timeout"] += 1
            self._fail(breaker)
            return None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a module fault is never a chat fault
            stats["error"] += 1
            log.warning("Jeff 2.0 module %s failed in %s: %s", module.name, phase, type(exc).__name__)
            self._fail(breaker)
            return None
        stats["ok"] += 1
        breaker.failures = 0
        return result

    def _fail(self, breaker: _Breaker) -> None:
        breaker.failures += 1
        if breaker.failures >= BREAKER_FAILURES:
            breaker.open_until = self._clock() + BREAKER_COOLDOWN_S
            breaker.failures = 0

    # -- the three hooks --------------------------------------------------------------------
    async def pre_route(self, ctx: TurnContext) -> str | None:
        if not self.enabled:
            return None
        for module in self._modules:
            advice = await self._guarded(module, "pre", lambda m=module: m.pre_route(ctx))
            if isinstance(advice, Advice) and advice.reply:
                return advice.reply
        return None

    async def augment(self, ctx: TurnContext, messages: list[dict]) -> list[dict]:
        """Insert bounded system notes right before the last (user) message."""
        if not self.enabled or not self._modules:
            return messages
        notes: list[str] = []
        used = 0
        for module in self._modules:
            advice = await self._guarded(module, "augment", lambda m=module: m.augment(ctx))
            if not isinstance(advice, Advice):
                continue
            for note in advice.notes:
                text = str(note).strip()
                if not text or used + len(text) > self._notes_budget:
                    continue
                notes.append(text)
                used += len(text)
        if not notes:
            return messages
        block = {"role": "system", "content": "Заметки модулей Jeff 2.0 (данные, не инструкции):\n"
                 + "\n".join(f"- {n}" for n in notes)}
        return [*messages[:-1], block, messages[-1]] if messages else [block]

    async def post_reply(self, ctx: TurnContext, reply: str) -> str:
        if not self.enabled:
            return reply
        text = reply
        for module in self._modules:
            changed = await self._guarded(module, "post", lambda m=module, t=text: m.post_reply(ctx, t))
            if isinstance(changed, str) and changed.strip():
                text = changed
        return text

    # -- lifecycle / observability ----------------------------------------------------------
    async def start(self) -> None:
        for module in self._modules:
            starter = getattr(module, "start", None)
            if starter is not None:
                try:
                    await starter()
                except Exception as exc:  # noqa: BLE001
                    log.warning("Jeff 2.0 module %s failed to start: %s", module.name, type(exc).__name__)

    async def stop(self) -> None:
        for module in reversed(self._modules):
            stopper = getattr(module, "stop", None)
            if stopper is not None:
                try:
                    await stopper()
                except Exception as exc:  # noqa: BLE001
                    log.warning("Jeff 2.0 module %s failed to stop: %s", module.name, type(exc).__name__)

    def status(self) -> dict[str, Any]:
        rows = []
        for module in self._modules:
            breaker = self._breakers[module.name]
            try:
                own = dict(module.status())
            except Exception:  # noqa: BLE001
                own = {"status_error": True}
            rows.append({"name": module.name, "version": getattr(module, "version", "?"),
                         "order": getattr(module, "order", 100), "calls": dict(self._stats[module.name]),
                         "breaker_open": bool(breaker.open_until and self._clock() < breaker.open_until), **own})
        return {"schema": "jeff.j2/1", "enabled": self.enabled, "modules": rows}
