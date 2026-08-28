"""Числа браузерного резерва. Все — здесь, ни одного в коде действий.

Причина не в аккуратности. Порог «сколько отказов подряд считать сломанным
интерфейсом» и длина паузы после этого — это решения владельца о том, насколько
рано его беспокоить. Константа в коде превращает такое решение в свойство
сборки: чтобы передумать, нужен новый релиз. Здесь их можно переопределить
окружением (`SF_BROWSER_*`) или словарём из конфигурации аккаунта.

Чего в конфигурации НЕТ и не будет: выключателя проверки личности, выключателя
редакции секретов, выключателя отпечатка цели и порога «сколько раз попробовать
пройти проверку человека самому». Это не настройки — это границы.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

ENV_PREFIX = "SF_BROWSER_"


def _flag(raw: Any, default: bool) -> bool:
    if raw is None or raw == "":
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "да"}


def _int(raw: Any, default: int, *, low: int, high: int) -> int:
    if raw is None or raw == "":
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return max(low, min(value, high))


@dataclass(frozen=True, slots=True)
class BrowserConfig:
    """Настройки браузерного резерва.

    `enabled=False` по умолчанию намеренно: браузерный путь включается явно и
    только для тех аккаунтов, где владелец это разрешил (`DIGEST_CORE` G6).
    Забытая настройка означает «выключено», а не «работает как получится».
    """

    enabled: bool = False
    headless: bool = True
    context_root: Path = Path("./browser-contexts")
    # Путь к Chromium. Пустая строка означает «взять тот, что Playwright
    # поставил себе сам». Настройка нужна там, где браузер уже установлен
    # системой и ставить второй незачем — а также чтобы не запускать установку
    # браузера из тестов.
    chromium_executable: str = ""
    # Права каталога контекста. Ниже 0700 приложение не опускается: сессия
    # аккаунта в каталоге, доступном другим пользователям машины, — это чужой
    # вход в аккаунт, а не удобство.
    context_dir_mode: int = 0o700

    # Сколько подряд ДЕТЕРМИНИРОВАННЫХ отказов на одной версии пакета
    # селекторов означают, что интерфейс провайдера сменился.
    deterministic_failure_threshold: int = 3
    # Пауза, на которую возможность уходит в TEMPORARILY_DISABLED после этого.
    cooldown_minutes: int = 60
    # «refresh once» из `55_BROWSER_STATE_MACHINE`: одно обновление страницы,
    # затем запасные стратегии, затем BROKEN_UI. Не цикл.
    refresh_attempts: int = 1
    # Сколько повторов действия допускается внутри одной работы. Повтор здесь —
    # это повтор ПОИСКА цели, а не повтор внешнего эффекта.
    max_action_attempts: int = 2

    action_timeout_ms: int = 30_000
    navigation_timeout_ms: int = 60_000
    snapshot_max_text: int = 20_000
    snapshot_max_interactive: int = 200
    # Сколько ждать человека, прежде чем работа признаётся зависшей и уходит
    # владельцу отдельным уведомлением. Ожидание не отменяет передачу.
    takeover_deadline_minutes: int = 60
    # Срок жизни снимка возможностей браузерного пути (`DIGEST_CORE` G9).
    capability_snapshot_ttl_hours: int = 24

    def __post_init__(self) -> None:
        # Права каталога проверяются на КАЖДОМ построении настроек, а не только
        # при чтении окружения. Настройки аккаунта приходят данными, и
        # `merged({"context_dir_mode": 0o777})` иначе не просто открывал бы
        # сессию другим пользователям машины — он отключал бы и саму проверку
        # прав, потому что `assert_private` сверяется с этим же числом.
        mode = int(self.context_dir_mode)
        if mode & ~0o700:
            raise ValueError(
                f"права каталога контекста {oct(mode)} шире 0700: сессия аккаунта "
                f"не должна быть доступна другим пользователям машины. Это не "
                f"настройка, а граница")

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "BrowserConfig":
        source = dict(os.environ if env is None else env)
        base = cls()

        def get(name: str) -> Any:
            return source.get(ENV_PREFIX + name)

        root = source.get("SF_BROWSER_CONTEXT_ROOT") or ""
        return cls(
            # исторически флаг называется SF_BROWSER_FALLBACK (.env.example)
            enabled=_flag(source.get("SF_BROWSER_FALLBACK"), base.enabled),
            headless=_flag(get("HEADLESS"), base.headless),
            context_root=Path(root) if root else base.context_root,
            context_dir_mode=base.context_dir_mode,
            chromium_executable=str(get("CHROMIUM_PATH")
                                    or base.chromium_executable),
            deterministic_failure_threshold=_int(
                get("FAILURE_THRESHOLD"), base.deterministic_failure_threshold,
                low=1, high=100),
            cooldown_minutes=_int(get("COOLDOWN_MINUTES"), base.cooldown_minutes,
                                  low=1, high=60 * 24 * 30),
            refresh_attempts=_int(get("REFRESH_ATTEMPTS"), base.refresh_attempts,
                                  low=0, high=5),
            max_action_attempts=_int(get("MAX_ACTION_ATTEMPTS"),
                                     base.max_action_attempts, low=1, high=10),
            action_timeout_ms=_int(get("ACTION_TIMEOUT_MS"), base.action_timeout_ms,
                                   low=1_000, high=600_000),
            navigation_timeout_ms=_int(get("NAVIGATION_TIMEOUT_MS"),
                                       base.navigation_timeout_ms,
                                       low=1_000, high=600_000),
            snapshot_max_text=_int(get("SNAPSHOT_MAX_TEXT"), base.snapshot_max_text,
                                   low=500, high=1_000_000),
            snapshot_max_interactive=_int(get("SNAPSHOT_MAX_INTERACTIVE"),
                                          base.snapshot_max_interactive,
                                          low=10, high=5_000),
            takeover_deadline_minutes=_int(get("TAKEOVER_DEADLINE_MINUTES"),
                                           base.takeover_deadline_minutes,
                                           low=1, high=60 * 24 * 30),
            capability_snapshot_ttl_hours=_int(get("CAPABILITY_TTL_HOURS"),
                                               base.capability_snapshot_ttl_hours,
                                               low=1, high=24 * 30),
        )

    def merged(self, overrides: dict[str, Any] | None) -> "BrowserConfig":
        """Настройки аккаунта поверх общих. Неизвестные ключи — ошибка.

        Молча проглоченный ключ — это настройка, которую владелец считает
        применённой, а она не применена.
        """
        raw = dict(overrides or {})
        if not raw:
            return self
        known = {f for f in self.__slots__}
        unknown = set(raw) - known
        if unknown:
            raise ValueError(f"неизвестные настройки браузера: {sorted(unknown)}")
        if "context_root" in raw:
            raw["context_root"] = Path(raw["context_root"])
        return replace(self, **raw)

    def as_dict(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "headless": self.headless,
                "context_root": str(self.context_root),
                "context_dir_mode": oct(self.context_dir_mode),
                "chromium_executable": self.chromium_executable,
                "deterministic_failure_threshold": self.deterministic_failure_threshold,
                "cooldown_minutes": self.cooldown_minutes,
                "refresh_attempts": self.refresh_attempts,
                "max_action_attempts": self.max_action_attempts,
                "action_timeout_ms": self.action_timeout_ms,
                "navigation_timeout_ms": self.navigation_timeout_ms,
                "snapshot_max_text": self.snapshot_max_text,
                "snapshot_max_interactive": self.snapshot_max_interactive,
                "takeover_deadline_minutes": self.takeover_deadline_minutes,
                "capability_snapshot_ttl_hours": self.capability_snapshot_ttl_hours}


__all__ = ["ENV_PREFIX", "BrowserConfig"]
