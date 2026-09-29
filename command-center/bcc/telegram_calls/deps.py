"""Dependency probe for the optional ``calls`` extra. Never imports the heavy packages, only looks for them."""
from __future__ import annotations

import importlib.metadata
import importlib.util

#: import name -> (pip distribution, required for a real Telegram call?)
PACKAGES: dict[str, tuple[str, bool]] = {
    "telethon": ("telethon", True),
    "pytgcalls": ("py-tgcalls", True),
    "ntgcalls": ("ntgcalls", True),
    "numpy": ("numpy", True),
    "soxr": ("soxr", False),                    # better resampling; numpy fallback exists
    "pysilero_vad": ("pysilero-vad", False),    # real VAD; flagged energy fallback exists
    "faster_whisper": ("faster-whisper", True),   # Jeff's existing Whisper tract (bcc.oss.whisper / bcc.pit.speech)
}


def version_of(dist: str) -> str | None:
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return None


def probe() -> dict:
    """{module: {"installed", "version", "required"}, "missing_required": [...], "ready_for_telegram_call": bool}."""
    out: dict = {}
    missing: list[str] = []
    for module, (dist, required) in PACKAGES.items():
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            found = False
        out[module] = {"installed": found, "version": version_of(dist) if found else None, "required": required, "dist": dist}
        if required and not found:
            missing.append(dist)
    return {"packages": out, "missing_required": missing, "ready_for_telegram_call": not missing}


def engine_selfcheck() -> dict:
    """Build the REAL py-tgcalls engine against a never-connected Telethon client. No network, no call.

    Catches version drift (renamed classes, moved imports) at install time instead of in the middle of a call.
    """
    import asyncio

    async def run() -> dict:
        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession
        except Exception:  # noqa: BLE001
            return {"ok": False, "reason": "telethon_missing"}
        try:
            from pytgcalls.types import CallConfig, ExternalMedia, MediaStream, RecordStream  # noqa: F401
            from pytgcalls.types.raw import AudioParameters
        except Exception:  # noqa: BLE001
            return {"ok": False, "reason": "py_tgcalls_missing_or_incompatible"}
        from .call.pytgcalls_transport import FRAME_MS, RX_RATE, TX_RATE, PyTgCallsEngine, PyTgCallsTransport
        try:
            client = TelegramClient(StringSession(), 1, "0" * 32)
            transport = PyTgCallsTransport(PyTgCallsEngine(client))
            MediaStream(ExternalMedia.AUDIO, audio_parameters=AudioParameters(TX_RATE, 1))
            RecordStream(audio=True, audio_parameters=AudioParameters(RX_RATE, 1))
            return {"ok": True, "frame_bytes": transport.audio_format.frame_bytes(FRAME_MS)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": "engine_build_failed", "detail": type(exc).__name__}

    try:
        return asyncio.run(run())
    except RuntimeError:
        return {"ok": False, "reason": "event_loop_running"}
