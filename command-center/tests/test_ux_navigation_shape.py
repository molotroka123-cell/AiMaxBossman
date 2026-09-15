"""§11 — the sidebar's shape is a decision, and it must stay one.

The nav had grown to two sections: "Основное" with five entries and "Система"
with everything else — around twenty items in one flat list where Video Studio
sat next to "Развилки". Finding anything required remembering where it was.

The consolidation groups those items into six named spaces. The risk of any
such change is that tidying quietly removes capability, so most of this file
checks the opposite: every route still exists, still renders, and is still
reachable. A cleaner menu that lost a page would be a worse outcome than the
menu it replaced.
"""
from __future__ import annotations

import pytest

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [pytest.mark.timeout(180),
              pytest.mark.skipif(not chromium_available(), reason=browser_reason())]

#: Named spaces, in the order the sidebar shows them.
EXPECTED_SECTIONS = ["main", "work", "studio", "apps", "brains", "system"]

#: The workspaces §11 names as first class. Each must be its own destination,
#: not a tab buried inside something else.
FIRST_CLASS = ["video-studio", "web_designer", "trading_lab", "browser", "coding"]


@pytest.fixture
def nav(live):  # noqa: F811
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            _login(page, live)
            page.wait_for_selector(".nav-item")
            yield {
                "pages": page.evaluate("window.__bxPages"),
                "sections": page.evaluate("window.__bxSections"),
                "items": page.evaluate(
                    "() => Array.from(document.querySelectorAll('.nav-item'))"
                    "  .map(b => b.dataset.page)"),
                "labels": page.evaluate(
                    "() => Array.from(document.querySelectorAll('.nav-item .nav-label'))"
                    "  .map(s => s.textContent.trim())"),
                "headers": page.evaluate(
                    "() => Array.from(document.querySelectorAll('.nav-section'))"
                    "  .map(s => s.textContent.trim())"),
                "mobile": page.evaluate(
                    "() => Array.from(document.querySelectorAll('.mnav-item'))"
                    "  .map(b => b.dataset.page)"),
            }
        finally:
            browser.close()


# ------------------------------------------------------- nothing was lost

def test_every_page_still_declares_a_route(nav):
    """The set of destinations is what a consolidation must not shrink."""
    assert len(nav["pages"]) >= 25
    assert all(p["id"] and p["title"] for p in nav["pages"])


def test_every_page_belongs_to_exactly_one_named_space(nav):
    """A page with no declared home lands wherever the fallback puts it, which
    is how the flat "Система" list grew in the first place."""
    known = set(EXPECTED_SECTIONS)
    for page in nav["pages"]:
        assert page["section"] in known, page


def test_a_page_kept_out_of_the_sidebar_is_still_reachable(live):  # noqa: F811
    """`#/command` and the superseded landings are hidden from the MENU, never
    from the app. Hiding a route would be deleting a capability.

    Self-contained rather than built on the `nav` fixture: two Playwright
    contexts in one test cannot share the sync API's loop."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            _login(page, live)
            page.wait_for_selector(".nav-item")
            hidden = [p for p in page.evaluate("window.__bxPages") if not p["inSidebar"]]
            assert hidden, "the duplicate should be hidden from the sidebar"
            for entry in hidden:
                page.goto(f"{live.url}/#/{entry['id']}", wait_until="domcontentloaded")
                page.wait_for_function(
                    "t => document.getElementById('page-title').textContent === t",
                    arg=entry["title"], timeout=15000)
        finally:
            browser.close()


# --------------------------------------------------------- the new shape

def test_the_sidebar_has_between_five_and_seven_named_spaces(nav):
    assert nav["sections"] == EXPECTED_SECTIONS
    assert 5 <= len(nav["headers"]) <= 7, nav["headers"]


def test_no_two_menu_entries_carry_the_same_name(nav):
    """`command` and `control` were both "Пульт". Two identical entries make
    the question "which one is the real one" answerable only by clicking."""
    duplicates = [label for label in nav["labels"] if nav["labels"].count(label) > 1]
    assert not duplicates, f"дубликаты в меню: {sorted(set(duplicates))}"


def test_the_specialist_workspaces_are_first_class_destinations(nav):
    for page_id in FIRST_CLASS:
        assert page_id in nav["items"], f"{page_id} исчез из меню"


def test_the_studio_space_carries_the_specialist_surfaces(nav):
    """They used to sit in the same flat list as recovery and forks."""
    studio = {p["id"] for p in nav["pages"] if p["section"] == "studio"}
    assert {"video-studio", "web_designer", "browser", "coding"} <= studio


def test_deep_telemetry_moved_under_system(nav):
    """§11: telemetry stays available, but stops competing with daily work."""
    system = {p["id"] for p in nav["pages"] if p["section"] == "system"}
    assert {"resources", "governor", "healing", "forks"} <= system


def test_no_space_is_left_empty(nav):
    """An empty header is visual noise that claims to organise something."""
    populated = {p["section"] for p in nav["pages"] if p["inSidebar"]}
    assert set(EXPECTED_SECTIONS) <= populated


# ----------------------------------------------------------- still usable

def test_the_phone_bar_still_has_five_real_destinations(nav):
    """The bottom bar used to be filled from "Основное". After the regrouping
    that section holds one page, so a naive change would have left the phone
    with a single button."""
    assert len(nav["mobile"]) == 5
    known = {p["id"] for p in nav["pages"]}
    assert all(page_id in known for page_id in nav["mobile"])


def test_every_menu_entry_points_at_a_real_page(nav):
    known = {p["id"] for p in nav["pages"]}
    assert all(item in known for item in nav["items"])


def test_menu_entries_are_keyboard_reachable_buttons(live):  # noqa: F811
    """§11 requires keyboard navigation to survive. A div with a click handler
    would look identical and be unusable without a mouse."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            _login(page, live)
            page.wait_for_selector(".nav-item")
            tags = page.evaluate(
                "() => Array.from(document.querySelectorAll('.nav-item'))"
                "  .map(b => b.tagName + ':' + (b.getAttribute('type') || ''))")
            assert tags and all(t == "BUTTON:button" for t in tags), tags
            first = page.locator(".nav-item").first
            first.focus()
            assert page.evaluate(
                "() => document.activeElement.classList.contains('nav-item')")
        finally:
            browser.close()


def test_the_sidebar_survives_a_narrow_viewport(live):  # noqa: F811
    """Responsive behaviour must remain functional (§11)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 390, "height": 780})
            _login(page, live)
            page.wait_for_selector(".mnav-item")
            visible = page.evaluate(
                "() => Array.from(document.querySelectorAll('.mnav-item'))"
                "  .filter(b => b.getBoundingClientRect().width > 0).length")
            assert visible >= 3, "нижняя панель телефона исчезла"
        finally:
            browser.close()
