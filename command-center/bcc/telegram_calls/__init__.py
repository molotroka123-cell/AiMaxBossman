"""Telegram live calls: real two-way voice with Bossman over a personal 1:1 call.

Same product, additional control surface: the dashboard panel, ``bossman call``
and the worker all use the ONE Command Center backend, Jeff's existing context/voice/rules, the local model
routes of the Bossman product, the existing Vault for secrets and the existing memory.
See docs/telegram-calls/ARCHITECTURE.md.

Heavy/native dependencies (Telethon, py-tgcalls/ntgcalls, onnxruntime, ...) are the
optional ``calls`` extra and are imported lazily inside the worker only, so importing
this package never requires them.
"""
from __future__ import annotations

from .types import (  # noqa: F401  (public contract)
    AccountState, AudioFormat, CallError, CallEvent, CallRecord, CallState, CallSummary,
    CallTransport, CancelToken, Outcome, PeerRef, Phase, STTEngine, STTResult, STTStream,
    TransportEvent, TransportEventKind, TTSEngine, Turn, TurnMetrics, Brain, ERRORS)

MODULE_VERSION = "0.1.0"
