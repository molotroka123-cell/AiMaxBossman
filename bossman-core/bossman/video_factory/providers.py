"""Провайдеры генерации видео + связка с политикой ComputerUse.

Два вида провайдеров:
1. `SyntheticFFmpegProvider` — детерминированный ЛОКАЛЬНЫЙ провайдер (реальный
   короткий mp4 через ffmpeg testsrc). Им пользуются тесты и деградационный
   режим. Текст промпта в argv НЕ попадает (см. ffmpeg.build_testsrc_argv).
2. Браузерный провайдер (веб-UI, где надо нажать «Generate») — НЕ реализуем
   реально, но даём ШОВ политики, который физически не даёт авто-сабмита сквозь
   стену: `assert_browser_provider_allowed` + `GuardedBrowserProvider`.

Связка с `toolkit/browser.py` (read-only переиспользование его политики):
- `domain_risk(url) == "blocked"` → STOP (PolicyDenied);
- `BLOCKERS` (captcha / rate-limit / anti-bot / access-denied) на тексте
  страницы → STOP (PolicyDenied), НИКОГДА не решаем и не обходим;
- финальный «Generate/Submit» идёт ТОЛЬКО через approval-gated путь
  (confirm=True, действие паркуется), а не авто-клик.

Запрещено (ТЗ, дословно): CAPTCHA bypass, anti-bot stealth, fingerprint
spoofing, обход rate-limit. Браузерный провайдер на стене ОСТАНАВЛИВАЕТСЯ.
"""
from __future__ import annotations

from pathlib import Path
from typing import Awaitable, Callable, Protocol, runtime_checkable

from .. import errors
from .ffmpeg import next_take_path, run_testsrc

# Политику берём из общего шва браузера (read-only). Импорт терпимый: пакет
# video_factory обязан импортироваться даже там, где playwright/браузер не
# подъехали — тогда используем локальную копию правил с той же семантикой.
try:
    from ..toolkit.browser import BLOCKERS, domain_risk, is_sensitive_label
except Exception:  # noqa: BLE001 - pragma: no cover
    import re as _re

    BLOCKERS = _re.compile(
        r"captcha|verify you are human|too many requests|rate limit|"
        r"automation (?:is )?not allowed|bot detected|unusual traffic|access denied",
        _re.I,
    )

    def domain_risk(url: str) -> str:  # type: ignore[misc]
        return "normal"

    def is_sensitive_label(label: str) -> bool:  # type: ignore[misc]
        return False


@runtime_checkable
class VideoProvider(Protocol):
    """Контракт провайдера: сгенерировать один клип и вернуть путь к файлу.

    Провайдер НИКОГДА не вызывается без удержанной аренды Resource Brain — это
    гарантирует конвейер (см. pipeline._generate_once). Провайдер обязан писать
    новый дубль и не перезаписывать предыдущий (см. next_take_path)."""

    async def generate(self, *, prompt: str, duration_s: float, output_dir: str) -> str:
        ...


class SyntheticFFmpegProvider:
    """Локальный детерминированный провайдер: реальный короткий mp4 через ffmpeg.

    Пишет `take-NNN.mp4` в каталог сцены, НИКОГДА не затирая предыдущий дубль.
    Текст промпта НЕ используется в argv (инвариант безопасности)."""

    name = "synthetic-ffmpeg"

    async def generate(self, *, prompt: str, duration_s: float, output_dir: str) -> str:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        take = next_take_path(out_dir)  # take-NNN.mp4 — без перезаписи
        # prompt намеренно НЕ передаётся в ffmpeg-argv.
        return await run_testsrc(take, duration_s)


# --- шов политики браузерного провайдера ------------------------------------

def assert_browser_provider_allowed(
    url: str,
    page_text: str = "",
    *,
    submitting: bool = False,
    confirmed: bool = False,
) -> None:
    """Гейт браузерного провайдера. Бросает `errors.PolicyDenied`, если:

    - домен заблокирован политикой (`domain_risk == "blocked"`);
    - на странице обнаружена СТЕНА `BLOCKERS` (captcha / rate-limit / anti-bot /
      access-denied) — останавливаемся, НИКОГДА не решаем и не обходим;
    - это финальный сабмит (`submitting=True`), но он НЕ прошёл approval-gated
      подтверждение (`confirmed=False`) — авто-сабмит запрещён.

    Вызывается ДО любого клика/сабмита, поэтому провайдер физически не может
    нажать «Generate» сквозь стену или без подтверждения."""
    if domain_risk(url) == "blocked":
        raise errors.PolicyDenied(
            "browser video provider: domain blocked by policy; stop",
            extra={"stage": "domain"},
        )
    blocker = BLOCKERS.search(page_text or "")
    if blocker:
        # В extra кладём только КАТЕГОРИЮ стены, не сырой текст страницы.
        raise errors.PolicyDenied(
            "browser video provider: STOP wall detected; never bypass",
            extra={"stage": "blocker", "wall": blocker.group(0)[:40]},
        )
    if submitting and not confirmed:
        raise errors.PolicyDenied(
            "browser video provider: final submit must go through approval-gated "
            "confirmed path (no auto-submit)",
            extra={"stage": "submit"},
        )


class GuardedBrowserProvider:
    """Каркас браузерного провайдера, который ФИЗИЧЕСКИ не может авто-сабмитить.

    Мы НЕ реализуем реальный сторонний веб-провайдер — только политический шов.
    Зависимости инъектируются (в проде их дал бы runner поверх toolkit/browser):
    - `fetch_page()` — прочитать текст текущей страницы (browser.observe/extract);
    - `request_approval()` — припарковать confirm=True действие и дождаться
      решения (runner → approvals.create/wait); вернуть True/False;
    - `do_submit()` — собственно нажать «Generate» (browser.confirmed_click) и
      вернуть путь к результату.

    Порядок в `generate` жёсткий: сначала STOP-гейт на стенах, затем approval,
    затем ещё раз гейт с `submitting=True, confirmed=<решение>`, и только потом
    `do_submit()`. Нет ни одной ветки, где `do_submit` вызывается без
    подтверждения или сквозь captcha."""

    name = "guarded-browser"

    def __init__(
        self,
        *,
        url: str,
        fetch_page: Callable[[], Awaitable[str]],
        request_approval: Callable[[], Awaitable[bool]],
        do_submit: Callable[[], Awaitable[str]],
    ) -> None:
        self.url = url
        self._fetch_page = fetch_page
        self._request_approval = request_approval
        self._do_submit = do_submit

    async def generate(self, *, prompt: str, duration_s: float, output_dir: str) -> str:
        # 1) Прочитать страницу и остановиться на любой стене (captcha/rate-limit/
        #    blocked). Здесь ветка до сабмита — обход невозможен.
        page_text = await self._fetch_page()
        assert_browser_provider_allowed(self.url, page_text)

        # 2) Финальный сабмит — только через approval-gated подтверждение.
        approved = bool(await self._request_approval())

        # 3) Повторный гейт уже с submitting=True: без подтверждения — PolicyDenied.
        assert_browser_provider_allowed(
            self.url, page_text, submitting=True, confirmed=approved
        )
        if not approved:  # избыточно (гейт уже бросил), но делает намерение явным
            raise errors.PolicyDenied("browser video provider: submit not approved")

        # 4) Только теперь — реальное нажатие «Generate».
        return await self._do_submit()
