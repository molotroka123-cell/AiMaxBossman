"""Bounded semantic browser reads; condition waits, not blind readiness sleeps.

This is a tool-path optimization, not model training or a security sandbox.
Caller policy/approval/ledger/verification are still mandatory.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import time
from typing import Any

from ..models import ActionKind, Observation
from ...apprentice.models import PlanStep, SemanticTarget

_ROLES = ("button", "link", "textbox", "checkbox", "combobox", "menuitem", "tab", "option", "radio", "switch")
_MAX_ELEMENTS = 60
_MAX_WAIT_MS = 30_000

# Playwright resolves roles, then a single read-only RPC collects each batch.
# Accessible-name approximations deliberately fail closed at exact actuation.
_ROLE_SNAPSHOT = r"""(nodes, config) => nodes.slice(0, config.limit).map(el => {
    const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
    const labelled = (el.getAttribute('aria-labelledby') || '').split(/\s+/)
        .map(id => el.ownerDocument.getElementById(id)?.textContent || '').join(' ');
    const labels = Array.from(el.labels || []).map(label => label.textContent || '').join(' ');
    const name = clean(labelled) || clean(el.getAttribute('aria-label')) ||
        clean(labels) || clean(el.textContent) || clean(el.getAttribute('title'));
    if (!name || name.length > 1024) return null;
    const privateValue = el.type === 'password' ||
        /(?:password|one-time-code|cc-number|cc-csc)/i.test(el.getAttribute('autocomplete') || '');
    const entry = {role: config.role, name, enabled:
        !el.matches(':disabled') && !el.closest('[aria-disabled="true"]'), text: name.slice(0, 120)};
    for (const attr of ['aria-checked', 'aria-selected', 'aria-expanded', 'aria-busy']) {
        if (el.hasAttribute(attr)) entry[attr] = el.getAttribute(attr);
    }
    if ('checked' in el) entry.checked = el.checked;
    if (config.role === 'textbox' && 'value' in el) {
        if (privateValue) entry.redacted = true;
        else {
            entry.value = String(el.value).slice(0, 200);
            entry.text = name.slice(0, 120) + '=' + entry.value;
        }
    }
    return entry;
}).filter(Boolean)"""


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode('utf-8')).hexdigest()


def _wait_ms(value: Any) -> int:
    if type(value) is not int or not 0 < value <= _MAX_WAIT_MS:
        raise ValueError(f'wait must be an integer in 1..{_MAX_WAIT_MS} ms')
    return value


class PlaywrightBrowserObserver:
    """Freshness includes observed widget state; no cross-step cache reuse.

    The bounded fingerprint is not proof against hostile DOM or same-state node
    replacement. Foreground ownership and effect verification remain external.
    """
    name = 'playwright-chromium'
    _ids = itertools.count(1)

    def __init__(self, page: Any, *, max_observation_age_s: float = 30.0) -> None:
        if (type(max_observation_age_s) not in (int, float) or
                not math.isfinite(max_observation_age_s) or max_observation_age_s <= 0):
            raise ValueError('finite positive observation age required')
        self.page = page
        self.max_observation_age_s = max_observation_age_s
        self._gen = 0
        self._last_id = ''
        self._last_digest = ''
        self._observed_at = 0.0
        self._observed_mono = 0.0
        self.last_metrics: dict[str, float | int] = {}

    def _snapshot(self) -> dict[str, Any]:
        before = self.page.evaluate('() => ({title: document.title, url: location.href})')
        elements: list[dict] = []
        for role in _ROLES:
            elements.extend(self.page.get_by_role(role).evaluate_all(
                _ROLE_SNAPSHOT, {'role': role, 'limit': _MAX_ELEMENTS}))
        after = self.page.evaluate('() => ({title: document.title, url: location.href})')
        if before != after:
            raise RuntimeError('page navigated while observing; re-observe')
        return {'foreground': {'app': 'Chromium', **after, 'tab_id': '0'},
                'elements': elements}

    def observe(self) -> Observation:
        started = time.perf_counter()
        snapshot = self._snapshot()
        self._observed_at = time.time()
        self._observed_mono = time.monotonic()
        self._gen += 1
        self._last_id = f'obs_{next(self._ids)}'
        self._last_digest = _digest(snapshot)
        self.last_metrics = {'duration_ms': (time.perf_counter() - started) * 1000,
                             'browser_read_calls': len(_ROLES) + 2,
                             'elements': len(snapshot['elements'])}
        return Observation(id=self._last_id, created_at=self._observed_at, generation=self._gen,
                           foreground=snapshot['foreground'], summary=snapshot['foreground']['title'],
                           ui_tree={'elements': snapshot['elements']},
                           sensitive=any(e.get('redacted', False) for e in snapshot['elements']))

    def is_current(self, obs: Observation) -> bool:
        if (obs.id != self._last_id or obs.generation != self._gen or
                obs.created_at != self._observed_at or
                not 0 <= time.monotonic() - self._observed_mono <= self.max_observation_age_s):
            return False
        try:
            supplied = {'foreground': obs.foreground,
                        'elements': (obs.ui_tree or {}).get('elements', [])}
            return (_digest(supplied) == self._last_digest and
                    _digest(self._snapshot()) == self._last_digest)
        except Exception:  # browser closed, navigated or incomplete read => stale
            return False


class PlaywrightBrowserActuator:
    """Strict actionability; a locator receipt never proves generation success."""
    name = 'playwright-chromium'

    def __init__(self, page: Any, *, default_wait_ms: int = 500) -> None:
        self.page = page
        self.default_wait_ms = _wait_ms(default_wait_ms)

    def _locator(self, target: SemanticTarget) -> Any:
        role = (target.role or '').lower()
        name = target.name or target.text
        if role not in _ROLES or not name or len(name) > 1024:
            raise RuntimeError('supported role and exact bounded accessible name required')
        return self.page.get_by_role(role, name=name, exact=True)

    def _locate(self, step: PlanStep) -> Any:
        loc = self._locator(step.target)
        count = loc.count()
        if count != 1:
            raise RuntimeError(f'semantic target missing or ambiguous ({count} matches); re-observe')
        return loc  # no `.first`, no force=True; strict at actual dispatch too

    def act(self, step: PlanStep, obs: Any, *, action_id: str = '', side_effect_id: str = '') -> Any:
        detail = self._perform(step)
        if side_effect_id:
            from ...apprentice.models import EffectReceipt
            return EffectReceipt(side_effect_id=side_effect_id, action_id=action_id,
                                 action_type=step.kind.value, observed_at=time.time(),
                                 evidence_source=self.name)
        return {'detail': detail}

    def _wait(self, step: PlanStep) -> str:
        condition = step.args.get('until')
        if condition is None:
            self.page.wait_for_timeout(_wait_ms(step.args.get('ms', self.default_wait_ms)))
            return 'waited (explicit duration; not readiness proof)'
        if (type(condition) is not dict or set(condition) != {'role', 'name', 'state'} or
                not all(type(v) is str for v in condition.values())):
            raise ValueError('until requires only role, name, state strings')
        state = condition['state']
        if state not in {'visible', 'hidden', 'enabled', 'disabled'}:
            raise ValueError('unsupported wait condition')
        timeout = _wait_ms(step.args.get('timeout_ms', 10_000))
        loc = self._locator(SemanticTarget(condition['role'], condition['name']))
        if state in {'enabled', 'disabled'}:
            from playwright.sync_api import expect
            (expect(loc).to_be_enabled if state == 'enabled' else expect(loc).to_be_disabled)(timeout=timeout)
        else:
            loc.wait_for(state=state, timeout=timeout)
        return f'condition observed: {state}; independent post-state verification still required'

    def _perform(self, step: PlanStep) -> str:
        if step.kind is ActionKind.BROWSER and step.args.get('op') == 'navigate':
            self.page.goto(str(step.args.get('url')), timeout=30_000, wait_until='domcontentloaded')
            return 'navigated'
        if step.kind is ActionKind.WAIT:
            return self._wait(step)
        if step.kind is ActionKind.TAKE_SCREENSHOT:
            raise RuntimeError('screenshot capture is not implemented by this adapter')
        if step.target is None:
            raise RuntimeError(f'{step.kind} requires a semantic target')
        loc = self._locate(step)
        if step.kind is ActionKind.TYPE:
            loc.fill(str(step.text or ''), timeout=10_000)
            return f'typed into {step.target.label()}'
        if step.kind is ActionKind.DOUBLE_CLICK:
            loc.dblclick(timeout=10_000)
            return f'double-clicked {step.target.label()}'
        if step.kind in (ActionKind.CLICK, ActionKind.UI_INVOKE):
            loc.click(timeout=10_000)
            return f'clicked {step.target.label()}'
        if step.kind is ActionKind.FOCUS:
            loc.focus(timeout=10_000)
            return 'focused'
        raise RuntimeError(f'unsupported action kind {step.kind}')
