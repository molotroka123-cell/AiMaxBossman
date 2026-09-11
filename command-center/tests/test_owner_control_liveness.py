"""MF-032 — клик по основной кнопке обязан быть виден, пока идёт работа.

Проверяется граница «состояние объявлено ↔ состояние отрисовано»:

    classList.add(...)  != владелец это видит
    класс есть в JS     != правило есть в стилях

Честная оговорка о происхождении этого файла. Сначала он был написан под вывод
«правила `.is-loading` нет вовсе»; вывод оказался НЕВЕРНЫМ — поиск шёл только по
`style.css`, а правило со спиннером живёт в `theme.css` (`.bx-btn.is-loading`,
там же и ветка prefers-reduced-motion). Ошибку поймал негативный контроль:
проверка осталась зелёной после удаления «исправления», то есть проверяла не то,
что заявляла. Лишнее правило убрано, а тест оставлен — потому что сама граница
реальна и ничем другим не закрыта: класс-состояние без правила невидим, и
заметить это на глаз нельзя.

Что действительно было добавлено этим проходом — `aria-busy`: занятость
существовала только визуально.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[1] / "ui"

#: Классы, которыми JS сигналит состояние. Ищем оба способа переключения.
_TOGGLE = re.compile(r"classList\.(?:add|toggle)\(\s*'([a-z0-9-]+)'")


def _js_files() -> list[Path]:
    return sorted(p for p in UI.rglob("*.js") if "tests" not in p.parts)


def _css_text() -> str:
    """Все стили, а не только файлы `.css`.

    Часть состояний оформляется инлайновым `<style>` внутри модуля (так живёт,
    например, полоса тестового периода). Считать такой класс неоформленным
    значило бы поднять ложную тревогу и научить читателя игнорировать этот тест.
    """
    parts = [p.read_text(encoding="utf-8") for p in sorted(UI.rglob("*.css"))]
    # Только стили, а не весь модуль: иначе обращение к свойству вроде
    # `app.active` сошло бы за правило CSS, и проверка стала бы зелёной по
    # любому поводу. Стили в модуле живут двумя способами — тегом <style> и
    # шаблонной строкой, которую кладут в `style.textContent`; берём оба.
    blocks = re.compile(r"<style[^>]*>(.*?)</style>", re.S | re.I)
    literals = re.compile(r"`([^`]*)`", re.S)
    looks_like_css = re.compile(r"[.#:][A-Za-z][\w-]*[^{}`]*\{[^{}`]*:")
    for path in _js_files() + sorted(UI.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        parts.extend(blocks.findall(text))
        parts.extend(lit for lit in literals.findall(text) if looks_like_css.search(lit))
    return "\n".join(parts)


def _styled(cls: str, css: str) -> bool:
    return re.search(rf"\.{re.escape(cls)}\b", css) is not None


def test_the_shared_primary_button_declares_a_busy_state():
    """Механизм обязан существовать: без класса и aria-busy отрисовывать нечего."""
    source = (UI / "pages" / "_ui.js").read_text(encoding="utf-8")
    assert "is-loading" in source
    assert "aria-busy" in source, (
        "состояние занятости обязано существовать и без зрения")


def test_the_busy_state_of_the_primary_button_is_actually_rendered():
    """Класс, который вешается на каждую основную кнопку, обязан быть отрисован."""
    css = _css_text()
    assert _styled("is-loading", css), (
        "`.is-loading` вешается на каждую основную кнопку, но не описан ни в одном "
        "файле стилей — кнопка во время запроса неотличима от нетронутой")
    # Недостаточно «упомянуть» класс: он обязан гасить повторные клики, иначе
    # владелец жмёт второй раз и попадает в тихо проглоченный вызов.
    rules = re.findall(r"\.is-loading[^{}]*\{([^}]*)\}", css)
    assert any("pointer-events" in body for body in rules), (
        "занятая кнопка обязана не принимать повторный клик")


def test_every_state_class_the_ui_toggles_is_rendered_somewhere():
    """Обобщение дефекта, а не только его частный случай.

    Любой класс-состояние, который JS вешает, но никто не рисует, — это ещё один
    невидимый клик. Проверяются все файлы стилей, включая постраничные.
    """
    css = _css_text()
    toggled = {cls for path in _js_files()
               for cls in _TOGGLE.findall(path.read_text(encoding="utf-8"))}
    assert toggled, "не нашли ни одного переключаемого класса — сломался сам поиск"
    unstyled = sorted(cls for cls in toggled if not _styled(cls, css))
    assert not unstyled, f"классы-состояния без единого правила в CSS: {unstyled}"


def test_the_check_would_notice_an_unstyled_class():
    """Негативный контроль. Без него предыдущий тест мог бы «проходить» просто
    потому, что регулярка ничего не находит."""
    css = _css_text()
    assert not _styled("bx-definitely-not-a-real-class", css)
    assert _styled("is-loading", css)


@pytest.mark.parametrize("page", ["web_designer.js", "apps.js"])
def test_primary_owner_controls_signal_that_work_started(page):
    """Экран волен строить кнопку сам — но не волен молчать.

    Проверяется НАЛИЧИЕ сигнала, а не способ: общий помощник `_ui.js btn()`
    ставит `is-loading` и `aria-busy`, а `apps.js` делает это вручную и вдобавок
    меняет подпись на «Запускаю…». Оба варианта честны; отсутствие любого — нет.
    """
    source = (UI / "pages" / page).read_text(encoding="utf-8")
    signals = ("is-loading", "aria-busy", "disabled = true", "btn.disabled")
    assert any(s in source for s in signals), (
        f"{page}: ни одного признака занятости — клик по основной кнопке молчит")
