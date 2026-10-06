"""Jeff 2.0 module layer: independent modules behind one contract and one pipeline.

Every module lives in its own file ``bcc/pit/j2/<name>.py`` and exposes ``create(runtime) -> J2Module``.
The runtime talks only to :class:`~bcc.pit.j2.pipeline.J2Pipeline`, so a module can fail, time out or be
switched off without touching the chat route.
"""
from __future__ import annotations

from .contract import Advice, J2Module, TurnContext
from .pipeline import J2Pipeline

JEFF_2_SCHEMA = "jeff.j2/1"

__all__ = ["Advice", "J2Module", "J2Pipeline", "JEFF_2_SCHEMA", "TurnContext"]
