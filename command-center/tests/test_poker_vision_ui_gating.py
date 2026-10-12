"""Poker Vision page: controls that the service rejects without a desk / policy stay disabled and say why.

UI sweep of 0b15e00c: Pause/Resume sent POST desk/pause without a desk (400), «Запустить сервис» was
clickable before the policy was known (409), and the three mode buttons were disabled with no reason.
Static invariants only (no browser); the live check is `scripts/ui_acceptance_sweep.py --pages poker-vision`.
"""
from __future__ import annotations

import re
from pathlib import Path

JS = (Path(__file__).resolve().parents[1] / "ui" / "pages" / "poker_vision.js").read_text(encoding="utf-8")


def test_pause_resume_and_start_begin_disabled_with_a_reason():
    for name in ("pauseBtn", "resumeBtn", "startBtn"):
        m = re.search(rf"const {name} = btn\([^\n]*disabled: true, title: [^\n]+\}}\);", JS)
        assert m, f"{name} must start disabled with a title"


def test_pause_requires_a_running_unpaused_desk_and_resume_a_paused_one():
    assert "pauseBtn.disabled = !desk || Boolean(desk.paused);" in JS
    assert "resumeBtn.disabled = !desk || !desk.paused;" in JS


def test_start_is_enabled_only_when_policy_is_known_and_allows_it():
    assert "typeof policy.enabled === 'boolean'" in JS
    assert "const off = !known || policy.enabled === false;" in JS
    assert "startBtn.disabled = off;" in JS


def test_mode_buttons_explain_why_they_are_disabled_without_a_desk():
    assert "const why = !desk ? NO_DESK_WHY" in JS
    assert "title: (!desk || !allowed) ? why : ''" in JS


def test_sync_runs_on_every_status_tick():
    assert "syncDeskButtons(desk, st.control_policy);" in JS
