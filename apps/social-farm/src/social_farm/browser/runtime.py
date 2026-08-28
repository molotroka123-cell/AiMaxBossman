"""Запуск настоящего браузера: постоянный контекст на аккаунт.

Единственное место, где создаётся Chromium. Всё, что здесь важно, — это два
слова из спецификации: **isolated persistent context per account**.

* *persistent* — контекст лежит в каталоге аккаунта и переживает перезапуск.
  Иначе каждый запуск требовал бы нового входа, то есть нового человека у
  экрана, и браузерный резерв не работал бы вовсе.
* *isolated* — каталог свой, права 0700, маркер владельца сверяется перед
  открытием. Общего профиля не существует ни в каком режиме.

Загрузки уходят в песочницу внутри каталога аккаунта, расширения не ставятся,
отладочный порт наружу не открывается (`32_BROWSER_SECURITY`).

Импорт Playwright ленивый: приложение обязано работать и проходить тесты без
установленной группы `browser`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import BrowserConfig
from .dom import BrowserUnavailable, PlaywrightDom
from .isolation import AccountContextRoot

DOWNLOADS_SUBDIR = "downloads"


@dataclass(slots=True)
class PlaywrightRuntime:
    """Хозяин процессов Chromium в одном воркере.

    В одном воркере живёт один аккаунт, поэтому и контекст здесь один. Класс
    всё же умеет несколько — но только с разными каталогами и только через
    `AccountContextRoot`, где каждый путь выводится из идентификатора аккаунта.
    """

    config: BrowserConfig = field(default_factory=BrowserConfig)
    _playwright: Any = None
    _contexts: dict[str, Any] = field(default_factory=dict)

    @property
    def context_root(self) -> AccountContextRoot:
        return AccountContextRoot(root=Path(self.config.context_root),
                                  mode=self.config.context_dir_mode)

    async def _start_playwright(self) -> Any:
        if self._playwright is not None:
            return self._playwright
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:                              # pragma: no cover
            raise BrowserUnavailable(
                "Playwright не установлен. Браузерный резерв — необязательная "
                "часть приложения: поставьте группу `browser` "
                "(pip install -e '.[browser]'), иначе этот путь недоступен, "
                "и приложение сообщает об этом честно"
            ) from exc
        self._playwright = await async_playwright().start()
        return self._playwright

    async def open(self, account_id: str) -> PlaywrightDom:
        """Открыть постоянный контекст аккаунта и вернуть порт к его странице."""
        root = self.context_root
        directory = root.prepare(account_id)
        # Проверяем перед КАЖДЫМ открытием, а не только при создании: каталог
        # мог быть подменён между запусками.
        root.assert_owned(account_id, directory)
        root.assert_private(directory)
        if account_id in self._contexts:                      # pragma: no cover
            context = self._contexts[account_id]
            pages = context.pages
            page = pages[0] if pages else await context.new_page()
            return self._port(page)

        downloads = directory / DOWNLOADS_SUBDIR
        downloads.mkdir(parents=True, exist_ok=True)
        playwright = await self._start_playwright()
        # Путь к браузеру задаётся настройкой: там, где Chromium уже стоит в
        # системе, второй экземпляр качать незачем, а тесты не вправе тянуть
        # его из сети.
        extra = ({"executable_path": self.config.chromium_executable}
                 if self.config.chromium_executable else {})
        context = await playwright.chromium.launch_persistent_context(
            str(directory),
            headless=self.config.headless,
            **extra,
            viewport={"width": 1440, "height": 900},
            accept_downloads=True,
            downloads_path=str(downloads),
            # Расширения не ставятся: чужой код в контексте аккаунта — это
            # чужой доступ к аккаунту.
            args=["--disable-extensions", "--no-first-run",
                  "--no-default-browser-check"],
        )
        self._contexts[account_id] = context
        pages = context.pages
        page = pages[0] if pages else await context.new_page()
        return self._port(page)

    def _port(self, page: Any) -> PlaywrightDom:
        return PlaywrightDom(page=page,
                             action_timeout_ms=self.config.action_timeout_ms,
                             navigation_timeout_ms=self.config.navigation_timeout_ms)

    async def close(self, account_id: str = "") -> None:
        targets = [account_id] if account_id else list(self._contexts)
        for key in targets:
            context = self._contexts.pop(key, None)
            if context is not None:
                try:
                    await context.close()
                except Exception:                             # pragma: no cover
                    pass
        if not self._contexts and self._playwright is not None:
            try:
                await self._playwright.stop()
            finally:
                self._playwright = None


__all__ = ["DOWNLOADS_SUBDIR", "PlaywrightRuntime"]
