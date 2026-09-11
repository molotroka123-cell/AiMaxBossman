"""
Capability adapter registry for Bossman Hybrid OSS.

The default remains the proven legacy implementation until explicit acceptance.
Feature flags can select an optional backend, but selection is health-gated and
must fail closed rather than returning a backend already reporting unhealthy.
"""

from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Any, Dict

from .capabilities import (
    BackendUnavailableError,
    BrowserRuntime,
    ContextStoreRuntime,
    DesktopRuntime,
    LocalModelRuntime,
    VideoCompositionRuntime,
    WebEditorRuntime,
)

logger = logging.getLogger("bcc.hybrid.registry")


class CapabilityName(str, Enum):
    DESKTOP = "desktop"
    BROWSER = "browser"
    WEB_EDITOR = "web_editor"
    VIDEO = "video"
    LOCAL_MODEL = "local_model"
    CONTEXT_STORE = "context_store"


class BackendType(str, Enum):
    LEGACY = "legacy"
    WINDOWS_MCP = "windows_mcp"
    BROWSER_USE = "browser_use"
    GRAPESJS = "grapesjs"
    WEAVE = "weave"
    LOCALAI = "localai"
    # Родная память — ОТДЕЛЬНОЕ имя бэкенда, а не LEGACY: для контекст-стора
    # "legacy" означало бы «прежняя реализация того же механизма», а здесь
    # родное хранилище не прежнее, оно авторитетное и остаётся таким.
    BOSSMAN_NATIVE = "bossman_native"
    OPENCONTEXT = "opencontext"


class AdapterRegistry:
    """Registry managing capability backends with health-gated legacy fallback."""

    def __init__(self) -> None:
        self._desktop_adapters: Dict[str, DesktopRuntime] = {}
        self._browser_adapters: Dict[str, BrowserRuntime] = {}
        self._web_editor_adapters: Dict[str, WebEditorRuntime] = {}
        self._video_adapters: Dict[str, VideoCompositionRuntime] = {}
        self._local_model_adapters: Dict[str, LocalModelRuntime] = {}
        self._context_store_adapters: Dict[str, ContextStoreRuntime] = {}

    def register_desktop(self, backend: str, adapter: DesktopRuntime) -> None:
        self._desktop_adapters[backend.lower()] = adapter

    def register_browser(self, backend: str, adapter: BrowserRuntime) -> None:
        self._browser_adapters[backend.lower()] = adapter

    def register_web_editor(self, backend: str, adapter: WebEditorRuntime) -> None:
        self._web_editor_adapters[backend.lower()] = adapter

    def register_video(self, backend: str, adapter: VideoCompositionRuntime) -> None:
        self._video_adapters[backend.lower()] = adapter

    def register_local_model(self, backend: str, adapter: LocalModelRuntime) -> None:
        self._local_model_adapters[backend.lower()] = adapter

    def register_context_store(self, backend: str, adapter: ContextStoreRuntime) -> None:
        self._context_store_adapters[backend.lower()] = adapter

    @staticmethod
    def _adapter_is_healthy(adapter: Any) -> bool:
        """Treat missing, malformed, or throwing health identity as unhealthy."""
        try:
            identity = adapter.get_runtime_identity()
        except Exception as exc:  # noqa: BLE001 - third-party adapter boundary
            logger.warning("Adapter health probe raised %s: %s", type(exc).__name__, exc)
            return False
        return getattr(identity, "is_healthy", False) is True

    def _resolve_runtime(
        self,
        adapters: Dict[str, Any],
        *,
        target: str,
        capability: str,
        fallback: str = BackendType.LEGACY.value,
    ) -> Any:
        legacy_key = fallback
        selected = adapters.get(target)

        if selected is not None and self._adapter_is_healthy(selected):
            return selected

        if selected is None:
            logger.warning(
                "%s backend '%s' not registered; attempting healthy legacy fallback",
                capability,
                target,
            )
        else:
            logger.warning(
                "%s backend '%s' is unhealthy; attempting healthy legacy fallback",
                capability,
                target,
            )

        # If the requested backend itself is legacy, there is no second backend
        # to silently fall through to. The unhealthy state must remain visible.
        fallback_adapter = adapters.get(legacy_key)
        if fallback_adapter is not None and self._adapter_is_healthy(fallback_adapter):
            return fallback_adapter

        # Имя запасного бэкенда называется, а не подразумевается: у контекст-стора
        # запасной — `bossman_native`, и сообщение «legacy adapter is unhealthy»
        # отправило бы читателя искать несуществующий legacy-адаптер.
        if fallback_adapter is None:
            raise BackendUnavailableError(
                f"No healthy {capability} runtime available: "
                f"{legacy_key} adapter is not registered"
            )
        raise BackendUnavailableError(
            f"No healthy {capability} runtime available: {legacy_key} adapter is unhealthy"
        )

    def get_desktop_runtime(self) -> DesktopRuntime:
        target = os.getenv("BCC_DESKTOP_BACKEND", BackendType.LEGACY.value).strip().lower()
        return self._resolve_runtime(
            self._desktop_adapters,
            target=target,
            capability="desktop",
        )

    def get_browser_runtime(self) -> BrowserRuntime:
        target = os.getenv("BCC_BROWSER_BACKEND", BackendType.LEGACY.value).strip().lower()
        return self._resolve_runtime(
            self._browser_adapters,
            target=target,
            capability="browser",
        )

    def get_web_editor_runtime(self) -> WebEditorRuntime:
        target = os.getenv("BCC_WEB_EDITOR_BACKEND", BackendType.LEGACY.value).strip().lower()
        return self._resolve_runtime(
            self._web_editor_adapters,
            target=target,
            capability="web editor",
        )

    def get_video_runtime(self) -> VideoCompositionRuntime:
        target = os.getenv("BCC_VIDEO_BACKEND", BackendType.LEGACY.value).strip().lower()
        return self._resolve_runtime(
            self._video_adapters,
            target=target,
            capability="video",
        )

    def get_local_model_runtime(self) -> LocalModelRuntime:
        target = os.getenv("BCC_LOCAL_MODEL_BACKEND", BackendType.LEGACY.value).strip().lower()
        return self._resolve_runtime(
            self._local_model_adapters,
            target=target,
            capability="local model",
        )

    def get_context_store_runtime(self) -> ContextStoreRuntime:
        """Авторитетный контекст-стор. По умолчанию и по замыслу — родной.

        Переменная окружения здесь НЕ выбирает OpenContext: селектор
        существует, чтобы будущий бэкенд можно было подключить после
        длительной проверки эквивалентности (§7), а fallback ведёт в
        `bossman_native`, а не в `legacy`, — у памяти проекта нет «прежней
        реализации», у неё есть авторитетный владелец.
        """
        target = os.getenv("BCC_CONTEXT_STORE_BACKEND",
                           BackendType.BOSSMAN_NATIVE.value).strip().lower()
        return self._resolve_runtime(
            self._context_store_adapters,
            target=target,
            capability="context store",
            fallback=BackendType.BOSSMAN_NATIVE.value,
        )


# Global singleton instance
registry = AdapterRegistry()
