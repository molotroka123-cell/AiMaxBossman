"""Профили доступа к чату (мульти-пользователь) поверх Stage 6 device-identity.

Экспорт: `router` (HTTP /profiles) и `build_subsystem` (для lifecycle).
Enforcement — через `gate` (capability gate) и `service.computer_access_check`.
"""
from __future__ import annotations

from .router import router
from .subsystem import build_subsystem

__all__ = ["router", "build_subsystem"]
