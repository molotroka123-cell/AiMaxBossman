"""Static invariants of the Direct Generation window (no browser needed).

The window is offline-first: no CDN, no fonts, no external requests. It keeps a
STOP control, announces status changes to assistive tech and carries the one
permanent adults-only line. It must not rewrite the prompt or moderate it.
"""
from __future__ import annotations

import re
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "ui"
JS = (UI / "pages" / "direct_gen.js").read_text(encoding="utf-8")
CSS = (UI / "pages" / "direct_gen.css").read_text(encoding="utf-8")
INDEX = (UI / "index.html").read_text(encoding="utf-8")
PAGES = (UI / "pages" / "index.js").read_text(encoding="utf-8")

LEGAL = "Только для совершеннолетних; материалы реальных людей без их согласия не создавать"


def test_no_external_urls_or_fonts():
    for name, text in (("js", JS), ("css", CSS)):
        assert not re.search(r"https?://", text), f"external URL in direct_gen.{name}"
        assert "@import" not in text and "@font-face" not in text
        assert "url(" not in text, "css must not fetch resources"
    assert "fonts.googleapis" not in JS + CSS


def test_only_internal_api_is_called():
    assert "const base = '/api/direct-gen'" in JS
    assert not re.search(r"fetch\(\s*['\"]http", JS)
    assert "XMLHttpRequest" not in JS and "WebSocket(" not in JS


def test_stop_and_status_announcements_present():
    assert "'STOP'" in JS
    assert "dg-stop" in JS
    assert "aria-live" in JS and "role: 'status'" in JS
    assert "role: 'alert'" in JS


def test_stage_ribbon_covers_every_lifecycle_state():
    for state in ("queued", "loading", "generating", "postprocessing", "completed", "failed", "cancelled"):
        assert f"{state}:" in JS, state


def test_adults_only_line_is_permanent_and_single():
    assert JS.count(LEGAL) == 1
    assert "dg-legal" in JS
    # not a blocking warning: no modal/confirm gate around creating a job
    assert "confirmDialog" not in JS and "openModal" not in JS and "confirm(" not in JS


def test_prompt_is_never_rewritten_or_filtered_client_side():
    assert "prompt: prompt.value" in JS  # sent as typed
    assert "prompt.value =" not in JS.split("function loadToForm")[0].split("const body =")[1].split("async function submit")[0]
    for banned in ("moderat", "blocklist", "forbidden", "sanitize", "censor"):
        assert banned not in JS.lower(), banned
    assert "effective_prompt" in JS  # what the model really got stays visible in the log
    assert "assist_choice" in JS  # ASSISTED: the human picks, nothing silent


def test_progress_percent_only_from_real_steps():
    assert "kind === 'steps'" in JS
    assert "Процент недоступен" in JS


def test_accessibility_basics():
    assert "prefers-reduced-motion" in CSS
    assert ":focus-visible" in CSS
    assert re.search(r"min-height:\s*(4\d|5\d)px", CSS)
    # every form control is bound to a <label for=...> or has an aria-label
    for control_id in ("dg-prompt", "dg-duration", "dg-resolution", "dg-seed", "dg-negative", "dg-image", "dg-audio"):
        assert f"'{control_id}'" in JS
    assert "label.dg-label" in JS and "{ for: id }" in JS
    assert "role: 'radiogroup'" in JS or "radiogroup" in JS


def test_page_is_registered_and_styled_from_the_shared_layer():
    assert "pages/direct_gen.css" in INDEX
    assert "'direct-gen'" in PAGES and "./direct_gen.js" in PAGES
    assert "var(--bx-" in CSS
    assert "from './_ui.js'" in JS and "from '../components.js'" in JS


def test_photo_video_switch_and_per_kind_controls():
    assert "name: 'dg-kind'" in JS and "'Фото'" in JS and "'Видео'" in JS
    assert "m.kind === state.kind" in JS            # selector shows only the current mode's models
    assert "'dg-steps'" in JS and "PHOTO_PRESETS" in JS
    assert "только для собственного или вымышленного персонажа" in JS
    for code in ("insufficient_memory", "gpu_busy", "engine_error"):
        assert f"{code}:" in JS


def test_placeholders_are_neutral_and_have_no_example_prompts():
    for text in (JS, CSS):
        assert not re.search(r"\b(nsfw|nude|explicit|porn|erotic)\b", text, re.I)
    assert "Опишите сцену" in JS
