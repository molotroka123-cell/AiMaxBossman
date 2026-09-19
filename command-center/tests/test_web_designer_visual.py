"""The OSS editor may change the body, never lose head/source or bypass CAS."""
import pytest
from pathlib import Path

from bcc.web_designer_visual import parts, merge

HEAD = '<head><!-- keep --><title>Original</title><style media="screen">h1{color:red}</style><script>window.keep=1;</script></head>'
SOURCE = '<!DOCTYPE html>\n<html lang="ru">' + HEAD + '<body class="site"><h1>Before</h1></body></html>'


def test_edit_preserves_untouched_document_and_reuses_one_stylesheet():
    changed = merge(SOURCE, '<h1 id="title">After</h1>', '#title{color:blue}')
    assert HEAD[:-7] in changed
    assert changed.startswith('<!DOCTYPE html>\n<html lang="ru">')
    assert '<body class="site"><h1 id="title">After</h1></body>' in changed
    again = merge(changed, '<h1 id="title">Again</h1>', '#title{color:green}')
    assert again.count('data-bossman-visual=') == 1
    assert parts(again)['css'] == '#title{color:green}'
    assert parts(again)['base_css'] == 'h1{color:red}'
    assert 'window.keep=1;' in again


@pytest.mark.parametrize('body', [
    '<script>alert(1)</script>', '<iframe src="/"></iframe>',
    '<div onclick="alert(1)">x</div>', '<template><p>kept</p></template>',
    '<svg viewBox="0 0 10 10"></svg>', '<x-component>custom</x-component>',
    '<img src="x" onerror="alert(1)">', '<a href="java\nscript:alert(1)">x</a>',
    '<div data-gjs-script="alert(1)">x</div>',
])
def test_unsupported_import_is_refused_instead_of_silently_removed(body):
    with pytest.raises(ValueError):
        parts(SOURCE.replace('<h1>Before</h1>', body))
    with pytest.raises(ValueError):
        merge(SOURCE, body, '')


def test_body_event_and_css_escape_are_refused():
    with pytest.raises(ValueError):
        parts(SOURCE.replace('class="site"', 'onload="alert(1)"'))
    with pytest.raises(ValueError):
        merge(SOURCE, '<p>okay</p>', '</style><script>alert(1)</script>')


def test_stylesheet_media_type_and_order_survive_preview_and_save():
    styles = ('<style media="print">h1{display:none}</style>'
              '<style media="(max-width: 480px)">h1{color:blue}</style>'
              '<style type="text/less">h1{color:green}</style>')
    source = SOURCE.replace('</head>', styles + '</head>')
    split = parts(source)
    assert split['base_styles'] == [
        {'text': 'h1{color:red}', 'media': 'screen', 'type': ''},
        {'text': 'h1{display:none}', 'media': 'print', 'type': ''},
        {'text': 'h1{color:blue}', 'media': '(max-width: 480px)', 'type': ''},
        {'text': 'h1{color:green}', 'media': '', 'type': 'text/less'},
    ]
    saved = merge(source, '<h1>Edited</h1>', 'h1{margin:1px}')
    assert styles in saved
    assert parts(saved)['base_styles'] == split['base_styles']


async def test_visual_save_conflict_and_reopen(env):
    res = await env.client.post('/api/web-designer/projects', json={'name': 'Visual', 'template': 'blank'})
    pid = res.json()['meta']['id']
    await env.client.put(f'/api/web-designer/projects/{pid}/code', json={'html': SOURCE})
    route = f'/api/web-designer/projects/{pid}/visual'
    initial = (await env.client.get(route)).json()
    assert initial['body'] == '<h1>Before</h1>'
    payload = {'body': '<h1>After</h1>', 'css': 'h1{color:blue}', 'base_version': initial['meta']['version']}
    res = await env.client.put(route, json=payload)
    assert res.status_code == 200, res.text
    assert (await env.client.get(route)).json()['body'] == '<h1>After</h1>'
    res = await env.client.put(route, json={**payload, 'body': '<h1>Stale</h1>'})
    assert res.status_code == 409
    assert (await env.client.get(route)).json()['body'] == '<h1>After</h1>'
    assert (await env.client.put(route, json={'body': '<p>no version</p>'})).status_code == 422
    assert (await env.client.get('/api/web-designer/visual-editor')).status_code == 503
    env.svc.settings.ui_dir = Path(__file__).resolve().parents[1] / 'ui'
    shell = await env.client.get('/api/web-designer/visual-editor')
    assert shell.status_code == 200
    assert 'sandbox allow-scripts;' in shell.headers['Content-Security-Policy']
    assert 'allow-same-origin' not in shell.headers['Content-Security-Policy']
