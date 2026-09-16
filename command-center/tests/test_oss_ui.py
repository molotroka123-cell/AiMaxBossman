"""Real browser and server: new owner controls produce actual local effects."""
import pytest

from .browser_support import chromium_available, reason, required
from .test_ux2_thinking_pane import _launch
from .test_editors_user_acceptance import editor_server, login  # noqa: F401

pytestmark = [pytest.mark.timeout(180),
              pytest.mark.skipif(not chromium_available() and not required(), reason=reason())]


@pytest.fixture
def oss_env(tmp_path, monkeypatch):
    """Папки и флаги — ДО старта сервера: EditorServer копирует окружение в
    процесс сервера, и на приёмке это процесс установленного архива, а не клон."""
    docs = tmp_path / "documents"
    docs.mkdir()
    notes = tmp_path / "notes"
    notes.mkdir()
    monkeypatch.setenv("BCC_FILE_INTELLIGENCE", "1")
    monkeypatch.setenv("AIFS_ROOTS", str(docs))
    return docs, notes


@pytest.fixture
def live(oss_env, editor_server):
    return editor_server


def _server_has(server, engine: str) -> bool:
    """Способность проверяется у процесса сервера, а не у процесса теста:
    docling лежит в runtime архива, а не обязательно в окружении pytest."""
    import httpx
    with httpx.Client(base_url=server.url, trust_env=False, timeout=10) as client:
        login = client.post("/api/login", json={"token": (server.data / "token").read_text().strip(),
                                                "label": "capability-probe"})
        if login.status_code != 200:
            return False
        client.headers["X-BCC-CSRF"] = login.json()["csrf"]
        items = client.get("/api/oss/status").json()["items"]
    return any(item["id"] == engine and item["state"] in ("installed", "configured", "integrated") for item in items)


def test_owner_reads_document_and_searches_notes_in_browser(live, oss_env, tmp_path):
    from playwright.sync_api import sync_playwright, expect
    docs, notes = oss_env
    server = live
    if not _server_has(server, "docling"):
        pytest.skip("сервер сообщает docling=needs_setup: чтение документов на этом хосте недоступно")
    document = docs / "invoice.csv"
    document.write_text("item,amount\nPrague,42\n", encoding="utf-8")
    (notes / "Prague.md").write_text("# Prague\nBossman opens the Prague office on Monday.", encoding="utf-8")
    if True:
        with sync_playwright() as pw:
            browser = _launch(pw)
            try:
                page = browser.new_page(viewport={"width": 1366, "height": 900})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                login(page, server)
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
