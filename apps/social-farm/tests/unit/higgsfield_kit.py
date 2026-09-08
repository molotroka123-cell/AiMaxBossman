"""Детерминированные страницы генератора Higgsfield.

Живого Higgsfield здесь нет и быть не может: аккаунта провайдера в этой среде
нет, а заводить его ради теста запрещено границей приложения. Всё, что
доказывают эти страницы, — поведение защит и жизненного цикла работы, а не
работоспособность самого Higgsfield. Возможность, проверенная только здесь,
остаётся `EXPERIMENTAL` (`browser/capabilities.py`), и никакой тест этого не
меняет.

Страница описывается и разметкой, и элементами: разметку читает распознавание
проверок человека (`challenge.py`), элементы — поиск целей. Расхождение между
ними означало бы фикстуру, которая доказывает сама себя.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from social_farm.browser import (AccountBrowserSession, BrowserConfig, FixtureDom,
                                 FixtureElement, FixturePage, SelectorRegistry)
from social_farm.generation.higgsfield_adapter import (HiggsfieldAdapterConfig,
                                                       HiggsfieldBrowserAdapter)
from social_farm.browser.isolation import AccountContextRoot
from social_farm.generation.higgsfield_selectors import PROVIDER, pack_document
from social_farm.generation.workspace import GenerationWorkspace

GENERATION_URL = "https://fixture.higgsfield.local/create"
ACCOUNT_ID = "owner-studio"
IDENTITY = "owner@studio.example"
OTHER_IDENTITY = "someone-else@studio.example"

_BASE_MARKUP = """\
<!doctype html>
<html lang="en">
<head><title>Higgsfield — fixture</title></head>
<body>
<div data-testid="account-name">{identity}</div>
{body}
</body>
</html>
"""


def _page(*, body_markup: str, text: str, elements: list[FixtureElement],
          url: str = GENERATION_URL, identity: str = IDENTITY) -> FixturePage:
    return FixturePage(
        url=url, title="Higgsfield — fixture",
        text=f"{identity} {text}".strip(),
        markup=_BASE_MARKUP.format(identity=identity, body=body_markup),
        elements=[FixtureElement(tag="div", text=identity,
                                 attributes={"data-testid": "account-name"}),
                  *elements])


def _prompt_field(value: str = "") -> FixtureElement:
    return FixtureElement(tag="textarea", label="Prompt", value=value,
                          attributes={"id": "prompt", "name": "prompt",
                                      "data-testid": "prompt-input"})


def _aspect_field(value: str = "") -> FixtureElement:
    return FixtureElement(tag="input", type="text", label="Aspect ratio", value=value,
                          attributes={"id": "aspect",
                                      "data-testid": "aspect-input"})


def _duration_field(value: str = "") -> FixtureElement:
    return FixtureElement(tag="input", type="text", label="Duration", value=value,
                          attributes={"id": "duration",
                                      "data-testid": "duration-input"})


def _generate_button(*, disabled: bool = False) -> FixtureElement:
    return FixtureElement(tag="button", text="Generate", disabled=disabled,
                          attributes={"id": "generate",
                                      "data-testid": "generate-submit"})


def _job_card(label: str = "Generating") -> FixtureElement:
    return FixtureElement(tag="div", role="status", accessible_name=label, text=label,
                          attributes={"data-testid": "job-card"})


def _download_button() -> FixtureElement:
    return FixtureElement(tag="button", text="Download",
                          attributes={"id": "download",
                                      "data-testid": "result-download"})


# --------------------------------------------------------------------- страницы

def ready_page(identity: str = IDENTITY) -> FixturePage:
    """Владелец вошёл, форма на месте: с этой страницы можно работать."""
    return _page(
        identity=identity,
        body_markup='<label for="prompt">Prompt</label>'
                    '<textarea id="prompt"></textarea>'
                    '<button id="generate">Generate</button>',
        text="Create a new scene Prompt Aspect ratio Duration Generate",
        elements=[_prompt_field(), _aspect_field(), _duration_field(),
                  _generate_button()])


def auth_page() -> FixturePage:
    """Сессия владельца не активна: генератор предлагает войти."""
    return _page(
        identity=IDENTITY,
        body_markup='<button id="signin">Sign in</button>',
        text="Sign in to continue Continue with Google",
        elements=[FixtureElement(tag="button", text="Sign in",
                                 attributes={"id": "signin"})])


def challenge_page() -> FixturePage:
    """Капча. Проходить её автоматом нельзя и здесь этого не происходит."""
    return _page(
        identity=IDENTITY,
        body_markup='<div class="g-recaptcha" data-sitekey="fixture"></div>',
        text="Verify you are human before continuing",
        elements=[FixtureElement(tag="div", text="Verify you are human",
                                 attributes={"class": "g-recaptcha"})])


def drifted_page() -> FixturePage:
    """Интерфейс сменился: поле есть, кнопки запуска — нет."""
    return _page(
        identity=IDENTITY,
        body_markup='<label for="prompt">Prompt</label>'
                    '<textarea id="prompt"></textarea>',
        text="Create a new scene Prompt",
        elements=[_prompt_field()])


def rate_limited_page() -> FixturePage:
    return _page(
        identity=IDENTITY,
        body_markup='<div id="notice">Too many requests</div>',
        text="Too many requests, try again later",
        elements=[_prompt_field(), _generate_button(),
                  FixtureElement(tag="div", text="Too many requests",
                                 attributes={"id": "notice"})])


def quota_page() -> FixturePage:
    return _page(
        identity=IDENTITY,
        body_markup='<div id="notice">Out of credits</div>',
        text="Out of credits — upgrade your plan to continue",
        elements=[_prompt_field(), _generate_button(),
                  FixtureElement(tag="div", text="Out of credits",
                                 attributes={"id": "notice"})])


def failed_page() -> FixturePage:
    page = ready_page()
    page.text = f"{IDENTITY} Generation failed, please try a different prompt"
    page.elements = [page.elements[0], _prompt_field(), _generate_button()]
    return page


# --------------------------------------------------------- реакция на отправку

def submitting(*, observable: bool = True, then_ready: bool = False,
               signals: int = 3) -> Any:
    """Крючок фикстуры: что страница делает в ответ на нажатие «Generate».

    `observable=False` — страница, которая после нажатия не меняется ничем.
    Это не выдуманный случай: так выглядит и проглоченное нажатие, и медленный
    интерфейс. Адаптер обязан отличать её от отправки, а не считать нажатие
    доказательством.

    `signals=1` — страница, у которой изменился ТОЛЬКО текст. Один признак —
    самый опасный случай из трёх: он выглядит как отправка и ею не является.
    Текст «Generating» мог остаться от прошлой работы, прийти из соседней
    вкладки интерфейса или просто быть частью подсказки.
    """

    def on_click(page: FixturePage, element: FixtureElement) -> None:
        if element.attributes.get("data-testid") != "generate-submit":
            return
        if not observable:
            return
        page.text = page.text + " Generating your scene"
        if signals >= 2:
            element.disabled = True
            page.elements = [*page.elements, _job_card()]
        if then_ready:
            page.elements = [*page.elements, _download_button()]
            page.text = page.text + " Download"

    return on_click


def make_ready(page: FixturePage) -> None:
    """Довести страницу до состояния «результат готов»."""
    page.elements = [*page.elements, _download_button()]
    page.text = page.text + " Download"


# ------------------------------------------------------------------- сборка

def downloading(quarantine: Path, *, payload: bytes | None = None,
                name: str = "higgsfield-result.mp4",
                arrives: bool = True) -> Any:
    """Крючок: что кладёт браузер в карантин при нажатии «Download».

    Имя файла нарочно `.mp4` во всех случаях, включая те, где внутри лежит не
    видео. Именно так и приходит страница ошибки: под правильным именем.
    """

    def on_click(page: FixturePage, element: FixtureElement) -> None:
        if element.attributes.get("data-testid") != "result-download":
            return
        if not arrives:
            return
        quarantine.mkdir(parents=True, exist_ok=True)
        (quarantine / name).write_bytes(payload if payload is not None else b"")

    return on_click


def both(*hooks: Any) -> Any:
    """Соединить несколько крючков в один: страница умеет и то и другое."""

    def on_click(page: FixturePage, element: FixtureElement) -> None:
        for hook in hooks:
            hook(page, element)

    return on_click


def workspace(root: Path, account_id: str = ACCOUNT_ID) -> GenerationWorkspace:
    return GenerationWorkspace(
        context_root=AccountContextRoot(root=root), account_id=account_id)


def registry() -> SelectorRegistry:
    reg = SelectorRegistry()
    reg.register_document(pack_document())
    return reg


def session(dom: FixtureDom, *, identity: str = IDENTITY,
            account_id: str = ACCOUNT_ID, **kwargs: Any) -> AccountBrowserSession:
    return AccountBrowserSession(
        account_id=account_id, expected_identity=identity, dom=dom,
        registry=registry(), provider=PROVIDER,
        config=kwargs.pop("config", None) or BrowserConfig(), **kwargs)


def adapter(dom: FixtureDom, quarantine: Path, *,
            identity: str = IDENTITY,
            config: HiggsfieldAdapterConfig | None = None,
            space: GenerationWorkspace | None = None,
            **kwargs: Any) -> HiggsfieldBrowserAdapter:
    return HiggsfieldBrowserAdapter(
        session=session(dom, identity=identity, **kwargs),
        config=config or HiggsfieldAdapterConfig(
            generation_url=GENERATION_URL, quarantine_dir=quarantine,
            download_timeout_s=1.0, download_poll_s=0.01),
        workspace=space)


def on(page: FixturePage, **hooks: Any) -> FixtureDom:
    dom = FixtureDom(page)
    for name, hook in hooks.items():
        setattr(dom, name, hook)
    return dom


__all__ = [
    "ACCOUNT_ID", "GENERATION_URL", "IDENTITY", "OTHER_IDENTITY", "adapter",
    "auth_page", "both", "challenge_page", "downloading", "drifted_page",
    "failed_page", "make_ready", "on", "quota_page", "rate_limited_page",
    "ready_page", "registry", "session", "submitting", "workspace",
]
