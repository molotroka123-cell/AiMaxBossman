"""Real vendored GrapesJS, real browser input and the running project API."""
import pytest

from .browser_support import chromium_available, reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [pytest.mark.timeout(120), pytest.mark.skipif(not chromium_available(), reason=reason())]


def seed(page):
    return page.evaluate('''async () => {
      const h = {'Content-Type':'application/json','X-BCC-CSRF':localStorage.getItem('bcc.csrf')};
      const r = await fetch('/api/web-designer/projects', {method:'POST',headers:h,body:JSON.stringify({name:'Visual QA',template:'blank'})});
      const id = (await r.json()).meta.id;
      const html = '<!DOCTYPE html><html lang="ru"><head><title>Keep title</title><style>h1{color:rgb(180, 20, 30)}</style><script>window.headPreserved=true;</script></head><body class="site"><h1>BEFORE VISUAL</h1><p>Keep paragraph</p></body></html>';
      await fetch('/api/web-designer/projects/'+id+'/code',{method:'PUT',headers:h,body:JSON.stringify({html})});
      return id;
    }''')


def stored(page, pid):
    return page.evaluate('''async id => (await (await fetch('/api/web-designer/projects/'+id)).json()).code''', pid)


def open_editor(page):
    page.get_by_role('button', name='Конструктор блоков', exact=True).click()
    dialog = page.get_by_role('dialog', name='Визуальный конструктор')
    dialog.get_by_role('button', name='Сохранить сайт', exact=True).wait_for()
    page.wait_for_function("() => [...document.querySelectorAll('dialog button')].some(b => b.textContent === 'Сохранить сайт' && !b.disabled)")
    return dialog, page.frame_locator('iframe[title="Конструктор блоков"]').frame_locator('iframe.gjs-frame')


def test_visual_edit_save_reopen_and_noop_preserve_source(live):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={'width':1440,'height':1000})
            errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
            _login(page, live); pid = seed(page)
            page.goto(live.url + f'/#/web_designer?project={pid}')
            original = stored(page, pid)
            dialog, canvas = open_editor(page)
            assert canvas.locator('h1').inner_text() == 'BEFORE VISUAL'
            assert canvas.locator('h1').evaluate('(e) => getComputedStyle(e).color') == 'rgb(180, 20, 30)'
            dialog.get_by_role('button', name='Сохранить сайт', exact=True).click()
            dialog.wait_for(state='detached')
            assert stored(page, pid) == original

            dialog, canvas = open_editor(page)
            heading = canvas.locator('h1')
            heading.dblclick()
            page.keyboard.press('ControlOrMeta+a'); page.keyboard.type('AFTER VISUAL')
            # Blur commits the rich-text editing session to GrapesJS.
            canvas.locator('p').click()
            dialog.get_by_role('button', name='Сохранить сайт', exact=True).click()
            dialog.wait_for(state='detached', timeout=15000)
            after = stored(page, pid)
            assert 'AFTER VISUAL' in after and 'BEFORE VISUAL' not in after
            assert '<title>Keep title</title>' in after and 'window.headPreserved=true;' in after
            assert 'Keep paragraph' in after
            page.reload()
            _, canvas = open_editor(page)
            assert canvas.locator('h1').inner_text() == 'AFTER VISUAL'
            assert errors == [], errors
        finally:
            browser.close()


def test_isolation_undo_conflict_and_cancel_keep_owner_data(live):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={'width':1440,'height':1000})
            _login(page, live); pid = seed(page)
            page.goto(live.url + f'/#/web_designer?project={pid}')
            requests = []; page.on('request', lambda r: requests.append(r.url))
            dialog, canvas = open_editor(page)
            shell = next(f for f in page.frames if f.url.startswith('data:text/html'))
            assert shell.evaluate('window.origin') == 'null'
            assert shell.evaluate('''() => {try {parent.localStorage.length; return false} catch(e) {return true}}''')
            assert shell.evaluate('''() => {try {parent.document.title; return false} catch(e) {return true}}''')
            assert not [url for url in requests if not url.startswith((live.url, 'data:', 'about:'))]
            # Even with a known nonce, a sibling canvas is not the trusted shell.
            nonce = shell.url.rsplit('#', 1)[1]
            canvas.locator('body').evaluate('''(_, nonce) => parent.parent.postMessage({source:'bossman-visual',nonce,type:'error',message:'FORGED'}, '*')''', nonce)
            assert 'FORGED' not in dialog.inner_text()
            canvas.locator('h1').dblclick(); page.keyboard.press('ControlOrMeta+a'); page.keyboard.type('OWNER DRAFT')
            canvas.locator('p').click()
            controls = page.frame_locator('iframe[title="Конструктор блоков"]')
            controls.get_by_role('button', name='↶ Отменить').click()
            assert canvas.locator('h1').inner_text() == 'BEFORE VISUAL'
            controls.get_by_role('button', name='↷ Повторить').click()
            assert canvas.locator('h1').inner_text() == 'OWNER DRAFT'
            page.evaluate('''async id => {
              await fetch('/api/web-designer/projects/'+id+'/code',{method:'PUT',
                headers:{'Content-Type':'application/json','X-BCC-CSRF':localStorage.getItem('bcc.csrf')},
                body:JSON.stringify({html:'<html><head></head><body><h1>OTHER TAB</h1></body></html>'})});
            }''', pid)
            dialog.get_by_role('button', name='Сохранить сайт', exact=True).click()
            dialog.get_by_role('status').filter(has_text='Сайт изменён').wait_for()
            assert canvas.locator('h1').inner_text() == 'OWNER DRAFT'
            assert 'OTHER TAB' in stored(page, pid)
            with page.expect_download() as download:
                dialog.get_by_role('button', name='Скачать черновик', exact=True).click()
            assert 'OWNER DRAFT' in __import__('pathlib').Path(download.value.path()).read_text()
            page.once('dialog', lambda d: d.dismiss())
            dialog.get_by_role('button', name='Закрыть', exact=True).click()
            assert dialog.is_visible()
            page.once('dialog', lambda d: d.accept())
            dialog.get_by_role('button', name='Закрыть', exact=True).click()
            dialog.wait_for(state='detached')
            assert 'OTHER TAB' in stored(page, pid)
        finally:
            browser.close()


def test_responsive_and_print_styles_keep_their_media_after_edit_and_reload(live):
    """Preview must match authored media conditions, not flatten print/mobile CSS."""
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            _login(page, live)
            pid = seed(page)
            page.evaluate('''async id => {
              const html = '<html><head><title>Responsive</title>'
                + '<style>h1{color:rgb(180,20,30)}</style>'
                + '<style media="(max-width: 480px)">h1{color:rgb(20,40,180)}</style>'
                + '<style media="print">h1{display:none}</style>'
                + '</head><body><h1>RESPONSIVE BEFORE</h1><p>Paragraph</p></body></html>';
              const r = await fetch('/api/web-designer/projects/'+id+'/code', {method:'PUT',
                headers:{'Content-Type':'application/json','X-BCC-CSRF':localStorage.getItem('bcc.csrf')},
                body:JSON.stringify({html})});
              if(!r.ok) throw new Error('fixture save failed');
            }''', pid)
            page.goto(live.url + f'/#/web_designer?project={pid}')
            dialog, canvas = open_editor(page)
            heading = canvas.locator('h1')
            expect(heading).to_be_visible()
            expect(heading).to_have_css('color', 'rgb(180, 20, 30)')
            controls = page.frame_locator('iframe[title="Конструктор блоков"]')
            controls.get_by_label('Размер экрана').select_option(label='Телефон')
            expect(heading).to_have_css('color', 'rgb(20, 40, 180)')
            heading.dblclick()
            page.keyboard.press('ControlOrMeta+a')
            page.keyboard.type('RESPONSIVE AFTER')
            canvas.locator('p').click()
            controls.get_by_role('button', name='↶ Отменить').click()
            expect(heading).to_have_text('RESPONSIVE BEFORE')
            controls.get_by_role('button', name='↷ Повторить').click()
            expect(heading).to_have_text('RESPONSIVE AFTER')
            controls.get_by_label('Размер экрана').select_option(label='Компьютер')
            expect(heading).to_have_css('color', 'rgb(180, 20, 30)')
            dialog.get_by_role('button', name='Сохранить сайт', exact=True).click()
            dialog.wait_for(state='detached')
            after = stored(page, pid)
            assert '<style media="print">h1{display:none}</style>' in after
            assert '<style media="(max-width: 480px)">h1{color:rgb(20,40,180)}</style>' in after
            page.reload()
            _, canvas = open_editor(page)
            expect(canvas.locator('h1')).to_be_visible()
            expect(canvas.locator('h1')).to_have_text('RESPONSIVE AFTER')
            expect(canvas.locator('h1')).to_have_css('color', 'rgb(180, 20, 30)')
        finally:
            browser.close()


def test_download_matches_saved_removal_of_body_attributes(live):
    """Exercise upstream wrapper state, then actual download and save buttons."""
    from pathlib import Path
    from bcc.web_designer_visual import parts
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            _login(page, live)
            pid = seed(page)
            page.goto(live.url + f'/#/web_designer?project={pid}')
            dialog, _ = open_editor(page)
            shell = next(f for f in page.frames if f.url.startswith('data:text/html'))
            shell.evaluate("grapesjs.editors[0].getWrapper().setClass([])")
            dialog.get_by_role('status').filter(has_text='несохранённые').wait_for()
            with page.expect_download() as downloaded:
                dialog.get_by_role('button', name='Скачать черновик', exact=True).click()
            draft = Path(downloaded.value.path()).read_text(encoding='utf-8')
            assert 'class' not in parts(draft)['body_attributes']
            dialog.get_by_role('button', name='Сохранить сайт', exact=True).click()
            dialog.wait_for(state='detached')
            assert parts(draft)['body_attributes'] == parts(stored(page, pid))['body_attributes']
        finally:
            browser.close()
