"""Telegram calls panel: status polling invariants (static, no browser; the real CallsView runs in
``ui/tests/telegram_calls.test.mjs`` under Node, which CI executes).

Found in the 08.10 Jeff zone code read and reproduced in Node before the fix:
* a reply already in flight when the owner pressed «Завершить» / STOP was the last word: ``poll()`` returned at once,
  ``run()`` came back without a fresh status and the buttons/live state stayed stale for up to one 1.5 s interval;
* the 1.5 s interval kept polling a hidden tab.
The guard «one poll at a time, a skipped interval is not replayed» stays: a slow backend is never hammered.
"""
from __future__ import annotations

import re
from pathlib import Path

JS = (Path(__file__).resolve().parents[1] / "ui" / "pages" / "telegram_calls.js").read_text(encoding="utf-8")


def _method(name: str) -> str:
    start = re.search(rf"^  (?:async )?{name}\(", JS, re.M)
    assert start, f"{name}() not found"
    end = re.search(r"^  }$", JS[start.start():], re.M)
    return JS[start.start():start.start() + end.end()]


def test_a_hidden_tab_does_not_poll_and_polls_at_once_when_shown_again():
    tick = _method("tick")
    assert tick.index("if (!this.root.isConnected) { clearInterval(this.timer)") < tick.index("if (document.hidden) return;") \
        < tick.index("this.poll();"), "the detach cleanup still runs while hidden; a hidden tab skips the poll"
    assert "document.removeEventListener('visibilitychange', this.onVisible)" in tick, "leaving the page drops the listener"
    ctor = _method("constructor")
    assert "document.addEventListener('visibilitychange', this.onVisible)" in ctor
    assert re.search(r"this\.onVisible = \(\) => \{ if \(!document\.hidden && this\.root\.isConnected\) this\.poll\(\); \};", ctor)
    assert JS.count("setInterval(") == 1


def test_an_action_or_bus_event_during_a_poll_gets_one_follow_up_poll_and_waits_for_it():
    poll = _method("poll")
    assert re.search(r"if \(this\.polling\) \{\s*if \(fresh\) this\.pollAgain = true;\s*return this\.polling;\s*\}", poll), \
        "a fresh request while a poll is in flight queues ONE follow-up and returns the in-flight promise"
    assert re.search(r"do \{\s*this\.pollAgain = false;", poll) and "} while (this.pollAgain);" in poll
    assert "this.polling = new Promise(" in poll and poll.index("this.polling = new Promise(") < poll.index("await api.raw("), \
        "the in-flight marker is set before the first await"
    assert "finally { this.polling = null; settle(); }" in poll, "cleared in the same step as the last follow-up check"
    run = _method("run")
    assert run.count("await this.poll(true);") == 2 and "this.poll()" not in run, "both the success and the failure path"
    assert _method("stop").count("await this.poll(true);") == 1
    assert "live.poll(true)" in JS.split("onEvent(ev)", 1)[1]


def test_interval_ticks_never_queue_a_follow_up():
    """Negative control: only owner actions and bus events are 'fresh'. A tick during a slow poll (the worker wait is up
    to 2 s, the interval 1.5 s) is skipped, not replayed back-to-back."""
    assert "this.poll(true)" not in _method("tick")
    assert "setInterval(() => this.tick(), POLL_MS)" in JS and "POLL_MS = 1500" in JS
