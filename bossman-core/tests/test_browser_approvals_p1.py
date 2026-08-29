"""P1 (red-team): состояние-меняющие действия не должны проходить мимо
confirmed_*. Проверяем расширенный классификатор (в т.ч. русские подписи),
структурное определение submit-кнопки и симметрию реестра (у select появился
confirmed-близнец)."""
from __future__ import annotations

import os

import pytest

from bossman.toolkit import by_api_name
from bossman.toolkit.browser import _submit_like, is_sensitive_label
from browser_support import chromium_available, chromium_path, reason


# ---------- расширенный лексикон ----------

@pytest.mark.parametrize("label", [
    "Купить", "Оплатить", "Удалить аккаунт", "Подтвердить перевод",
    "Опубликовать", "Сделать ставку", "Subscribe", "Order now", "Deploy", "Upgrade plan",
])
def test_sensitive_labels_extended(label):
    assert is_sensitive_label(label), label


@pytest.mark.parametrize("label", ["Generate preview", "Показать ещё", "Open menu", "Назад"])
def test_benign_labels_not_flagged(label):
    assert not is_sensitive_label(label), label


# ---------- симметрия реестра: у каждого состояние-меняющего действия есть confirmed-близнец ----------

def test_registry_confirm_symmetry():
    # by_api_name сопоставляет по name с точками→подчёркиваниями (browser.click → browser_click)
    assert by_api_name("browser_click").confirm_default is False
    assert by_api_name("browser_confirmed_click").confirm_default is True
    assert by_api_name("browser_select").confirm_default is False
    # ключевое: раньше confirmed_select не существовал вовсе
    cs = by_api_name("browser_confirmed_select")
    assert cs is not None and cs.confirm_default is True
    assert by_api_name("browser_press").confirm_default is False
    assert by_api_name("browser_confirmed_press").confirm_default is True


# ---------- структурное определение submit (текстовый gate это пропускал) ----------

_HTML = """<!doctype html><html><body>
<form id='f' onsubmit='return false'>
  <button id='iconsubmit' type='submit' aria-label=''>➤</button>
  <button id='plainbtn' type='button'>Toggle</button>
</form>
<a id='link' href='#'>Just a link</a>
<button id='loosebtn'>Outside form</button>
</body></html>"""


@pytest.mark.asyncio
@pytest.mark.skipif(not chromium_available(), reason=reason())
async def test_submit_like_structural_detection():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, executable_path=chromium_path(), timeout=60_000)
        page = await browser.new_page()
        await page.set_content(_HTML)
        # Иконочная submit-кнопка в форме: без осмысленного текста, но state-changing.
        assert await _submit_like(page.locator("#iconsubmit")) is True
        # Обычная кнопка type=button — не submit.
        assert await _submit_like(page.locator("#plainbtn")) is False
        # Ссылка — навигация, не отправка формы.
        assert await _submit_like(page.locator("#link")) is False
        # Кнопка вне формы — не submit.
        assert await _submit_like(page.locator("#loosebtn")) is False
        await browser.close()
