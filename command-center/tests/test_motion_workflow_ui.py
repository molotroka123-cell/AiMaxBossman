"""Motion Studio «Рабочий процесс» panel in a real Chromium against a live server (real ffmpeg, no mocks).

One owner journey: upload -> analysis -> bind materials -> edit text / soundtrack / cover -> see what will NOT
be executed -> run -> result with parameters -> reload and reopen -> the Epic tab is still there.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from bcc.motion_workflow import render as render_mod

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "motion_workflow"))
import synthetic_package  # noqa: E402

ENV = render_mod.environment()
pytestmark = [
    pytest.mark.timeout(240),
    pytest.mark.skipif(not chromium_available(), reason=browser_reason()),
    pytest.mark.skipif(not ENV["ready"], reason="ffmpeg / ffprobe not available: " + "; ".join(ENV["problems"])),
]
NETWORK_NOISE = re.compile(r"net::ERR_|Failed to load resource|the server responded with a status of (404|501|503)", re.I)


def tid(name: str) -> str:
    return f"[data-testid='{name}']"


def test_the_owner_journey_through_the_workflow_panel(live, tmp_path):  # noqa: F811
    from PIL import Image
    from playwright.sync_api import expect, sync_playwright

    package = tmp_path / "package"
    synthetic_package.make_package(package, size="640x360", fps=25, scene_seconds=2.0, music_seconds=5.0)
    Image.new("RGB", (48, 48), (200, 120, 90)).save(package / "actor.png")
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("console", lambda m: errors.append(f"console {m.type}: {m.text}")
                if m.type in ("error", "warning") and not NETWORK_NOISE.search(m.text) else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        try:
            _login(page, live)
            page.goto(f"{live.url}/#/motion-studio", wait_until="domcontentloaded")
            page.wait_for_selector(tid("mw-root"), timeout=30000)
            # empty state: the upload control, the tabs, the renderer readiness are visible
            expect(page.locator(tid("mw-create"))).to_be_visible()
            expect(page.locator(tid("motion-tabs"))).to_contain_text("Epic-трейлер")
            expect(page.locator("#view")).to_contain_text("ffmpeg: есть")

            page.fill(tid("mw-new-title"), "UI journey")
            page.set_input_files(tid("mw-upload-file"), str(package / "source.mp4"))
            page.click(tid("mw-create"))
            page.wait_for_selector(f"{tid('mw-analyze')}:not([disabled])", timeout=60000)
            expect(page.locator(tid("mw-source"))).to_contain_text("Анализ ещё не запускался")
            page.click(tid("mw-analyze"))
            page.wait_for_selector(tid("mw-scenes-table"), timeout=120000)
            expect(page.locator(tid("mw-scenes-table") + " tbody tr")).to_have_count(3)
            # limits, estimate and the readiness verdict are shown BEFORE any run
            expect(page.locator(tid("mw-limits"))).to_contain_text("сцены")
            expect(page.locator(tid("mw-estimate"))).to_contain_text("платных вызовов: 0")
            expect(page.locator(tid("mw-readiness"))).to_contain_text("готов к запуску")
            # what was not measured is UNKNOWN, not "none"
            assert page.locator(tid("mw-scenes-table") + " [data-unknown='1']").count() > 0

            # materials: an audio track and a character image become @song and @actor
            page.fill(tid("mw-ref-name"), "song")
            page.set_input_files(tid("mw-ref-file"), str(package / "music.wav"))
            page.click(tid("mw-ref-add"))
            expect(page.locator(tid("mw-refs"))).to_contain_text("@song", timeout=20000)
            page.fill(tid("mw-ref-name"), "actor")
            page.select_option(tid("mw-ref-kind"), "character")
            page.set_input_files(tid("mw-ref-file"), str(package / "actor.png"))
            page.click(tid("mw-ref-add"))
            expect(page.locator(tid("mw-refs"))).to_contain_text("@actor", timeout=20000)

            # edit: text over scene 1, a face replacement in scene 2 (this renderer cannot execute it)
            page.fill(tid("mw-scene-text-s001"), "ПРЕМЬЕРА 12.10")
            page.click(tid("mw-scene-text-apply-s001"))
            expect(page.locator(tid("mw-scene-text-s001"))).to_have_value("ПРЕМЬЕРА 12.10", timeout=20000)
            page.select_option(tid("mw-policy-s002-face_character"), "replace")
            page.wait_for_selector(tid("mw-with-s002-face_character"), timeout=20000)
            page.select_option(tid("mw-with-s002-face_character"), "@actor")
            expect(page.locator(tid("mw-not-executed-preview"))).to_contain_text("сцена s002", timeout=20000)
            expect(page.locator(tid("mw-not-executed-preview"))).to_contain_text("Лицо / персонаж")
            # soundtrack replaced by @song, cover at 3 s with a title
            page.click(f"{tid('mw-audio-mode')} button:nth-child(2)")
            page.click(tid("mw-audio-apply"))
            expect(page.locator(tid("mw-audio-note"))).to_contain_text("@song", timeout=20000)
            page.fill(tid("mw-cover-time"), "3")
            page.fill(tid("mw-cover-title"), "Мой ролик")
            page.click(tid("mw-cover-apply"))
            expect(page.locator(tid("mw-cover-title"))).to_have_value("Мой ролик", timeout=20000)

            # run, result
            page.click(tid("mw-run-full"))
            page.wait_for_selector(tid("mw-result-banner"), timeout=120000)
            banner = page.locator(tid("mw-result-banner"))
            expect(banner).to_contain_text("файл проверен")
            expect(banner).to_contain_text("выполнено частично")
            assert banner.get_attribute("data-partial") == "1"
            expect(page.locator(tid("mw-not-executed"))).to_contain_text("сцена s002")
            expect(page.locator(tid("mw-not-executed"))).to_contain_text("Лицо / персонаж")
            params = page.locator(tid("mw-parameters"))
            expect(params).to_contain_text("h264")
            expect(params).to_contain_text("640×360")
            expect(params).to_contain_text("платные вызовы")
            expect(page.locator(tid("mw-video"))).to_be_visible()
            expect(page.locator(tid("mw-cover-img"))).to_be_visible()
            page.click(tid("mw-check"))
            expect(page.locator(tid("mw-check-result"))).to_contain_text("совпадает с отчётом", timeout=60000)

            # reload the page: the project, the result and the not-executed list are still there
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector(tid("mw-result-banner"), timeout=30000)
            expect(page.locator(tid("mw-not-executed"))).to_contain_text("сцена s002")
            expect(page.locator(tid("mw-project-select"))).to_have_value(re.compile(r"^mw_"))

            # phone width: no horizontal page scroll
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(300)
            widths = page.evaluate("({sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth})")
            assert widths["sw"] <= widths["cw"] + 1, widths
            page.set_viewport_size({"width": 1440, "height": 1000})

            # the Epic panel is still on the same page
            page.click(f"{tid('motion-tabs')} button:nth-child(2)")
            expect(page.locator("#view")).to_contain_text("Готовность", timeout=20000)
            expect(page.locator(tid("motion-studio"))).to_be_visible()
        finally:
            browser.close()
    assert errors == []
