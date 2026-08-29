"""Local Chromium acceptance suite: no third-party network required."""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from browser_support import chromium_available, chromium_path, reason

# Нет браузера — честный skip. Раньше здесь стоял жёсткий linux-путь, и на
# Windows запуск несуществующего бинаря ВИСЕЛ, срывая весь прогон папки.
pytestmark = [pytest.mark.asyncio,
              pytest.mark.skipif(not chromium_available(), reason=reason())]

HTML = """<!doctype html><html><head><title>Bossman Browser Emulator</title></head><body>
<button id='normal' onclick="document.querySelector('#status').textContent='clicked'">Generate Preview</button>
<button id='danger'>Delete Account</button>
<input id='name'><input id='file' type='file'>
<select id='choice'><option value='a'>A</option><option value='b'>B</option></select>
<button id='popup' onclick="window.open('about:blank','_blank')">Open Popup</button>
<a id='download' download='sample.txt' href='data:text/plain,bossman-download'>Download</a>
<iframe id='fr' srcdoc="<button id='inside'>Frame Button</button>"></iframe>
<div id='status'>idle</div>
<script>setTimeout(()=>document.querySelector('#status').dataset.ready='yes',200)</script>
</body></html>"""

async def test_emulator_15_scenarios(tmp_path: Path):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, executable_path=chromium_path(), timeout=60_000)
        ctx = await browser.new_context(accept_downloads=True)
        page = await ctx.new_page(); await page.set_content(HTML)
        # 1 launch/title
        assert await page.title() == "Bossman Browser Emulator"
        # 2 click
        await page.click('#normal'); assert await page.text_content('#status') == 'clicked'
        # 3 fill
        await page.fill('#name','bossman'); assert await page.input_value('#name') == 'bossman'
        # 4 select
        await page.select_option('#choice','b'); assert await page.input_value('#choice') == 'b'
        # 5 wait state
        await page.wait_for_function("document.querySelector('#status').dataset.ready === 'yes'")
        # 6 screenshot
        shot=tmp_path/'shot.png'; await page.screenshot(path=str(shot)); assert shot.stat().st_size > 100
        # 7 download
        async with page.expect_download() as info: await page.click('#download')
        dl=await info.value; out=tmp_path/'sample.txt'; await dl.save_as(str(out)); assert out.read_text()=='bossman-download'
        # 8 iframe
        fr=page.frame_locator('#fr'); assert await fr.locator('#inside').inner_text() == 'Frame Button'
        # 9 popup/tab
        async with page.expect_popup() as pop: await page.click('#popup')
        popup=await pop.value; assert len(ctx.pages) == 2; await popup.close(); assert len(ctx.pages)==1
        # 10 upload selection
        f=tmp_path/'up.txt'; f.write_text('upload'); await page.set_input_files('#file',str(f)); assert await page.locator('#file').evaluate("e=>e.files[0].name")=='up.txt'
        # 11 hover
        await page.hover('#normal')
        # 12 scroll
        await page.mouse.wheel(0,300)
        # 13 stale selector produces deterministic failure
        with pytest.raises(Exception): await page.locator('#does-not-exist').click(timeout=100)
        # 14 timeout produces deterministic failure
        with pytest.raises(Exception): await page.get_by_text('never-here').wait_for(timeout=100)
        # 15 close/crash detection primitive
        await page.close(); assert page.is_closed()
        await ctx.close(); await browser.close()

async def test_persistent_profile_and_isolation(tmp_path: Path):
    async with async_playwright() as p:
        exe=chromium_path()
        a=tmp_path/'a'; b=tmp_path/'b'
        ca=await p.chromium.launch_persistent_context(str(a),headless=True,executable_path=exe,timeout=60_000)
        # Cookie ОБЯЗАНА иметь срок жизни: сессионную (без expires) Chromium в
        # профиль на диск не пишет вовсе, поэтому проверять на ней персистентность
        # профиля бессмысленно — тест падал не на нашем коде, а на своём условии.
        import time as _time
        await ca.add_cookies([{"name":"bossman","value":"A","url":"https://example.com",
                               "expires": _time.time() + 3600}])
        await ca.close()
        ca2=await p.chromium.launch_persistent_context(str(a),headless=True,executable_path=exe,timeout=60_000)
        cookies=await ca2.cookies("https://example.com")
        assert any(c["name"]=="bossman" and c["value"]=="A" for c in cookies)
        await ca2.close()
        cb=await p.chromium.launch_persistent_context(str(b),headless=True,executable_path=exe,timeout=60_000)
        cookies_b=await cb.cookies("https://example.com")
        assert not any(c["name"]=="bossman" for c in cookies_b)
        await cb.close()
