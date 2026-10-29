"""Glass UI (owner 28.10: nicer fonts, Apple-style glass menu): the font is bundled locally (no external fonts), the tab bar
has the sliding pill wired to showTab, the service worker ships the new shell with the font, and there is a solid fallback
for people who ask the system for less transparency or less motion."""
from __future__ import annotations

import re
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "bossmobile" / "ui"
CSS = (UI / "app.css").read_text(encoding="utf-8")
JS = (UI / "app.js").read_text(encoding="utf-8")
HTML = (UI / "index.html").read_text(encoding="utf-8")
SW = (UI / "sw.js").read_text(encoding="utf-8")


def test_font_is_local_and_licensed():
    assert (UI / "fonts" / "InterVariable.woff2").stat().st_size > 100_000
    assert "SIL OPEN FONT LICENSE" in (UI / "fonts" / "OFL-Inter-LICENSE.txt").read_text(encoding="utf-8").upper()
    assert "url('fonts/InterVariable.woff2')" in CSS
    assert not re.search(r"https?://[^)'\"]*(fonts\.googleapis|fonts\.gstatic)", CSS + HTML)   # still no external fonts


def test_tab_pill_is_wired():
    assert '<i class="tab-glow" aria-hidden="true"></i>' in HTML
    assert ".tab-glow" in CSS and "var(--tab-i, 0)" in CSS
    assert "setProperty('--tab-i', ti)" in JS
    nav = HTML[HTML.index('id="tabbar"'):HTML.index("</nav>", HTML.index('id="tabbar"'))]
    assert nav.count('data-tab="') == 8 and "repeat(8, 1fr)" in CSS and "/ 8)" in CSS   # 8 tabs, the pill is 1/8 wide


def test_service_worker_ships_the_new_shell():
    ver = int(re.search(r"bossman-shell-v(\d+)", SW).group(1))
    assert ver >= 27 and "'/m/fonts/InterVariable.woff2'" in SW


def test_accessibility_fallbacks_exist():
    assert "prefers-reduced-transparency: reduce" in CSS and "prefers-reduced-motion: reduce" in CSS
