"""Real browser and server: new owner controls produce actual local effects."""
import pytest

from .browser_support import chromium_available, reason
from .test_ux2_thinking_pane import LiveServer, _launch, _login

pytestmark = [pytest.mark.timeout(180), pytest.mark.skipif(not chromium_available(), reason=reason())]


def test_owner_reads_document_and_searches_notes_in_browser(tmp_path, monkeypatch):
    pytest.importorskip("docling")
    from playwright.sync_api import sync_playwright, expect
    docs = tmp_path / "documents"
    docs.mkdir()
    document = docs / "invoice.csv"
    document.write_text("item,amount\nPrague,42\n", encoding="utf-8")
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "Prague.md").write_text("# Prague\nBossman opens the Prague office on Monday.", encoding="utf-8")
    monkeypatch.setenv("BCC_FILE_INTELLIGENCE", "1")
    monkeypatch.setenv("AIFS_ROOTS", str(docs))
    server = LiveServer(tmp_path / "server").start()
    try:
        with sync_playwright() as pw:
            browser = _launch(pw)
            try:
                page = browser.new_page(viewport={"width": 1366, "height": 900})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                _login(page, server)
                page.goto(server.url + "/#/oss")
                expect(page.get_by_role("heading", name="Локальные инструменты", exact=True)).to_be_visible()
                page.get_by_role("button", name="Прочитать документ", exact=True).click()
                expect(page.get_by_text("Укажите путь к документу.", exact=True)).to_be_visible()
                page.get_by_label("Путь к документу").fill(str(document))
                page.get_by_role("button", name="Прочитать документ", exact=True).click()
                expect(page.get_by_placeholder("Текст документа")).to_have_value(__import__('re').compile("Prague"), timeout=30000)
                page.get_by_label("Папка с заметками", exact=True).fill(str(notes))
                page.get_by_role("button", name="Использовать обычный поиск", exact=True).click()
                expect(page.get_by_text("Настройки сохранены. Обновите индекс перед поиском.", exact=True)).to_be_visible()
                page.get_by_role("button", name="Обновить индекс", exact=True).click()
                expect(page.get_by_text("Индекс обновлён.", exact=True)).to_be_visible()
                page.get_by_placeholder("Что найти в заметках?").fill("Prague")
                page.get_by_role("button", name="Найти в заметках", exact=True).click()
                expect(page.get_by_placeholder("Найденные заметки")).to_have_value(__import__('re').compile("Prague office"))
                page.get_by_role("button", name="Расшифровать", exact=True).click()
                expect(page.get_by_text("Выберите WAV-файл.", exact=True)).to_be_visible()
                assert not errors, errors
            finally:
                browser.close()
    finally:
        server.stop()
