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
    BackendVersionMismatchError,
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
        # Закреплённая версия бэкенда: (возможность, бэкенд) -> ожидаемая строка.
        # Пусто по умолчанию — пин это осознанное действие того, кто
        # РЕГИСТРИРУЕТ адаптер, а не догадка реестра.
        self._pinned_versions: Dict[tuple, str] = {}

    def pin_version(self, capability: str, backend: str, version: str) -> None:
        """Закрепить версию бэкенда (обычно — точный SHA из sources.lock.json).

        Раздел 18 требует записывать точный SHA каждого внешнего проекта, а
        раздел 22 — отрабатывать отказ «неверная версия». Без этой связки
        закрепление остаётся утверждением в документе: рантайм не проверял
        НИКОГДА, что запущено именно то, что разбиралось по лицензии.
        """
        if not version or not version.strip():
            raise ValueError("пустая закреплённая версия ничего не закрепляет")
        self._pinned_versions[(capability, backend.lower())] = version.strip()

    def register_desktop(self, backend: str, adapter: DesktopRuntime, *,
                         pinned_version: str | None = None) -> None:
        self._desktop_adapters[backend.lower()] = adapter
        if pinned_version:
            self.pin_version(CapabilityName.DESKTOP.value, backend, pinned_version)

    def register_browser(self, backend: str, adapter: BrowserRuntime, *,
                         pinned_version: str | None = None) -> None:
        self._browser_adapters[backend.lower()] = adapter
        if pinned_version:
            self.pin_version(CapabilityName.BROWSER.value, backend, pinned_version)

    def register_web_editor(self, backend: str, adapter: WebEditorRuntime, *,
                            pinned_version: str | None = None) -> None:
        self._web_editor_adapters[backend.lower()] = adapter
        if pinned_version:
            self.pin_version(CapabilityName.WEB_EDITOR.value, backend, pinned_version)

    def register_video(self, backend: str, adapter: VideoCompositionRuntime, *,
                       pinned_version: str | None = None) -> None:
        self._video_adapters[backend.lower()] = adapter
        if pinned_version:
            self.pin_version(CapabilityName.VIDEO.value, backend, pinned_version)

    def register_local_model(self, backend: str, adapter: LocalModelRuntime, *,
                             pinned_version: str | None = None) -> None:
        self._local_model_adapters[backend.lower()] = adapter
        if pinned_version:
            self.pin_version(CapabilityName.LOCAL_MODEL.value, backend, pinned_version)

    def register_context_store(self, backend: str, adapter: ContextStoreRuntime, *,
                               pinned_version: str | None = None) -> None:
        self._context_store_adapters[backend.lower()] = adapter
        if pinned_version:
            self.pin_version(CapabilityName.CONTEXT_STORE.value, backend, pinned_version)

    def _version_matches_pin(self, adapter: Any, *, capability: str, backend: str) -> bool:
        """Совпадает ли объявленная адаптером версия с закреплённой.

        Незакреплённый бэкенд считается совпадающим: пин необязателен. А вот
        закреплённый, который НЕ МОЖЕТ назвать свою версию, совпадающим не
        считается — «версия неизвестна» это не «версия та самая».
        """
        expected = self._pinned_versions.get((capability, backend.lower()))
        if expected is None:
            return True
        try:
            actual = getattr(adapter.get_runtime_identity(), "version", None)
        except Exception as exc:  # noqa: BLE001 - граница стороннего адаптера
            logger.warning("Version probe raised %s: %s", type(exc).__name__, exc)
            return False
        if not isinstance(actual, str) or not actual.strip():
            logger.warning(
                "%s backend '%s' is pinned to %s but declares no version",
                capability, backend, expected)
            return False
        if actual.strip() != expected:
            logger.warning(
                "%s backend '%s' is pinned to %s but is running %s",
                capability, backend, expected, actual.strip())
            return False
        return True

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

        # Порядок важен: сначала здоровье, потом версия. Больной бэкенд не
        # обязан уметь называть версию, и сообщать о «несовпадении версии» там,
        # где на самом деле мёртв процесс, значит увести читателя не туда.
        if selected is not None and self._adapter_is_healthy(selected):
            if self._version_matches_pin(selected, capability=capability, backend=target):
                return selected
            # Несовпадение версии — это утверждение о ПРОИСХОЖДЕНИИ, а не
            # временная неисправность: запущено не то, что разбиралось по
            # лицензии и поведению (раздел 18). Поэтому такой бэкенд не
            # используется. Продукт при этом не ломается: ниже тот же откат на
            # проверенную реализацию, что и для больного бэкенда.
            if target == legacy_key:
                raise BackendVersionMismatchError(
                    f"{capability} backend '{target}' does not match its pinned version; "
                    f"there is no second backend to fall back to")
            logger.warning(
                "%s backend '%s' rejected on version pin; attempting legacy fallback",
                capability, target)
            fallback_adapter = adapters.get(legacy_key)
            if fallback_adapter is not None and self._adapter_is_healthy(fallback_adapter) \
                    and self._version_matches_pin(fallback_adapter, capability=capability,
                                                  backend=legacy_key):
                return fallback_adapter
            raise BackendVersionMismatchError(
                f"{capability} backend '{target}' does not match its pinned version "
                f"and no healthy {legacy_key} fallback is available")

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
        if fallback_adapter is not None and self._adapter_is_healthy(fallback_adapter) \
                and self._version_matches_pin(fallback_adapter, capability=capability,
                                              backend=legacy_key):
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
