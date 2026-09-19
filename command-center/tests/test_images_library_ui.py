"""Real browser, real image import and persistent collection/favorite APIs."""
import pytest

from .browser_support import chromium_available, reason, required
from .test_ux2_thinking_pane import _launch
from .test_editors_user_acceptance import editor_server, login as _login  # noqa: F401

pytestmark = [pytest.mark.timeout(120), pytest.mark.skipif(not chromium_available() and not required(), reason=reason())]


@pytest.fixture
def live(editor_server):
    # Windows owner CI points this real subprocess at the exact installed
    # bundle and verifies wheel identity through BCC_ACCEPTANCE_PYTHON/SHA.
    return editor_server


def test_favorites_collections_and_empty_selection_use_real_server_filters(live):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={'width': 1600, 'height': 1100})
            _login(page, live)
            ids = page.evaluate('''async () => {
              const headers = {'Content-Type':'application/json','X-BCC-CSRF':localStorage.getItem('bcc.csrf')};
              async function call(url, body, method='POST') {
                const response = await fetch(url, {method, headers, body: JSON.stringify(body)});
                if (!response.ok) throw new Error('seed failed: '+response.status);
                return response.json();
              }
              const collection = await call('/api/images/collections', {name:'Prague trip'});
              const ids = [];
              for (const title of ['Prague favorite', 'Brno other']) {
                const image = await call('/api/images/assets/import', {
                  filename:title+'.svg', title,
                  data_base64:btoa('<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8" fill="red"/></svg>'), tags:[]});
                ids.push(image.id);
              }
              await call('/api/images/assets/'+ids[0], {favorite:true, collection_id:collection.id}, 'PATCH');
              return {assets:ids, collection:collection.id};
            }''')
            page.goto(live.url + '/#/images')
            cards = page.locator('.images-card')
            expect(cards).to_have_count(2)
            sidebar = page.locator('.images-collections')

            with page.expect_response(lambda r: '/api/images/assets?' in r.url and 'favorite=true' in r.url):
                sidebar.get_by_role('button', name='Избранное').click()
            expect(cards).to_have_count(1)
            expect(cards.first).to_contain_text('Prague favorite')
            expect(sidebar.get_by_role('button', name='Избранное')).to_have_attribute('aria-pressed', 'true')
            # Unfavoriting updates the real store and clears the stale inspector.
            page.get_by_role('button', name='Убрать из избранного', exact=True).click()
            expect(cards).to_have_count(0)
            expect(page.get_by_text('В избранном пока нет изображений', exact=True)).to_be_visible()
            expect(page.locator('.images-inspector')).to_contain_text('Выберите изображение')

            with page.expect_response(lambda r: '/api/images/assets?' in r.url and f'collection_id={ids["collection"]}' in r.url):
                sidebar.get_by_role('button', name='Prague trip').click()
            expect(cards).to_have_count(1)
            expect(cards.first).to_contain_text('Prague favorite')
            expect(sidebar.get_by_role('button', name='Prague trip')).to_have_attribute('aria-pressed', 'true')
            expect(sidebar.get_by_role('button', name='Все изображения')).to_contain_text('2')

            # This button opens a native prompt; automatic dialog dismissal is
            # not evidence that it is dead. Accept it and verify persistence.
            page.once('dialog', lambda dialog: dialog.accept('Empty collection'))
            page.get_by_role('button', name='Новая коллекция', exact=True).click()
            sidebar.get_by_role('button', name='Empty collection').click()
            expect(cards).to_have_count(0)
            expect(page.get_by_text('В этой коллекции пока нет изображений', exact=True)).to_be_visible()
            sidebar.get_by_role('button', name='Все изображения').click()
            expect(cards).to_have_count(2)
            page.reload()
            expect(cards).to_have_count(2)
            expect(sidebar.get_by_role('button', name='Empty collection')).to_be_visible()
            search = page.get_by_role('searchbox', name='Поиск изображений', exact=True)
            search.fill('Brno')
            with page.expect_response(lambda r: '/api/images/assets?' in r.url and 'search=Brno' in r.url):
                search.press('Enter')
            expect(cards).to_have_count(1)
            expect(cards.first).to_contain_text('Brno other')
            expect(search).to_have_value('Brno')
            search.fill('nonexistent image')
            page.get_by_role('button', name='Найти', exact=True).click()
            expect(cards).to_have_count(0)
            expect(page.get_by_text('Изображения не найдены', exact=True)).to_be_visible()
            # The search controls remain usable even when nothing matches.
            page.get_by_role('button', name='Сбросить поиск', exact=True).click()
            expect(cards).to_have_count(2)
            expect(search).to_have_value('')
        finally:
            browser.close()
