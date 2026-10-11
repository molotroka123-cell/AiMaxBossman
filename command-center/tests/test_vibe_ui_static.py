"""Static invariants of the Vibe Studio page (no browser needed)."""
from __future__ import annotations

import re
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "ui"
JS = (UI / "pages" / "vibe.js").read_text(encoding="utf-8")
PAGES = (UI / "pages" / "index.js").read_text(encoding="utf-8")


def test_registered_and_static_fields_match_manifest():
    assert "id: 'vibe'" in PAGES and "./vibe.js" in PAGES
    for field in ("id: 'vibe'", "title: 'Vibe Studio'", "icon: 'builder'", "nav: 'more'", "section: 'studio'"):
        assert field in JS and field in PAGES


def test_preview_iframe_is_sandboxed_without_same_origin():
    assert "export const PREVIEW_SANDBOX = 'allow-scripts'" in JS
    assert "sandbox: PREVIEW_SANDBOX" in JS
    assert "allow-same-origin" not in JS.split("export const PREVIEW_SANDBOX")[1].split("\n")[0]
    assert not re.search(r"allow-same-origin['\"]", JS.replace("НЕ добавлять allow-same-origin", ""))
    assert "srcdoc" not in JS                          # the document comes from the CSP-carrying endpoint


def test_no_external_urls_and_only_internal_api():
    assert not re.search(r"https?://", JS)
    assert "const base = '/api/vibe'" in JS
    assert not re.search(r"\bfetch\(", JS) and "innerHTML" not in JS and "eval(" not in JS


def test_stop_cancel_and_privacy_controls_present():
    assert "'STOP'" in JS and "/stop" in JS and "cancel" in JS
    assert "local_only" in JS and "vibe-local" in JS
    assert "aria-live" in JS and "role: 'alert'" in JS
