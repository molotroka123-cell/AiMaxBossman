"""Human-like data collector (rc19/i-collector, workstream I).

Gathers information from the web the way a considerate human reader would:
one owner-approved page at a time, through the product's own Playwright
browser runtime (``bcc.v2.browser_control``), with robots.txt honoured,
polite per-domain delays, daily page caps, and full provenance (URL,
retrieved-at UTC, quote, selector, page hash) on every extracted fact.

What this package explicitly refuses to do (by design, not by omission):
no fake user-agent rotation, no fingerprint spoofing, no proxy rotation, no
CAPTCHA solving/bypass, no login walls, no paywall circumvention, no
personal-data harvesting, and no crawling beyond the owner-approved source
list (it never follows a link it was not given).

Not merged into the release candidate; lives only on branch
``rc19/i-collector`` until the owner reviews it.
"""
from __future__ import annotations

__all__ = ["engine", "models", "robots", "domain_state", "control", "extract",
           "priority", "memory_bridge", "ledger", "cli"]
