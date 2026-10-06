"""EXECUTOR: turns one already-decided PolicyDecision into verified UI actions, or stops. It never decides strategy.

Per click: fresh frame -> window identity/occlusion/size/staleness -> same hand, street, hero's turn, action available ->
amount/units/limits -> RE-LOCATE the element on this frame -> map to screen with DPI/scale/position -> check what is under the
point -> press -> read new frames until the state really changed. Any doubt halts; an unconfirmed result is never retried;
STOP is checked before every step and wins every race."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

from .backends import Backend, ClickRefused
from .geometry import GeometryError, Rect, ScreenMapper
from .identity import IdentityGuard, WindowProbe
from .intent import PolicyDecision, hand_key
from .journal import Journal
from .locator import Target


@dataclass
class Fresh:
    frame: object          # adapters.base.Frame
    state: object          # TableState
    committed: object      # reconcile.Committed
    age_ms: float          # capture clock now - frame time


@dataclass(frozen=True)
class Preset:
    label: str
    value: float
    box: Rect


@dataclass
class Panel:
    open: bool
    raise_to: float | None = None
    presets: list = field(default_factory=list)
    confirm: Target | None = None


class PanelReader(Protocol):
    def read(self, fresh: Fresh) -> Panel: ...


@dataclass
class ExecResult:
    ok: bool
    steps: list = field(default_factory=list)
    halted: str | None = None
    obsolete: str | None = None


class Halt(Exception):
    pass


class Transient(Exception):
    """A single-frame doubt BEFORE any click (e.g. a button not read on this frame): re-read a few fresh frames, then halt."""


class Obsolete(Exception):
    """The decided state no longer exists (new hand/street, hero no longer to act). Nothing was clicked; the decision is dropped, not retried."""


class Executor:
    def __init__(self, *, read_fresh: Callable[[], Fresh], locator, backend: Backend, probe: WindowProbe, guard: IdentityGuard,
                 stop: threading.Event, journal: Journal | None = None, panel: PanelReader | None = None, pointer_scale: float = 1.0,
                 verify_timeout_s: float = 6.0, settle_s: float = 0.25, sleep: Callable[[float], None] = time.sleep, max_actions: int = 500):
        self.read_fresh, self.locator, self.backend, self.probe, self.guard = read_fresh, locator, backend, probe, guard
        self.stop, self.journal, self.panel, self.pointer_scale = stop, journal, panel, pointer_scale
        self.verify_timeout_s, self.settle_s, self.sleep, self.max_actions = verify_timeout_s, settle_s, sleep, max_actions
        self.halted: str | None = None
        self.before_press_hook: Callable[[], None] | None = None     # bench/test only: perturb the world between preflight and the click
        self.n_actions = 0
        self.log: list[dict] = []

    # ------------------------------------------------------------ control
    def halt(self, why: str) -> None:
        self.halted = why
        self._log(event="halt", reason=why)

    def resume_after_owner_review(self) -> None:
        self.halted = None

    def _log(self, **kw) -> dict:
        rec = {"t": time.time(), **kw}
        self.log.append(rec)
        if self.journal:
            self.journal.add(**kw)
        return rec

    def _read(self) -> Fresh:
        try:
            return self.read_fresh()
        except Halt:
            raise
        except Exception as exc:  # noqa: BLE001 - a lost/closed/minimised source (or any capture failure) halts control, it never crashes the loop
            raise Halt(f"SOURCE_LOST: {type(exc).__name__}: {exc}") from exc

    def _check_stop(self, phase: str) -> None:
        if self.stop.is_set():
            raise Halt(f"STOP ({phase})")

    # ------------------------------------------------------------ one decision
    def execute(self, d: PolicyDecision) -> ExecResult:
        res = ExecResult(False)
        try:
            if self.halted:
                raise Halt(f"HALTED: {self.halted}")
            if d.kind in ("FOLD", "CHECK", "CALL"):
                res.steps.append(self._click_step(d, d.kind, final=True))
            else:
                res.steps.append(self._click_step(d, "RAISE", final=False))
                res.steps.append(self._set_amount(d))
                res.steps.append(self._click_step(d, "CONFIRM", final=True, expect_raise_to=res.steps[-1]["amount_read"]))
            res.ok = True
        except Obsolete as o:
            res.obsolete = str(o)
            self._log(event="decision_dropped", reason=str(o))
        except Halt as h:
            res.halted = str(h)
            if not str(h).startswith("STOP"):
                self.halt(str(h))
            else:
                self._log(event="stopped", reason=str(h))
        except (ClickRefused, GeometryError) as exc:
            res.halted = f"REFUSED: {exc}"
            self.halt(res.halted)
        return res

    # ------------------------------------------------------------ preflight
    def _window_check(self, fr: Fresh):
        snap = self.probe.snapshot(self.guard.bound)
        bad = self.guard.reasons(snap, fr.age_ms)
        if bad:
            raise Halt("window check: " + ", ".join(bad))
        cap = (getattr(fr.frame, "meta", None) or {}).get("rect")
        if cap and snap.rect and any(abs(a - b) > 1.5 for a, b in zip(cap, (snap.rect.x, snap.rect.y, snap.rect.w, snap.rect.h))):
            raise Halt("window moved/resized since the frame was captured: coordinates would be wrong")
        return snap

    def _preflight(self, d: PolicyDecision, label: str, *, panel_step: bool = False, retries: int = 6) -> tuple[Fresh, Target, tuple[float, float]]:
        last = ""
        for _ in range(retries):
            try:
                return self._preflight_once(d, label, panel_step=panel_step)
            except Transient as t:
                last = str(t)
                self._check_stop("retry")
                self.sleep(self.settle_s)
        raise Halt(f"{last} (after {retries} fresh frames; nothing was clicked)")

    def _preflight_once(self, d: PolicyDecision, label: str, *, panel_step: bool = False) -> tuple[Fresh, Target, tuple[float, float]]:
        self._check_stop("before frame")
        if self.n_actions >= self.max_actions:
            raise Halt("action budget exhausted")
        fr = self._read()
        snap = self._window_check(fr)
        cm, st = fr.committed, fr.state
        if cm.blocked:
            raise Transient("state blocked: " + "; ".join(cm.blocked))
        if hand_key(list(cm.hero_cards), list(cm.board)) != d.hand_key and not panel_step:
            raise Obsolete("hand/street changed since the decision")
        if cm.hero_turn is not True:
            raise Obsolete("not hero's turn (or unknown)") if cm.hero_turn is False else Transient("hero's turn unknown")
        names = [a[0] for a in (cm.actions or [])]
        if label not in names:
            raise Transient(f"{label} is not an available action now ({names})")
        if label == "CALL" and d.to_call is not None:
            shown = dict((a[0], a[1]) for a in cm.actions).get("CALL")
            if shown is None or abs(shown - d.to_call) > max(getattr(cm.to_call, "step", 1.0) if cm.to_call else 1.0, 1.0):
                raise Transient(f"CALL amount on screen ({shown}) differs from the decided one ({d.to_call})")
        loc = self.locator.locate(fr.frame, st, label)
        if loc.target is None:
            raise Transient("locate: " + loc.reason)
        self._check_stop("after locate")
        if snap.rect is None:
            raise Halt("window rect unknown")
        # layout-shift / animation guard: the target must sit at the same place on the NEXT fresh frame too, otherwise the UI is still moving
        fr2 = self._read()
        loc2 = self.locator.locate(fr2.frame, fr2.state, label)
        if loc2.target is None:
            raise Transient("target vanished on the confirming frame: " + loc2.reason)
        a, b = loc.target.box, loc2.target.box
        tol = max(3.0, 0.004 * fr.frame.h)
        if abs(a.x - b.x) > tol or abs(a.y - b.y) > tol or abs(a.w - b.w) > 2 * tol or abs(a.h - b.h) > 2 * tol:
            raise Transient(f"target moved between two frames ({a.x:.0f},{a.y:.0f} -> {b.x:.0f},{b.y:.0f}): layout still shifting")
        snap = self._window_check(fr2)
        fr, loc = fr2, loc2
        mapper = ScreenMapper(fr.frame.w, fr.frame.h, snap.rect, self.pointer_scale)
        px, py = mapper.target_point(loc.target.box)
        sx, sy = mapper.frame_to_screen(loc.target.box.cx, loc.target.box.cy)
        owner = self.probe.owner_at(sx, sy)
        if owner is not None and owner != self.guard.bound.handle:
            raise Halt(f"another window is on top at the click point (handle {owner})")
        return fr, loc.target, (px, py)

    # ------------------------------------------------------------ steps
    def _press_and_verify(self, d: PolicyDecision, label: str, fr: Fresh, tgt: Target, pt, final: bool, verify) -> dict:
        self._check_stop("before click")
        if self.before_press_hook:
            self.before_press_hook()
            self._check_stop("after hook")
        # last look immediately before the press: same window, same place as when the frame was captured, nothing on top at the point
        snap = self._window_check(fr)
        if pt is not None and snap.rect is not None:
            owner = self.probe.owner_at(*ScreenMapper(fr.frame.w, fr.frame.h, snap.rect, 1.0).frame_to_screen(tgt.box.cx, tgt.box.cy))
            if owner is not None and owner != self.guard.bound.handle:
                raise Halt(f"another window is on top at the click point (handle {owner}) [last check]")
        before = self.journal.frame(f"before_{label}", fr.frame.bgr, fr.state) if self.journal else None
        t0 = time.time()
        out = self.backend.press(label, pt)
        self.n_actions += 1
        after_fresh = None
        ok, why = False, "timeout"
        deadline = time.time() + self.verify_timeout_s
        while time.time() < deadline:
            self.sleep(self.settle_s)
            if self.stop.is_set():
                self._log(event="click_sent_then_stop", label=label)
                raise Halt("STOP (after click, result not verified)")
            after_fresh = self._read()
            ok, why = verify(after_fresh)
            if ok:
                break
        after = self.journal.frame(f"after_{label}", after_fresh.frame.bgr, after_fresh.state) if (self.journal and after_fresh) else None
        step = {"label": label, "target": {"box": [tgt.box.x, tgt.box.y, tgt.box.w, tgt.box.h], "conf": tgt.confidence, "locator": tgt.source},
                "backend": out, "verified": ok, "verify": why, "before": before, "after": after, "latency_ms": round((time.time() - t0) * 1000)}
        self._log(event="action", decision={"kind": d.kind, "raise_to": d.raise_to, "reason": d.reason, "hand": d.hand_key, "street": d.street}, **step)
        if not ok:
            raise Halt(f"{label}: click not confirmed by a state change ({why}); not retried")
        return step

    def _click_step(self, d: PolicyDecision, label: str, final: bool, expect_raise_to: float | None = None) -> dict:
        fr, tgt, pt = self._preflight(d, label, panel_step=(label == "CONFIRM"))
        base_pot = fr.committed.pot.amount if fr.committed.pot else None
        base_key = hand_key(list(fr.committed.hero_cards), list(fr.committed.board))

        def verify(a: Fresh):
            cm = a.committed
            if label == "RAISE":
                names = [x[0] for x in (cm.actions or [])]
                return ("CONFIRM" in names, "raise panel open" if "CONFIRM" in names else "panel not open")
            if hand_key(list(cm.hero_cards), list(cm.board)) != base_key:
                return True, "hand/street changed"
            if cm.hero_turn is False:
                return True, "hero no longer on turn"
            if cm.pot and base_pot is not None and cm.pot.amount != base_pot:
                return True, "pot changed"
            return False, "state unchanged"
        return self._press_and_verify(d, label, fr, tgt, pt, final, verify)

    def _set_amount(self, d: PolicyDecision) -> dict:
        if self.panel is None:
            raise Halt("raise needs a panel reader; none configured")
        self._check_stop("before amount")
        fr = self._read()
        pn = self.panel.read(fr)
        if not pn.open or not pn.presets:
            raise Halt("raise panel/presets not readable")
        target = float(d.raise_to)
        stack = fr.committed.hero_stack.amount if fr.committed.hero_stack else None
        legal = [p for p in pn.presets if (stack is None or p.value <= stack) and d.size_min <= p.value <= d.size_max]
        best = min(legal, key=lambda p: abs(p.value - target), default=None)
        if best is None:
            raise Halt(f"no preset inside the policy's range [{d.size_min:g}, {d.size_max:g}] (have {[p.value for p in pn.presets]}); amount not changed by the clicker")
        # click the preset: re-run the same window/hand checks, then map the preset box
        self._check_stop("before preset")
        snap = self._window_check(fr)
        mapper = ScreenMapper(fr.frame.w, fr.frame.h, snap.rect, self.pointer_scale)
        pt = mapper.target_point(best.box)
        sx, sy = mapper.frame_to_screen(best.box.cx, best.box.cy)
        owner = self.probe.owner_at(sx, sy)
        if owner is not None and owner != self.guard.bound.handle:
            raise Halt("another window is on top at the preset")
        tgt = Target(best.label, best.box, 1.0, "panel", best.value)
        read_back = {}

        def verify(a: Fresh):
            p2 = self.panel.read(a)
            read_back["v"] = p2.raise_to
            if p2.raise_to is not None and abs(p2.raise_to - best.value) <= 0.5:
                return True, f"raise-to reads {p2.raise_to:g}"
            return False, f"raise-to reads {p2.raise_to}, expected {best.value:g}"
        step = self._press_and_verify(d, f"PRESET {best.label}", fr, tgt, pt, False, verify)
        step["amount_read"] = read_back["v"]; step["amount_decided"] = target; step["amount_set"] = best.value
        return step
