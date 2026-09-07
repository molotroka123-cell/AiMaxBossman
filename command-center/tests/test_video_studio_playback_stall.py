"""Превью не должно останавливаться от того, что страница перерисовалась.

Отдельный от приёмки стенд: приёмка проходит путь владельца целиком, а здесь
нужен УЗКИЙ замер — включить воспроизведение и посмотреть, что со звеном
делает сама страница. Инструментация живёт в тесте (init script), продукт под
неё не переписан: перехватываются настоящие события медиа-элемента, настоящие
вызовы play/pause/load, присвоения src/currentTime и появление/исчезновение
самого <video> в документе.

Что этот стенд измерил на живой странице (не предположил):
  call:play        — ровно один, промис РАЗРЕШИЛСЯ (play:resolved);
  call:pause       — ни одного;
  set:currentTime  — посреди игры только из onLoadedmetadata НОВОГО узла;
  dom:removed id=4 ct=0.436 paused=false  ← игравший элемент выброшен,
  dom:added   id=5 ct=0     paused=true   ← на его место построен новый,
  и новый узел встаёт на playhead (0.475881) и остаётся на паузе:
  paused=true, ended=false, seeking=false, readyState=4, error=null.
Это ровно то состояние, которое приёмка показывает как «preview playback
stalled»: ни кодеки, ни транспорт, ни байты к нему отношения не имеют.
"""
from __future__ import annotations

import json
import re
import subprocess

import pytest
from playwright.sync_api import sync_playwright

from .test_editors_user_acceptance import editor_server  # noqa: F401 — фикстура
from .test_editors_user_acceptance import login
from .test_ux2_thinking_pane import _launch

# Полный след: события медиа-элемента, вызовы play/pause/load, присвоения src и
# currentTime и появление/исчезновение <video> в документе. Каждая запись
# помечена id элемента — подмену узла видно, а не додумывают.
TRACE = r"""
window.__vsTrace = [];
const t0 = performance.now();
let seq = 0;
const idOf = el => { if (!el.__vsId) el.__vsId = ++seq; return el.__vsId; };
const at = () => Number((performance.now() - t0).toFixed(1));
const where = () => (new Error().stack || '').split('\n').slice(3, 6)
    .map(s => s.trim()).join(' <- ');
const log = (kind, detail) => { window.__vsTrace.push({ t: at(), kind, ...detail }); };
const snap = el => ({ id: idOf(el), ct: el.currentTime, paused: el.paused,
                      rs: el.readyState, ns: el.networkState,
                      connected: el.isConnected, src: (el.currentSrc || el.src).slice(-24) });

for (const name of ['loadstart', 'loadedmetadata', 'loadeddata', 'canplay', 'canplaythrough',
                    'play', 'playing', 'pause', 'waiting', 'stalled', 'suspend', 'emptied',
                    'abort', 'ended', 'seeking', 'seeked', 'error', 'ratechange',
                    'durationchange', 'timeupdate']) {
  document.addEventListener(name, e => {
    if (e.target instanceof HTMLMediaElement) log('event:' + name, snap(e.target));
  }, true);
}

const proto = HTMLMediaElement.prototype;
for (const method of ['play', 'pause', 'load']) {
  const original = proto[method];
  proto[method] = function (...args) {
    log('call:' + method, { ...snap(this), from: where() });
    const result = original.apply(this, args);
    if (method === 'play' && result && result.catch) {
      result.then(() => log('play:resolved', snap(this)),
                  err => log('play:rejected', { ...snap(this), error: String(err) }));
    }
    return result;
  };
}
for (const prop of ['src', 'currentTime']) {
  const desc = Object.getOwnPropertyDescriptor(proto, prop);
  Object.defineProperty(proto, prop, { ...desc, set(value) {
    log('set:' + prop, { id: idOf(this), to: String(value).slice(-24),
                         was: String(desc.get.call(this)).slice(-24), from: where() });
    desc.set.call(this, value);
  } });
}
const attribute = Element.prototype.setAttribute;
Element.prototype.setAttribute = function (name, value) {
  if (this instanceof HTMLMediaElement && name === 'src') {
    log('attr:src', { id: idOf(this), to: String(value).slice(-24), from: where() });
  }
  return attribute.call(this, name, value);
};

new MutationObserver(records => {
  for (const record of records) {
    for (const [key, nodes] of [['removed', record.removedNodes], ['added', record.addedNodes]]) {
      for (const node of nodes) {
        const found = node instanceof HTMLMediaElement ? [node]
            : (node.querySelectorAll ? node.querySelectorAll('video') : []);
        for (const v of found) log('dom:' + key, snap(v));
      }
    }
  }
}).observe(document, { childList: true, subtree: true });
"""

READY = """() => { const v = document.querySelector('.vs-preview video');
                   return v && v.readyState >= 1 && v.videoWidth > 0 && !v.error; }"""

# Источник в панели — отрендеренное превью, а не файл библиотеки. Продукт
# отдаёт их по разным адресам, и различать их надо по адресу, а не по времени.
RENDERED = r"""() => { const v = document.querySelector('.vs-preview video');
                       return v && /\/exports\/[^/]+\/file/.test(v.currentSrc || v.src); }"""


SRC = """() => { const v = document.querySelector('.vs-preview video');
                 return v && (v.currentSrc || v.src); }"""


def fixture_clip(tmp_path, seconds_long=4):
    """Настоящий файл, а не заглушка: превью длиннее секунды — окно шире."""
    path = tmp_path / f'stand-{seconds_long}s.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i',
                    f'color=green:size=320x180:rate=30:duration={seconds_long}', '-f', 'lavfi',
                    '-i', f'sine=frequency=440:sample_rate=48000:duration={seconds_long}',
                    '-c:v', 'libx264', '-threads', '1', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
                    '-shortest', str(path)], check=True, timeout=60)
    return path


def open_project_with_clip(page, server, fixture):
    login(page, server)
    page.goto(server.url + '/#/video-studio')
    page.get_by_role('button', name='＋ Новый проект', exact=True).click()
    page.get_by_label('Название', exact=True).fill('Playback stall stand')
    page.get_by_role('button', name='Применить', exact=True).click()
    page.locator('.vs-library input[type=file]').set_input_files(fixture)
    page.locator('.vs-media-card').first.wait_for(timeout=30000)
    page.locator('.vs-media-card').first.dblclick()
    page.locator('.vs-clip').first.wait_for(timeout=30000)
    return re.search(r'project_id=([^&]+)', page.url).group(1)


def render_preview(page):
    """Дождаться ИМЕННО отрендеренного превью, а не любого <video> в панели.

    Панель показывает исходник библиотеки тем же самым `.vs-preview video`,
    и он удовлетворяет READY ещё до того, как задача рендера завершилась.
    Ждать «появился video с readyState>=1» здесь мало: на медленном раннере
    стенд успевал взяться за исходник, а подмена на готовый рендер прилетала
    уже посреди измерения и выбрасывала игравший узел — то есть стенд мерил
    не то, что собирался. Условие ниже — строго сильнее и различает эти два
    источника по URL продукта: исходник это `/media/<id>/…`, отрендеренное
    превью — `/exports/<job>/file`.
    """
    page.locator('.vs-preview-actions').get_by_role(
        'button', name='Создать preview', exact=True).click()
    page.locator('.vs-preview video').wait_for(timeout=120000)
    page.wait_for_function(RENDERED, timeout=120000)
    page.wait_for_function(READY, timeout=30000)


def dump(page, tmp_path, name):
    trace = page.evaluate('() => window.__vsTrace')
    (tmp_path / name).write_text(json.dumps(trace, indent=1), encoding='utf-8')
    return trace


@pytest.mark.timeout(300)
def test_the_stand_waits_for_the_render_and_not_for_any_video(editor_server, tmp_path):
    """Негативный контроль к самому стенду, а не к продукту.

    До нажатия «Создать preview» в панели уже стоит <video> с ИСХОДНИКОМ из
    библиотеки. Прежнее условие готовности (`READY`) на нём истинно, поэтому
    ожидание «появился video и он готов» могло вернуть управление ещё на
    исходнике: измерение начиналось не на том элементе, а подмена на готовый
    рендер прилетала уже посреди него и выбрасывала игравший узел. Ровно это
    и наблюдалось на медленном раннере.

    `RENDERED` на исходнике ЛОЖНО и становится истинным только на
    `/exports/<job>/file`. Здесь это измеряется, а не предполагается.

    Измерено на этой машине: до рендера src панели —
    `/api/video-studio/media/<id>/file?...`, после — `/api/video-studio/
    exports/<job>/file`. Готовность (`READY`) намеренно НЕ проверяется: на
    одном хосте исходник успевает стать готовым, на другом нет, и именно эта
    разница делала прежнее ожидание недетерминированным. Различение по адресу
    от скорости хоста не зависит.
    """
    server = editor_server
    fixture = fixture_clip(tmp_path, 2)
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_context(viewport={'width': 1720, 'height': 1100}).new_page()
            open_project_with_clip(page, server, fixture)
            page.locator('.vs-preview video').wait_for(timeout=30000)
            before = page.evaluate(SRC)
            assert '/media/' in before and '/exports/' not in before, before
            assert page.evaluate(RENDERED) is False, before
            render_preview(page)
            after = page.evaluate(SRC)
            assert '/exports/' in after and after != before, (before, after)
            assert page.evaluate(RENDERED) is True, after
        finally:
            browser.close()


@pytest.mark.timeout(300)
def test_preview_keeps_playing_through_a_repaint(editor_server, tmp_path):
    """Перерисовка страницы посреди воспроизведения не должна его обрывать.

    `paint()` зовёт не только владелец: его зовут завершение задачи рендера
    (`pollJob`), событие сервера через `refresh()`, перехваченный сбой в
    `guard()`, переключение раскладки, языка и рабочего пространства. Здесь
    перерисовка вызывается НАСТОЯЩЕЙ кнопкой владельца («Сбросить раскладку»
    в подвале) — тем же самым `paint()`.

    Тест не считается пройденным вхолостую: отдельно проверяется, что панель
    превью действительно была перестроена (старая секция отсоединена), а
    <video> при этом остался ТЕМ ЖЕ узлом и доиграл до конца.
    """
    server = editor_server
    fixture = fixture_clip(tmp_path, 4)
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            context = browser.new_context(viewport={'width': 1720, 'height': 1100})
            page = context.new_page()
            page.add_init_script(TRACE)
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            open_project_with_clip(page, server, fixture)
            render_preview(page)

            page.locator('.vs-transport').get_by_role('button', name='│◀', exact=True).click()
            page.locator('.vs-transport').get_by_role('button', name='▶', exact=True).click()
            page.wait_for_function(
                """() => { const v = document.querySelector('.vs-preview video');
                           return v && v.currentTime > .3 && !v.error; }""", timeout=30000)
            playing = page.query_selector('.vs-preview video')
            panel = page.query_selector('.vs-preview')

            page.get_by_role('button', name='Сбросить раскладку', exact=True).click()

            # Перерисовка была настоящей: прежняя секция превью отсоединена.
            assert panel.evaluate('el => el.isConnected') is False
            survived = page.query_selector('.vs-preview video')
            assert page.evaluate('([a, b]) => a === b', [playing, survived]) is True, \
                dump(page, tmp_path, 'trace-repaint.json')
            try:
                page.wait_for_function(
                    """() => { const v = document.querySelector('.vs-preview video');
                               return v && v.ended && v.currentTime >= 3 && !v.error; }""",
                    timeout=25000)
            finally:
                trace = dump(page, tmp_path, 'trace-repaint.json')
            # Ни pause(), ни отклонённого промиса play(): элемент просто играл.
            assert [r for r in trace if r['kind'] == 'call:play']
            assert not [r for r in trace if r['kind'] in ('call:pause', 'play:rejected')], trace
            assert errors == [], errors
        finally:
            browser.close()


@pytest.mark.timeout(300)
def test_preview_element_is_rebuilt_when_the_source_actually_changes(editor_server, tmp_path):
    """Обратный контроль к переиспользованию узла.

    Узел сохраняется только при ТОМ ЖЕ источнике. Если бы он сохранялся всегда,
    выбор исходника в библиотеке показывал бы владельцу прежнее превью — то
    есть чужое видео под правильной подписью. Здесь проверяется, что при смене
    источника элемент действительно новый и src действительно другой.
    """
    server = editor_server
    fixture = fixture_clip(tmp_path, 2)
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            context = browser.new_context(viewport={'width': 1720, 'height': 1100})
            page = context.new_page()
            page.add_init_script(TRACE)
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            open_project_with_clip(page, server, fixture)
            render_preview(page)

            preview_node = page.query_selector('.vs-preview video')
            preview_src = preview_node.evaluate('v => v.currentSrc || v.src')

            page.locator('.vs-media-card').first.click()
            page.wait_for_function(
                """previous => { const v = document.querySelector('.vs-preview video');
                                 return v && (v.currentSrc || v.src) !== previous; }""",
                arg=preview_src, timeout=30000)
            source_node = page.query_selector('.vs-preview video')
            source_src = source_node.evaluate('v => v.currentSrc || v.src')
            assert page.evaluate('([a, b]) => a === b', [preview_node, source_node]) is False
            assert '/media/' in source_src and source_src != preview_src

            page.locator('.vs-clip').first.click()
            page.wait_for_function(
                """previous => { const v = document.querySelector('.vs-preview video');
                                 return v && (v.currentSrc || v.src) !== previous; }""",
                arg=source_src, timeout=30000)
            back = page.query_selector('.vs-preview video')
            assert back.evaluate('v => v.currentSrc || v.src') == preview_src
            assert page.evaluate('([a, b]) => a === b', [source_node, back]) is False
            dump(page, tmp_path, 'trace-source-switch.json')
            assert errors == [], errors
        finally:
            browser.close()



@pytest.mark.timeout(300)
def test_preview_keeps_playing_through_a_shell_rerender(editor_server, tmp_path):
    """Второй путь к тому же дефекту, и он срабатывает САМ.

    Оболочка зовёт `renderPage()` не только при навигации: на открытии
    вебсокета и на восстановлении связи (`app.js`: `ws.open`,
    `onConnRestored`). Приёмка перезапускает сервер посреди сценария — связь
    рвётся и восстанавливается там, где владелец смотрит превью. Пока каждый
    такой вызов строил новый `Editor`, студия собиралась с нуля вместе с новым
    <video>, и воспроизведение обрывалось молча: измерено кнопкой «Обновить»
    самой оболочки, которая идёт ровно этим путём.
    """
    server = editor_server
    fixture = fixture_clip(tmp_path, 4)
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            context = browser.new_context(viewport={'width': 1720, 'height': 1100})
            page = context.new_page()
            page.add_init_script(TRACE)
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            open_project_with_clip(page, server, fixture)
            render_preview(page)

            page.locator('.vs-transport').get_by_role('button', name='│◀', exact=True).click()
            page.locator('.vs-transport').get_by_role('button', name='▶', exact=True).click()
            page.wait_for_function(
                """() => { const v = document.querySelector('.vs-preview video');
                           return v && v.currentTime > .3 && !v.error; }""", timeout=30000)
            playing = page.query_selector('.vs-preview video')

            page.locator('#refresh-btn').click()

            try:
                page.wait_for_function(
                    """() => { const v = document.querySelector('.vs-preview video');
                               return v && v.ended && v.currentTime >= 3 && !v.error; }""",
                    timeout=25000)
            finally:
                dump(page, tmp_path, 'trace-shell-rerender.json')
            survived = page.query_selector('.vs-preview video')
            assert page.evaluate('([a, b]) => a === b', [playing, survived]) is True
            assert errors == [], errors
        finally:
            browser.close()


@pytest.mark.timeout(300)
def test_leaving_the_page_still_builds_a_fresh_studio(editor_server, tmp_path):
    """Обратный контроль к переиспользованию редактора.

    Живой редактор переиспользуется только пока его узел в документе и проект
    тот же. Если бы он переиспользовался всегда, уход на другую страницу и
    возврат показывали бы владельцу мёртвый снимок вместо перечитанной студии.
    """
    server = editor_server
    fixture = fixture_clip(tmp_path, 2)
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            context = browser.new_context(viewport={'width': 1720, 'height': 1100})
            page = context.new_page()
            page.add_init_script(TRACE)
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            open_project_with_clip(page, server, fixture)
            render_preview(page)
            before = page.query_selector('.vs-studio')

            page.goto(server.url + '/#/tasks')
            page.wait_for_function("() => !document.querySelector('.vs-studio')", timeout=15000)
            page.goto(server.url + '/#/video-studio')
            page.locator('.vs-clip').first.wait_for(timeout=30000)

            after = page.query_selector('.vs-studio')
            assert page.evaluate('([a, b]) => a === b', [before, after]) is False
            assert before.evaluate('el => el.isConnected') is False
            page.wait_for_function(READY, timeout=30000)
            assert errors == [], errors
        finally:
            browser.close()
