"""
Capability adapter registry for Bossman Hybrid OSS.
Default MUST remain legacy proven implementation until explicit acceptance.
Feature flags support safe fallback and instantaneous rollback.
"""

from __future__ import annotations
import os
import logging
from enum import Enum
from typing import Any, Dict, Optional, Type
from .capabilities import (
    DesktopRuntime,
    BrowserRuntime,
    WebEditorRuntime,
    VideoCompositionRuntime,
    LocalModelRuntime,
    BackendUnavailableError,
)

logger = logging.getLogger("bcc.hybrid.registry")


class CapabilityName(str, Enum):
    DESKTOP = "desktop"
    BROWSER = "browser"
    WEB_EDITOR = "web_editor"
    VIDEO = "video"
    LOCAL_MODEL = "local_model"


class BackendType(str, Enum):
    LEGACY = "legacy"
    WINDOWS_MCP = "windows_mcp"
    BROWSER_USE = "browser_use"
    GRAPESJS = "grapesjs"
    WEAVE = "weave"
    LOCALAI = "localai"


class AdapterRegistry:
    """Registry managing capability backends with guaranteed fallback to legacy."""

    def __init__(self) -> None:
        self._desktop_adapters: Dict[str, DesktopRuntime] = {}
        self._browser_adapters: Dict[str, BrowserRuntime] = {}
        self._web_editor_adapters: Dict[str, WebEditorRuntime] = {}
        self._video_adapters: Dict[str, VideoCompositionRuntime] = {}
        self._local_model_adapters: Dict[str, LocalModelRuntime] = {}

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

    def get_desktop_runtime(self) -> DesktopRuntime:
        target = os.getenv("BCC_DESKTOP_BACKEND", BackendType.LEGACY.value).lower()
        if target in self._desktop_adapters:
            return self._desktop_adapters[target]
        logger.warning("Desktop backend '%s' not registered, falling back to legacy", target)
        if BackendType.LEGACY.value in self._desktop_adapters:
            return self._desktop_adapters[BackendType.LEGACY.value]
        raise BackendUnavailableError("No desktop runtime adapter registered, including legacy")

    def get_browser_runtime(self) -> BrowserRuntime:
        target = os.getenv("BCC_BROWSER_BACKEND", BackendType.LEGACY.value).lower()
        if target in self._browser_adapters:
            return self._browser_adapters[target]
        logger.warning("Browser backend '%s' not registered, falling back to legacy", target)
        if BackendType.LEGACY.value in self._browser_adapters:
            return self._browser_adapters[BackendType.LEGACY.value]
        raise BackendUnavailableError("No browser runtime adapter registered, including legacy")

    def get_web_editor_runtime(self) -> WebEditorRuntime:
        target = os.getenv("BCC_WEB_EDITOR_BACKEND", BackendType.LEGACY.value).lower()
        if target in self._web_editor_adapters:
            return self._web_editor_adapters[target]
        logger.warning("Web editor backend '%s' not registered, falling back to legacy", target)
        if BackendType.LEGACY.value in self._web_editor_adapters:
            return self._web_editor_adapters[BackendType.LEGACY.value]
        raise BackendUnavailableError("No web editor runtime adapter registered, including legacy")

    def get_video_runtime(self) -> VideoCompositionRuntime:
        target = os.getenv("BCC_VIDEO_BACKEND", BackendType.LEGACY.value).lower()
        if target in self._video_adapters:
            return self._video_adapters[target]
        logger.warning("Video backend '%s' not registered, falling back to legacy", target)
        if BackendType.LEGACY.value in self._video_adapters:
            return self._video_adapters[BackendType.LEGACY.value]
        raise BackendUnavailableError("No video runtime adapter registered, including legacy")

    def get_local_model_runtime(self) -> LocalModelRuntime:
        target = os.getenv("BCC_LOCAL_MODEL_BACKEND", BackendType.LEGACY.value).lower()
        if target in self._local_model_adapters:
            return self._local_model_adapters[target]
        logger.warning("Local model backend '%s' not registered, falling back to legacy", target)
        if BackendType.LEGACY.value in self._local_model_adapters:
            return self._local_model_adapters[BackendType.LEGACY.value]
        raise BackendUnavailableError("No local model runtime adapter registered, including legacy")


# Global singleton instance
registry = AdapterRegistry()
