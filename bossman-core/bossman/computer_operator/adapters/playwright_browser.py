"""Real Chromium/Playwright ports for the Universal Computer Apprentice.

Implements the engine's observer/actuator protocol against a real local
browser: semantic targets only (ARIA role + accessible name), no coordinates.
Observations carry fresh generation + a URL/title identity so the engine's
freshness/window guards operate on real state.
"""
from __future__ import annotations

import itertools
import time
from typing import Any

from ..models import ActionKind, Observation
from ...apprentice.models import PlanStep

_ROLES = ("button", "link", "textbox", "checkbox", "combobox", "menuitem", "tab", "option")
_MAX_ELEMENTS = 60


class PlaywrightBrowserObserver:
    """Observe the live page: foreground identity + semantic element tree."""
    name = "playwright-chromium"
    _ids = itertools.count(1)

    def __init__(self, page: Any) -> None:
        self.page = page
        self._gen = 0
        self._t = 1_000.0
        self._identity = ("", "")

    def observe(self) -> Observation:
        self._gen += 1
        self._t += 1.0
        elements: list[dict] = []
        for role in _ROLES:
            try:
                loc = self.page.get_by_role(role)
                count = min(loc.count(), _MAX_ELEMENTS)
            except Exception:  # noqa: BLE001 — a page can refuse role queries mid-navigation
                continue
            for i in range(count):
                el = loc.nth(i)
                try:
                    an = getattr(el, "accessible_name", None)
                    label = an() if callable(an) else an
                    if not label:
                        label = el.get_attribute("aria-label") or el.text_content() or ""
                    if not label:
                        continue
                    label = " ".join(str(label).split())
                    entry = {"role": role, "name": label[:120], "enabled": el.is_enabled(), "text": label[:120]}
                    if role == "textbox":
                        try:
                            entry["value"] = el.input_value()[:200]        # real widget state part of the observation
                            entry["text"] = f"{label[:120]}={entry['value']}"
                        except Exception:  # noqa: BLE001 — non-valued inputs
                            pass
                    elements.append(entry)
                except Exception:  # noqa: BLE001 — element vanished between count and probe
                    continue
        title, url = self.page.title(), self.page.url
        self._identity = (title, url)
        return Observation(id=f"obs_{next(self._ids)}", created_at=self._t, generation=self._gen,
                           foreground={"app": "Chromium", "title": title, "url": url, "tab_id": "0"},
                           summary=title, ui_tree={"elements": elements}, sensitive=False)

    def is_current(self, obs: Observation) -> bool:
        return self._identity == (self.page.title(), self.page.url)


class PlaywrightBrowserActuator:
    """Act by semantic target on the live page. Raises RuntimeError on miss so
    the engine records a typed, recoverable actuator error."""
    name = "playwright-chromium"

    def __init__(self, page: Any, *, default_wait_ms: int = 500) -> None:
        self.page = page
        self.default_wait_ms = default_wait_ms

    def _locate(self, step: PlanStep) -> Any:
        t = step.target
        role = (t.role or "").lower()
        if role not in _ROLES:
            raise RuntimeError(f"unsupported semantic role {t.role!r}")
        loc = self.page.get_by_role(role, name=t.name or None, exact=False)
        if loc.count() == 0:
            raise RuntimeError(f"semantic target {t.label()} not found on the live page")
        return loc.first

    def act(self, step: PlanStep, obs: Any, *, action_id: str = "", side_effect_id: str = "") -> Any:
        """Engine protocol: side-effecting actions must return an EffectReceipt the
        engine can verify against the request; other actions return a plain dict."""
        detail = self._perform(step)
        if side_effect_id:
            from ...apprentice.models import EffectReceipt
            return EffectReceipt(side_effect_id=side_effect_id, action_id=action_id,
                                 action_type=step.kind.value, observed_at=time.time(),
                                 evidence_source=self.name)
        return {"detail": detail}

    def _perform(self, step: PlanStep) -> str:
        if step.kind is ActionKind.BROWSER and step.args.get("op") == "navigate":
            self.page.goto(str(step.args.get("url")), timeout=30_000, wait_until="domcontentloaded")
            return "navigated"
        if step.kind is ActionKind.WAIT:
            self.page.wait_for_timeout(int(step.args.get("ms", self.default_wait_ms)))
            return "waited"
        if step.target is None:
            raise RuntimeError(f"{step.kind} requires a semantic target")
        loc = self._locate(step)
        if step.kind is ActionKind.TYPE:
            loc.fill(str(step.text or ""), timeout=10_000)
            return f"typed into {step.target.label()}"
        if step.kind in (ActionKind.CLICK, ActionKind.DOUBLE_CLICK, ActionKind.UI_INVOKE):
            loc.click(timeout=10_000)
            return f"clicked {step.target.label()}"
        if step.kind is ActionKind.FOCUS:
            loc.focus(timeout=10_000)
            return "focused"
        if step.kind is ActionKind.TAKE_SCREENSHOT:
            return "screenshot skipped (evidence via DOM observations)"
        raise RuntimeError(f"unsupported action kind {step.kind} for the real browser adapter")
