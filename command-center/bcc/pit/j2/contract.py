"""The contract between the Jeff chat route and a Jeff 2.0 module.

Three optional hooks, all bounded by the pipeline:

* ``pre_route``  - runs before any model is called; may answer at once (safety, rate limit, quick facts);
* ``augment``    - returns extra system notes for the model (each note is DATA for the model, never an
  instruction that widens permissions); the pipeline caps their total size;
* ``post_reply`` - may adjust the final text and records what happened.

A module never receives another participant's data: ``TurnContext`` carries one ``person_key`` only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class TurnContext:
    person_key: str
    who: str
    text: str
    surface: str = "telegram"
    message_id: str = "0"
    memory_enabled: bool = False
    personalization_enabled: bool = True
    remote_processing_enabled: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Advice:
    """What a module adds to a turn. ``notes`` are system-role data lines; ``reply`` short-circuits the turn."""
    notes: tuple[str, ...] = ()
    reply: str | None = None
    tags: tuple[str, ...] = ()


@runtime_checkable
class J2Module(Protocol):
    name: str
    version: str
    order: int          # lower runs first; safety-like modules use small numbers

    async def pre_route(self, ctx: TurnContext) -> Advice | None: ...

    async def augment(self, ctx: TurnContext) -> Advice | None: ...

    async def post_reply(self, ctx: TurnContext, reply: str) -> str | None: ...

    def status(self) -> dict[str, Any]: ...


class BaseModule:
    """Convenience base: every hook is a no-op, so a module overrides only what it needs."""
    name = "base"
    version = "1"
    order = 100

    async def pre_route(self, ctx: TurnContext) -> Advice | None:
        return None

    async def augment(self, ctx: TurnContext) -> Advice | None:
        return None

    async def post_reply(self, ctx: TurnContext, reply: str) -> str | None:
        return None

    def status(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version}

    async def start(self) -> None:          # optional lifecycle for background modules
        return None

    async def stop(self) -> None:
        return None
