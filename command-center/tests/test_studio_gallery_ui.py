"""Browser acceptance uses real server/store and the packaged page when installed."""
import time
from playwright.sync_api import sync_playwright,expect
from .test_ux2_thinking_pane import _launch
from .test_editors_user_acceptance import editor_server,login

TERMINAL=('completed','failed','cancelled')


def studio_jobs(page,server):
    response=page.context.request.get(server.url+'/api/studio/jobs')
    assert response.status==200,response.text()
    return {j['id']:j for j in response.json()['items']}


def settled(page,server,before,expected=1,budget=180.0):
    """Ждать завершения прогона по состоянию записи, а не по скорости машины.

    Окно утверждения интерфейса (5 с по умолчанию) не увеличено: здесь
    ожидается только то, что обязано произойти до отрисовки карточки —
    появление задания и его переход в конечное состояние. Медленная машина
    больше не выдаётся за пропавшую карточку, а незавершённая или упавшая
    задача называется прямо, а не как «карточек 1 вместо 2».
    """
    deadline=time.monotonic()+budget
    while time.monotonic()<deadline:
        fresh=[j for jid,j in studio_jobs(page,server).items() if jid not in before]
        if len(fresh)>=expected and all(j['status'] in TERMINAL for j in fresh):
            statuses=sorted(j['status'] for j in fresh)
            assert statuses==['completed']*expected,(statuses,[j.get('error') for j in fresh])
            return fresh
        page.wait_for_timeout(200)
    raise AssertionError('studio job did not reach a terminal state within %.0f s'%budget)


def test_studio_composer_gallery_reuse_and_trash(editor_server):
    with sync_playwright() as pw:
        browser=_launch(pw)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000})
            login(page,editor_server)
            page.goto(editor_server.url+'/#/images')
            page.get_by_role('button',name='Создать в Studio',exact=True).click()
            prompt=page.get_by_role('textbox',name='Промпт Studio',exact=True)
            prompt.fill('Prague studio browser evidence')
            before=studio_jobs(page,editor_server)
            page.get_by_role('button',name='Создать результат',exact=True).click()
            settled(page,editor_server,before)
            card=page.locator('.studio-card').filter(has_text='Prague studio browser evidence')
            expect(card).to_have_count(1)
            card.get_by_role('button',name='Настройки и происхождение').click()
            expect(page.locator('.studio-proof')).to_contain_text('sha256')
            page.get_by_role('button',name='Повторить настройки',exact=True).click()
            expect(prompt).to_have_value('Prague studio browser evidence')
            page.locator('.studio-card').first.get_by_role('button',name='В корзину',exact=True).click()
            expect(page.locator('.studio-card')).to_have_count(0)
            page.get_by_role('button',name='Корзина Studio',exact=True).click()
            expect(page.locator('.studio-card')).to_have_count(1)
            page.get_by_role('button',name='Восстановить',exact=True).click()
            expect(page.locator('.studio-card')).to_have_count(0)
            page.get_by_role('button',name='Все результаты',exact=True).click()
            expect(page.locator('.studio-card')).to_have_count(1)
        finally:browser.close()


def test_studio_reference_import_and_reframe_ui(editor_server):
    from .test_studio_cloud import _png
    with sync_playwright() as pw:
        browser=_launch(pw)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000})
            login(page,editor_server)
            page.goto(editor_server.url+'/#/images?studio=1')
            page.get_by_label('Импортировать референс').set_input_files({'name':'owner.png','mimeType':'image/png','buffer':_png(256,256)})
            card=page.locator('.studio-card').filter(has_text='owner.png')
            expect(card).to_have_count(1)
            card.get_by_role('button',name='Рефрейм',exact=True).click()
            expect(page.get_by_label('Модель Studio')).to_have_value('local:reframe')
            before=studio_jobs(page,editor_server)
            page.get_by_role('button',name='Создать результат',exact=True).click()
            settled(page,editor_server,before)
            expect(page.locator('.studio-card')).to_have_count(2)
            page.get_by_label('Импортировать референс').set_input_files({'name':'bad.png','mimeType':'image/png','buffer':b'not png'})
            expect(page.locator('.studio-card')).to_have_count(2)
            expect(page.locator('.toast').last).to_be_visible()
        finally:browser.close()
