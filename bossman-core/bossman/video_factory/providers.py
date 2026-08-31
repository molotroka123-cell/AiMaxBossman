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

# --- OpenRouter Video: тонкий адаптер к тому же протоколу VideoProvider -----

class OpenRouterVideoProvider:
    """Тонкий адаптер OpenRouter `/api/v1/videos` (async submit→poll→download).

    Архитектурные правила:
    - Это НЕ второй движок: класс реализует тот же `VideoProvider`-протокол и
      подключается в `VideoFactory(provider=...)` как обычный провайдер.
    - Opt-in и ничего по умолчанию: нужен явный `api_key` (или
      `OPENROUTER_API_KEY`) и включённый фичевый флаг
      `BOSSMAN_VIDEO_OPENROUTER=1` (см. `enabled()`); облако никогда не
      вызывается молча.
    - Бюджетный guard: `budget_cap` (USD, накопительно по провайдеру) —
      превышение → `VideoProviderFailed` ДО нового сабмита.
    - Все ошибки провайдера/сети → `errors.VideoProviderFailed` (пайплайн
      ведёт учёт попыток и пишет новый дубль `take-NNN.mp4`).
    - Тестируемость: `client` инъектируется (async-клиент с
      post/get(url, ...)); в тестах — стаб, в проде — httpx.AsyncClient.

    Ref-изображения передаются как data-URI через `input_references`
    (reference-to-video); провайдер сам их не читает с диска — путь/URI даёт
    вызывающая сторона через `extra_payload` или `set_references()`.
    """

    name = "openrouter-video"

    def __init__(
        self,
        *,
        model: str = "bytedance/seedance-2.0-mini",
        api_key: str | None = None,
        base_url: str = "https://openrouter.ai/api/v1",
        resolution: str = "720p",
        aspect_ratio: str = "9:16",
        budget_cap: float | None = None,
        poll_interval_s: float = 20.0,
        poll_timeout_s: float = 900.0,
        extra_payload: dict | None = None,
        client: Any | None = None,
    ) -> None:
        import os

        key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not key:
            raise errors.VideoProviderFailed(
                "openrouter video provider: OPENROUTER_API_KEY is not set",
                extra={"stage": "init"},
            )
        self.model = model
        self.api_key = key
        self.base_url = base_url.rstrip("/")
        self.resolution = resolution
        self.aspect_ratio = aspect_ratio
        self.budget_cap = budget_cap
        self.poll_interval_s = poll_interval_s
        self.poll_timeout_s = poll_timeout_s
        self.extra_payload = dict(extra_payload or {})
        self.spend_usd = 0.0
        self._client = client  # seam для тестов; ленивый httpx.AsyncClient в проде

    @staticmethod
    def enabled() -> bool:
        """True, только если пользователь ЯВНО включил провайдер."""
        import os

        flag = os.getenv("BOSSMAN_VIDEO_OPENROUTER", "0").strip().lower()
        return flag in {"1", "true", "yes", "on"} and bool(os.getenv("OPENROUTER_API_KEY"))

    def set_references(self, data_uris: list[str]) -> None:
        self.extra_payload["input_references"] = [
            {"type": "image_url", "image_url": {"url": u}} for u in data_uris
        ]

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _client_or_default(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(timeout=120.0)
        return self._client

    async def _post(self, path: str, json: dict) -> dict:
        c = self._client_or_default()
        r = await c.post(f"{self.base_url}{path}", headers=self._headers(), json=json)
        if r.status_code not in (200, 202):
            raise errors.VideoProviderFailed(
                f"openrouter video: submit HTTP {r.status_code}",
                extra={"body": str(getattr(r, "text", ""))[:300]},
            )
        return r.json()

    async def _get(self, path: str) -> dict:
        c = self._client_or_default()
        r = await c.get(f"{self.base_url}{path}", headers=self._headers())
        if r.status_code != 200:
            raise errors.VideoProviderFailed(
                f"openrouter video: HTTP {r.status_code}",
                extra={"path": path},
            )
        return r.json()

    async def generate(self, *, prompt: str, duration_s: float, output_dir: str) -> str:
        if self.budget_cap is not None and self.spend_usd >= self.budget_cap:
            raise errors.VideoProviderFailed(
                f"openrouter video: budget cap ${self.budget_cap} reached "
                f"(spent ${self.spend_usd:.2f})",
                extra={"stage": "budget"},
            )
        payload = {
            "model": self.model,
            "prompt": prompt,
            "duration": int(duration_s),
            "resolution": self.resolution,
            "aspect_ratio": self.aspect_ratio,
            **self.extra_payload,
        }
        import asyncio

        submitted = await self._post("/videos", payload)
        job_id = submitted.get("id")
        if not job_id:
            raise errors.VideoProviderFailed(
                "openrouter video: no job id in submit response",
                extra={"stage": "submit"},
            )
        deadline = asyncio.get_event_loop().time() + self.poll_timeout_s
        status = submitted.get("status", "pending")
        cost = None
        urls: list[str] = []
        while status not in ("completed", "failed", "cancelled", "expired"):
            if asyncio.get_event_loop().time() > deadline:
                raise errors.VideoProviderFailed(
                    f"openrouter video: job {job_id} poll timeout",
                    extra={"stage": "poll"},
                )
            await asyncio.sleep(self.poll_interval_s)
            data = await self._get(f"/videos/{job_id}")
            status = data.get("status", "pending")
            cost = (data.get("usage") or {}).get("cost")
            urls = data.get("unsigned_urls") or []
        if cost is not None:
            self.spend_usd += float(cost)
        if status != "completed" or not urls:
            raise errors.VideoProviderFailed(
                f"openrouter video: job {job_id} terminal status={status}",
                extra={"job": str(job_id)},
            )
        c = self._client_or_default()
        dl = await c.get(urls[0], headers=self._headers())
        if dl.status_code != 200 or not dl.content:
            raise errors.VideoProviderFailed(
                f"openrouter video: download HTTP {dl.status_code}",
                extra={"stage": "download"},
            )
        out_dir_path = Path(output_dir)
        out_dir_path.mkdir(parents=True, exist_ok=True)
        take = next_take_path(out_dir_path)
        take.write_bytes(dl.content)
        return str(take)
