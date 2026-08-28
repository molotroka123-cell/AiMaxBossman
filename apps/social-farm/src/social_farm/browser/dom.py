"""Порт к странице: единственное место, знающее про Playwright.

Всё, что решает про безопасность — отпечаток цели, редакция секретов, автомат
состояний, передача человеку, — лежит НАД этим портом и не знает, настоящий там
браузер или фикстура. Это не архитектурное украшение: без такого разделения
тесты на фикстуре не доказывали бы ничего о настоящем браузере, потому что
проверяли бы другой код.

Чтобы разделение было честным, у порта две реализации с **одинаковой
семантикой поиска**:

* `PlaywrightDom` — настоящий Chromium; разбор страницы делает JavaScript ниже;
* `FixtureDom` — детерминированная страница в памяти; тот же разбор на Python.

Совпадение семантик проверяется тестом на одной и той же HTML-странице.

Одно правило порта, которое не обходится ничем: **значение поля `type=password`
не покидает страницу.** Оно не возвращается ни в снимке, ни в описании цели, ни
в отпечатке. Наружу уходит только факт «поле заполнено».
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .selectors import ALL_KINDS

REF_ATTRIBUTE = "data-sf-ref"
_WS = re.compile(r"\s+")


class DomError(RuntimeError):
    """Порт не смог выполнить операцию над страницей."""


class BrowserUnavailable(RuntimeError):
    """Playwright или Chromium недоступны.

    Отдельный тип, потому что это не поломка и не отказ провайдера: браузерный
    резерв — необязательная часть, и приложение обязано работать без него,
    честно говоря «этого пути сейчас нет».
    """


def _norm(value: Any) -> str:
    return _WS.sub(" ", str(value or "")).strip()[:220].lower()


@runtime_checkable
class DomPort(Protocol):
    """Минимум, которого хватает браузерному резерву. Больше не нужно."""

    async def current_url(self) -> str: ...
    async def title(self) -> str: ...
    async def navigate(self, url: str) -> None: ...
    async def reload(self) -> None: ...
    async def markup(self) -> str: ...
    async def visible_text(self, limit: int) -> str: ...
    async def elements(self, limit: int) -> list[dict[str, Any]]: ...
    async def find(self, kind: str, value: str) -> list[dict[str, Any]]: ...
    async def click(self, ref: str) -> None: ...
    async def fill(self, ref: str, value: str) -> None: ...
    async def close(self) -> None: ...


# --------------------------------------------------------------- разбор страницы

# Тот же разбор, что и в `FixtureDom`, только на JavaScript. Правится вместе с
# питоновской половиной — тест сверяет обе на одной странице.
PAGE_SCRIPT = r"""
(request) => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const norm = (s) => clean(s).slice(0, 220).toLowerCase();
  const CANDIDATES = 'a,button,input,textarea,select,summary,label,dialog,'
    + '[role],[tabindex],[data-testid],h1,h2,h3,h4,h5,h6';

  function roleOf(el) {
    const explicit = el.getAttribute('role');
    if (explicit && explicit.trim()) return explicit.trim().toLowerCase();
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'a') return el.hasAttribute('href') ? 'link' : '';
    if (tag === 'button') return 'button';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'form') return 'form';
    if (tag === 'dialog') return 'dialog';
    if (tag === 'img') return 'img';
    if (/^h[1-6]$/.test(tag)) return 'heading';
    if (tag === 'input') {
      if (['submit', 'button', 'reset', 'image'].indexOf(type) >= 0) return 'button';
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'file') return 'button';
      return 'textbox';
    }
    return '';
  }

  function labelOf(el) {
    const aria = el.getAttribute('aria-label');
    if (aria && aria.trim()) return clean(aria);
    if (el.id) {
      const tied = document.querySelector('label[for="' + el.id.replace(/"/g, '\\"') + '"]');
      if (tied) return clean(tied.textContent);
    }
    const wrapping = el.closest ? el.closest('label') : null;
    if (wrapping && wrapping !== el) return clean(wrapping.textContent);
    const placeholder = el.getAttribute('placeholder');
    if (placeholder && placeholder.trim()) return clean(placeholder);
    return '';
  }

  function nameOf(el) {
    const label = labelOf(el);
    if (label) return label;
    const alt = el.getAttribute('alt');
    if (alt && alt.trim()) return clean(alt);
    const title = el.getAttribute('title');
    if (title && title.trim()) return clean(title);
    const tag = el.tagName.toLowerCase();
    const role = roleOf(el);
    if (tag === 'input' && ['submit', 'button', 'reset'].indexOf(
        (el.getAttribute('type') || '').toLowerCase()) >= 0) {
      return clean(el.getAttribute('value') || '');
    }
    if (['button', 'a', 'summary', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'].indexOf(tag) >= 0
        || role === 'button' || role === 'link' || role === 'heading') {
      return clean(el.textContent).slice(0, 220);
    }
    return '';
  }

  function textOf(el) {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    // ЗНАЧЕНИЕ ПОЛЯ ПАРОЛЯ НЕ ПОКИДАЕТ СТРАНИЦУ. Ни в снимок, ни в отпечаток,
    // ни в трассу — ни для одного сценария оно не нужно.
    if (tag === 'input' && type === 'password') return '';
    if (tag === 'input' || tag === 'textarea') return clean(el.value || '');
    return clean(el.textContent).slice(0, 220);
  }

  function describe(el, ordinal, index) {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const secret = tag === 'input' && type === 'password';
    const ref = 'sf-' + request.generation + '-' + index;
    el.setAttribute('data-sf-ref', ref);
    return {
      ref: ref,
      tag: tag,
      role: roleOf(el),
      accessible_name: nameOf(el),
      label: labelOf(el),
      text: textOf(el),
      type: el.getAttribute('type') || '',
      ordinal: ordinal,
      secret: secret,
      filled: secret ? !!el.value : (!!el.value && (tag === 'input' || tag === 'textarea')),
      disabled: !!el.disabled,
      href: el.getAttribute('href') || '',
    };
  }

  function candidates() {
    return Array.from(document.querySelectorAll(CANDIDATES));
  }

  function matches(kind, value) {
    if (kind === 'css') return Array.from(document.querySelectorAll(value));
    if (kind === 'xpath') {
      const found = [];
      const it = document.evaluate(value, document, null,
        XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
      for (let i = 0; i < it.snapshotLength; i++) found.push(it.snapshotItem(i));
      return found;
    }
    if (kind === 'stable_attribute') {
      const eq = value.indexOf('=');
      const attr = eq < 0 ? value : value.slice(0, eq);
      const want = eq < 0 ? null : value.slice(eq + 1);
      return candidates().filter((el) => el.hasAttribute(attr)
        && (want === null || el.getAttribute(attr) === want));
    }
    if (kind === 'role') {
      const bar = value.indexOf('|');
      const role = (bar < 0 ? value : value.slice(0, bar)).trim().toLowerCase();
      const want = bar < 0 ? '' : norm(value.slice(bar + 1));
      return candidates().filter((el) => roleOf(el) === role
        && (!want || norm(nameOf(el)) === want));
    }
    if (kind === 'accessible_name') {
      const want = norm(value);
      return candidates().filter((el) => norm(nameOf(el)) === want);
    }
    if (kind === 'label') {
      const want = norm(value);
      return candidates().filter((el) => norm(labelOf(el)) === want);
    }
    return [];
  }

  if (request.op === 'elements') {
    document.querySelectorAll('[data-sf-ref]').forEach(
      (el) => el.removeAttribute('data-sf-ref'));
    return candidates().slice(0, request.limit).map((el, i) => describe(el, i, i));
  }
  if (request.op === 'find') {
    const found = matches(request.kind, request.value);
    return found.map((el, i) => describe(el, i, 'f' + i));
  }
  if (request.op === 'text') {
    return clean(document.body ? document.body.innerText : '').slice(0, request.limit);
  }
  return null;
}
"""


# --------------------------------------------------------------- Playwright

@dataclass(slots=True)
class PlaywrightDom:
    """Порт поверх настоящей страницы Playwright.

    Импорт Playwright ленивый: приложение обязано подниматься и проходить тесты
    без него (`pyproject.toml`, группа `browser` ставится отдельно).
    """

    page: Any
    generation: int = 0
    action_timeout_ms: int = 30_000
    navigation_timeout_ms: int = 60_000

    async def current_url(self) -> str:
        return str(self.page.url)

    async def title(self) -> str:
        return str(await self.page.title())

    async def navigate(self, url: str) -> None:
        await self.page.goto(url, wait_until="domcontentloaded",
                             timeout=self.navigation_timeout_ms)

    async def reload(self) -> None:
        await self.page.reload(wait_until="domcontentloaded",
                               timeout=self.navigation_timeout_ms)

    async def markup(self) -> str:
        return str(await self.page.content())

    async def visible_text(self, limit: int) -> str:
        return str(await self.page.evaluate(
            PAGE_SCRIPT, {"op": "text", "limit": int(limit), "generation": self.generation}))

    async def elements(self, limit: int) -> list[dict[str, Any]]:
        self.generation += 1
        found = await self.page.evaluate(
            PAGE_SCRIPT, {"op": "elements", "limit": int(limit),
                          "generation": self.generation})
        return list(found or [])

    async def find(self, kind: str, value: str) -> list[dict[str, Any]]:
        if kind not in ALL_KINDS:
            raise DomError(f"неизвестная стратегия поиска {kind!r}")
        self.generation += 1
        found = await self.page.evaluate(
            PAGE_SCRIPT, {"op": "find", "kind": kind, "value": value,
                          "generation": self.generation, "limit": 0})
        return list(found or [])

    def _locator(self, ref: str) -> Any:
        return self.page.locator(f'[{REF_ATTRIBUTE}="{ref}"]')

    async def click(self, ref: str) -> None:
        await self._locator(ref).click(timeout=self.action_timeout_ms)

    async def fill(self, ref: str, value: str) -> None:
        await self._locator(ref).fill(value, timeout=self.action_timeout_ms)

    async def close(self) -> None:
        try:
            await self.page.close()
        except Exception:                                    # pragma: no cover
            pass


def playwright_available() -> bool:
    try:
        import playwright.async_api                          # noqa: F401
        return True
    except Exception:
        return False


# --------------------------------------------------------------- фикстура

@dataclass(slots=True)
class FixtureElement:
    """Элемент детерминированной страницы."""

    tag: str = "div"
    role: str = ""
    accessible_name: str = ""
    label: str = ""
    text: str = ""
    type: str = ""
    attributes: dict[str, str] = field(default_factory=dict)
    value: str = ""
    disabled: bool = False
    href: str = ""

    @property
    def secret(self) -> bool:
        return self.tag == "input" and self.type.lower() == "password"

    def effective_role(self) -> str:
        if self.role:
            return self.role.strip().lower()
        tag, type_ = self.tag, self.type.lower()
        if tag == "a":
            return "link" if self.href else ""
        if tag == "button":
            return "button"
        if tag == "select":
            return "combobox"
        if tag == "textarea":
            return "textbox"
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            return "heading"
        if tag == "dialog":
            return "dialog"
        if tag == "input":
            if type_ in {"submit", "button", "reset", "image", "file"}:
                return "button"
            if type_ == "checkbox":
                return "checkbox"
            if type_ == "radio":
                return "radio"
            return "textbox"
        return ""

    def effective_label(self) -> str:
        return self.label or self.attributes.get("placeholder", "")

    def effective_name(self) -> str:
        if self.accessible_name:
            return self.accessible_name
        label = self.effective_label()
        if label:
            return label
        if self.tag in {"button", "a", "summary", "h1", "h2", "h3", "h4", "h5", "h6"}:
            return self.text
        return ""

    def visible_text(self) -> str:
        # То же правило, что и в JavaScript: значение поля пароля не отдаётся.
        if self.secret:
            return ""
        if self.tag in {"input", "textarea"}:
            return self.value
        return self.text

    def describe(self, ref: str, ordinal: int) -> dict[str, Any]:
        return {"ref": ref, "tag": self.tag, "role": self.effective_role(),
                "accessible_name": self.effective_name(),
                "label": self.effective_label(), "text": self.visible_text(),
                "type": self.type, "ordinal": ordinal, "secret": self.secret,
                "filled": bool(self.value), "disabled": self.disabled,
                "href": self.href}


@dataclass(slots=True)
class FixturePage:
    """Страница фикстуры: адрес, заголовок, разметочные признаки и элементы."""

    url: str = "fixture://blank"
    title: str = ""
    text: str = ""
    markup: str = ""
    elements: list[FixtureElement] = field(default_factory=list)


class FixtureDom:
    """Детерминированная страница в памяти. Настоящего Instagram здесь нет.

    Существует ради двух вещей: доказать поведение защит без живого аккаунта и
    дать браузерному пути хоть какое-то основание считаться работающим. Второе
    ограничено сознательно: возможность, проверенная только здесь, остаётся
    `EXPERIMENTAL` и до `VERIFIED_BROWSER` не поднимается (`44_...`).
    """

    __slots__ = ("page", "generation", "clicks", "fills", "_refs", "_navigations",
                 "closed", "on_click", "on_fill")

    def __init__(self, page: FixturePage | None = None) -> None:
        self.page = page or FixturePage()
        self.generation = 0
        self.clicks: list[str] = []
        self.fills: list[tuple[str, str]] = []
        self._refs: dict[str, FixtureElement] = {}
        self._navigations: list[str] = []
        self.closed = False
        # Крючки, чтобы фикстура могла менять страницу в ответ на действие —
        # так проверяется постусловие, а не только сам факт нажатия.
        self.on_click: Any = None
        self.on_fill: Any = None

    # ---- чтение

    async def current_url(self) -> str:
        return self.page.url

    async def title(self) -> str:
        return self.page.title

    async def navigate(self, url: str) -> None:
        self._navigations.append(url)
        self.page.url = url

    async def reload(self) -> None:
        self._navigations.append(self.page.url)

    async def markup(self) -> str:
        return self.page.markup

    async def visible_text(self, limit: int) -> str:
        return self.page.text[: int(limit)]

    async def elements(self, limit: int) -> list[dict[str, Any]]:
        self.generation += 1
        self._refs = {}
        out = []
        for index, element in enumerate(self.page.elements[: int(limit)]):
            ref = f"sf-{self.generation}-{index}"
            self._refs[ref] = element
            out.append(element.describe(ref, index))
        return out

    async def find(self, kind: str, value: str) -> list[dict[str, Any]]:
        if kind not in ALL_KINDS:
            raise DomError(f"неизвестная стратегия поиска {kind!r}")
        self.generation += 1
        matched = [el for el in self.page.elements if _fixture_matches(el, kind, value)]
        out = []
        for ordinal, element in enumerate(matched):
            ref = f"sf-{self.generation}-f{ordinal}"
            self._refs[ref] = element
            out.append(element.describe(ref, ordinal))
        return out

    # ---- действия

    def _element(self, ref: str) -> FixtureElement:
        element = self._refs.get(ref)
        if element is None:
            raise DomError(f"ссылка {ref} не принадлежит текущей странице")
        return element

    async def click(self, ref: str) -> None:
        element = self._element(ref)
        self.clicks.append(ref)
        if self.on_click is not None:
            self.on_click(self.page, element)

    async def fill(self, ref: str, value: str) -> None:
        element = self._element(ref)
        element.value = value
        self.fills.append((ref, value))
        if self.on_fill is not None:
            self.on_fill(self.page, element, value)

    async def close(self) -> None:
        self.closed = True

    @property
    def navigations(self) -> list[str]:
        return list(self._navigations)


def _fixture_matches(element: FixtureElement, kind: str, value: str) -> bool:
    """Питоновская половина семантики поиска. Зеркало `matches` из JavaScript."""
    if kind == "role":
        role, _, name = value.partition("|")
        if element.effective_role() != role.strip().lower():
            return False
        return not name.strip() or _norm(element.effective_name()) == _norm(name)
    if kind == "accessible_name":
        return _norm(element.effective_name()) == _norm(value)
    if kind == "label":
        return _norm(element.effective_label()) == _norm(value)
    if kind == "stable_attribute":
        attribute, sep, want = value.partition("=")
        present = element.attributes.get(attribute)
        if present is None:
            return False
        return not sep or present == want
    if kind == "css":
        # Фикстура понимает узкое подмножество CSS: `tag`, `#id`, `.class`,
        # `[attr=value]`. Больше здесь и не нужно: `css` — стратегия последней
        # надежды, на разрушающих действиях запрещённая совсем.
        return _fixture_css(element, value)
    if kind == "xpath":
        # То же и по той же причине: `//tag[@attr='value']`.
        return _fixture_xpath(element, value)
    return False


_CSS_ATTR = re.compile(r"^\[([\w-]+)=['\"]?([^\]'\"]*)['\"]?\]$")
_XPATH = re.compile(r"^//(\w+|\*)(?:\[@([\w-]+)=['\"]([^'\"]*)['\"]\])?$")


def _fixture_css(element: FixtureElement, selector: str) -> bool:
    selector = selector.strip()
    if selector.startswith("#"):
        return element.attributes.get("id") == selector[1:]
    if selector.startswith("."):
        classes = (element.attributes.get("class") or "").split()
        return selector[1:] in classes
    match = _CSS_ATTR.match(selector)
    if match:
        return element.attributes.get(match.group(1)) == match.group(2)
    return element.tag == selector


def _fixture_xpath(element: FixtureElement, expression: str) -> bool:
    match = _XPATH.match(expression.strip())
    if not match:
        raise DomError(
            f"фикстура понимает только выражения вида //tag[@attr='value'], "
            f"получено {expression!r}")
    tag, attribute, want = match.groups()
    if tag not in ("*", element.tag):
        return False
    if attribute is None:
        return True
    return element.attributes.get(attribute) == want


__all__ = ["PAGE_SCRIPT", "REF_ATTRIBUTE", "BrowserUnavailable", "DomError", "DomPort",
           "FixtureDom", "FixtureElement", "FixturePage", "PlaywrightDom",
           "playwright_available"]
