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
    "faster_whisper": ("faster-whisper", True),
    "piper": ("piper-tts", True),
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
