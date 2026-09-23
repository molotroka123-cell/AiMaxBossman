"""Jev (TypeSafe System-1) integration — STAGED, DISABLED BY DEFAULT.

Two cooperating but separate parts (docs/JEV_DECISION_ENGINE.md,
docs/JEV_ULTRAFAST_BROWSER_CRITICAL.md):

* ``decision``        — JevDecisionProvider: optional typed routing hints next to
                        the existing Smart Router (``bcc.features.router``).
* ``browser_fastpath`` — Jev Ultrafast adapter over the existing browser runtime
                        (``bcc.v2.browser_control``). Phase 1 = shadow only.

Everything here is stdlib-only on purpose: the owner runner
(``tools/jev_shadow_owner.py``) imports it from the bundle's ``python -I``
without the web stack. Nothing in this package runs, or touches the network,
unless ``BOSSMAN_JEV_ENABLED`` / ``BOSSMAN_JEV_BROWSER_ENABLED`` is set.
"""
