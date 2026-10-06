"""Desk session = source panel + modes. Observe: read and show. Coach: also explain options and recommend. Control: also execute, through
the Executor, only on surfaces whose adapter is proven for acting (the owner's own trainer). Pause, STOP and 'change source' always work;
a lost/replaced source stops control and is never silently swapped for a lookalike."""
from __future__ import annotations

import random
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from .adapters import registry
from .adapters.poker_train_panel import TrainerPanelReader
from .coach import recommend
from .control.backends import PointerBackend
from .control.executor import Executor, Fresh
from .control.identity import GuardConfig, IdentityGuard
from .control.journal import Journal
from .control.locator import VisionLocator
from .reconcile import Config, Reconciler
from .sandbox import SandboxSource, SourceLost
from .sources import DirSource

DESK_MODES = ("observe", "coach", "control")
OBSERVE_ONLY_WHY = {
    "replay": "запись: действовать нечем",
    "window": "внешнее окно: автоматические действия не доказаны и не разрешены владельцем",
}


def control_allowed(spec: dict, adapter) -> tuple[bool, str]:
    if spec.get("kind") != "sandbox":
        return False, OBSERVE_ONLY_WHY.get(spec.get("kind"), "источник не подтверждён для управления")
    if not adapter.capabilities.act:
        return False, f"адаптер {adapter.id} не имеет act (нет прогона)"
    return True, ""


def coach_allowed(spec: dict, adapter) -> tuple[bool, str]:
    if not adapter.capabilities.advise_live and spec.get("kind") != "replay":
        return False, f"адаптер {adapter.id}: подсказки для этого интерфейса не подтверждены (UNVERIFIED)"
    return True, ""


class DeskMixin:
    # ------------------------------------------------------------ start / modes
    def start_desk(self, source: dict, adapter: str = "poker_train", desk_mode: str = "observe", seed: int = 1, sims: int = 300,
                   max_hands: int = 40, auto_deal: bool = True, verify_timeout_s: float = 6.0, bench: dict | None = None) -> dict:
        if desk_mode not in DESK_MODES:
            raise ValueError(f"desk_mode: one of {DESK_MODES}")
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise RuntimeError("a session is already running: STOP it first")
            self.reset_session_state(); self.stop_ev.clear()
            ad = registry.get(adapter, self.profile_dir / f"{adapter}.json" if self.profile_dir else None)
            ad.reset()
            self.adapter = ad
            ok, why = (True, "")
            if desk_mode == "coach":
                ok, why = coach_allowed(source, ad)
            if desk_mode == "control":
                ok, why = control_allowed(source, ad)
            if not ok:
                raise PermissionError(why)
            if source.get("kind") == "sandbox":
                from .sources import assert_loopback
                assert_loopback(source.get("url") or "http://127.0.0.1:3000/")
            elif source.get("kind") == "replay":
                src = DirSource(source["path"]); self.src = src
            elif source.get("kind") == "window":
                raise NotImplementedError("live window capture is NOT_RUN in this build container (Windows only); use the sandbox or replay")
            else:
                raise ValueError(f"unknown source kind {source.get('kind')!r}")
            self.rec = Reconciler(Config(stale_ms=1500))
            self.desk = {"mode": desk_mode, "paused": False, "source": dict(source), "lost": None, "window_reasons": [], "auto_deal": auto_deal,
                         "recommendation": None, "rec_key": None, "executor": None, "verify_timeout_s": verify_timeout_s, "bench": bench or {}}
            self.session = {"id": f"s{int(time.time())}", "mode": "desk", "adapter": adapter, "act": desk_mode == "control", "started": time.time(),
                            "path": source.get("path"), "url": source.get("url"), "source_kind": source.get("kind")}
            self._rng = random.Random(seed); self._sims = sims; self._max_hands = max_hands
            self._max_frames = 10 ** 9; self._interval = 0.0
            self.journal = Journal(self.data_dir / "journal", self.session["id"])
            self.thread = threading.Thread(target=self._desk_loop, name="pokervision-desk", daemon=True)
            self.thread.start()
            return self.status()

    def set_desk_mode(self, mode: str) -> dict:
        if mode not in DESK_MODES:
            raise ValueError(f"mode: one of {DESK_MODES}")
        d = getattr(self, "desk", None)
        if not d or not self.thread or not self.thread.is_alive():
            raise RuntimeError("no running desk session")
        ok, why = (True, "")
        if mode == "coach":
            ok, why = coach_allowed(d["source"], self.adapter)
        if mode == "control":
            ok, why = control_allowed(d["source"], self.adapter)
            if ok and d["executor"] is not None and d["executor"].halted:
                ok, why = False, f"управление остановлено: {d['executor'].halted}; нужен осмотр владельца (resume)"
        if not ok:
            raise PermissionError(why)
        d["mode"] = mode
        self.session["act"] = mode == "control"
        return self.status()

    def set_paused(self, paused: bool) -> dict:
        d = getattr(self, "desk", None)
        if not d:
            raise RuntimeError("no desk session")
        d["paused"] = bool(paused)
        return self.status()

    def resume_executor(self) -> dict:
        """Owner reviewed a halt: allow control again. Re-verifies the profile first (hero cards readable on the CURRENT frame); a resized
        window is re-bound at its new size only after that check. Every per-action gate still applies afterwards."""
        d = getattr(self, "desk", None)
        if not (d and d.get("executor")):
            return self.status()
        ex, g = d["executor"], d.get("guard")
        if self.src is not None and getattr(self.src, "sandbox", False) and g is not None:
            snap = self.src.desk.snapshot(self.src.ident)
            cm = self.committed
            ok = bool(cm and all(cm.hero_cards)) or bool(self.last_state and (self.last_state.quality or {}).get("buttons"))
            if snap.rect is not None and ok:
                g.rebind_size((snap.rect.w, snap.rect.h))
            elif snap.rect is not None:
                d["resume_refused"] = "профиль не подтверждён на текущем размере/кадре: hero-карты и кнопки не читаются"
                return self.status()
        d.pop("resume_refused", None)
        ex.resume_after_owner_review()
        return self.status()

    def sandbox_cmd(self, cmd: str, **kw) -> dict:
        s = self.src
        if not getattr(s, "sandbox", False):
            raise PermissionError("sandbox commands exist only for the sandbox desk")
        return s.submit(cmd, **kw)

    # ------------------------------------------------------------ loop
    def _fresh(self) -> Fresh:
        src = self.src
        fr = src.read()
        st = self.adapter.read(fr)
        now_ms = src.now_ms() if hasattr(src, "now_ms") else fr.t_ms
        cm = self.rec.push(st, now_ms=now_ms, frame_bytes=None if hasattr(src, "alive") else fr.bgr.tobytes())
        if hasattr(src, "alive") and not src.alive():
            cm.blocked.append("SOURCE_NOT_RESPONDING (page liveness probe failed)")
        with self.lock:
            self.last_frame, self.last_state, self.committed = fr, st, cm
            self.frames += 1
            self._frame_seq = getattr(self, "_frame_seq", 0) + 1
        return Fresh(fr, st, cm, max(0.0, now_ms - fr.t_ms))

    def _build_executor(self) -> Executor:
        src = self.src
        guard = IdentityGuard(src.ident, (src.desk.rect_phys(src.wid).w, src.desk.rect_phys(src.wid).h), GuardConfig())
        panel = TrainerPanelReader()
        b = self.desk["bench"]
        click = b["click_wrap"](src) if b.get("click_wrap") else src.click_phys
        ex = Executor(read_fresh=self._fresh, locator=b.get("locator") or VisionLocator(), backend=PointerBackend(click), probe=src.desk, guard=guard,
                      stop=self.stop_ev, journal=self.journal, panel=panel, pointer_scale=src.pointer_scale,
                      verify_timeout_s=self.desk["verify_timeout_s"], **({"settle_s": b["settle_s"]} if "settle_s" in b else {}))
        if b.get("before_press_hook"):
            ex.before_press_hook = lambda: b["before_press_hook"](self)
        return ex

    def _desk_loop(self) -> None:
        d = self.desk
        try:
            if d["source"]["kind"] == "sandbox":
                s = d["source"]
                self.src = SandboxSource(s.get("url") or "http://127.0.0.1:3000/", s.get("bootstrap", "cash_nl10"), float(s.get("dpr", 1.0)))
                d["executor"] = self._build_executor()
                d["guard"] = d["executor"].guard
            n = 0
            while not self.stop_ev.is_set():
                t0 = time.perf_counter()
                try:
                    fresh = self._fresh()
                except SourceLost as exc:
                    d["lost"] = str(exc)
                    if d["executor"]:
                        d["executor"].halt(f"SOURCE_LOST: {exc}")
                    time.sleep(0.25)
                    if hasattr(self.src, "pump"):
                        self.src.pump()
                    continue
                if fresh is None:
                    break
                d["lost"] = None
                self.latency_ms.append((time.perf_counter() - t0) * 1000)
                if d.get("guard"):
                    d["window_reasons"] = d["guard"].reasons(self.src.desk.snapshot(self.src.ident), fresh.age_ms)
                    if d["window_reasons"] and d["executor"] and d["mode"] == "control":
                        d["executor"].halt("window check: " + ", ".join(d["window_reasons"]))
                self._queue_for_labelling(fresh.frame, fresh.state)
                if d["mode"] in ("coach", "control") and not d["paused"]:
                    self._deal_assist(fresh)
                    self._coach_step(fresh)
                    if d["mode"] == "control" and not d["window_reasons"]:
                        self._control_step(fresh)
                if self.src.__class__.__name__ == "DirSource":
                    time.sleep(0.02)
                n += 1
        except Exception as exc:  # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            try: self.rec.finish()
            except Exception: pass
            try: self.src.close()
            except Exception: pass
            self.finished = True

    def _deal_assist(self, fresh: Fresh) -> None:
        """TEST-HARNESS assist in the sandbox only: presses the trainer's DEAL through its DOM (the executor itself never does)."""
        d, ex = self.desk, self.desk.get("executor")
        if d["auto_deal"] and getattr(self.src, "sandbox", False) and not (ex and ex.halted) and fresh.committed.hero_turn is not True \
                and len(self.decisions) < self._max_hands * 6 and time.time() - getattr(self, "_last_deal", 0) > 1.5:
            self._last_deal = time.time()
            self.src.desk.deal()

    def _coach_step(self, fresh: Fresh) -> None:
        d, cm = self.desk, fresh.committed
        key = (tuple(cm.hero_cards), len([c for c in cm.board if c]), cm.pot.amount if cm.pot else None, tuple(map(tuple, cm.actions or [])), cm.hero_turn, tuple(cm.blocked))
        if key == d["rec_key"]:
            return
        d["rec_key"] = key
        rc = recommend(cm, self._rng, self._sims)
        d["recommendation"] = rc

    def _control_step(self, fresh: Fresh) -> None:
        d = self.desk; ex: Executor = d["executor"]
        rc = d.get("recommendation")
        if ex is None or ex.halted or not rc or not rc.ok or rc.decision is None:
            return
        if rc.decision.t_ms != fresh.committed.t_ms and fresh.committed.t_ms - rc.decision.t_ms > 1500:
            d["rec_key"] = None                         # decided on an old state: re-decide on the current one
            return
        if len(self.decisions) >= self._max_hands * 6:
            return
        decision = rc.decision
        if d["bench"].get("decide"):
            decision = d["bench"]["decide"](self, rc, fresh) or decision
        if d["bench"].get("pre_execute"):
            d["bench"]["pre_execute"](self)
        res = ex.execute(decision)
        d["rec_key"] = None
        if d["bench"].get("post_execute"):
            d["bench"]["post_execute"](self, decision, res)
        rec = {"hand": list(fresh.committed.hero_cards), "board": list(fresh.committed.board), "decision": rc.decision.kind, "raise_to": rc.decision.raise_to,
               "reason": rc.decision.reason, "equity": rc.equity, "ok": res.ok, "halted": res.halted, "steps": res.steps, "t": time.time(),
               "note": "equity vs random hands; not optimal play, not a profit claim"}
        self.decisions.append(rec)

    # ------------------------------------------------------------ views for the page
    def desk_status(self) -> dict | None:
        d = getattr(self, "desk", None)
        if not d:
            return None
        ex = d.get("executor")
        return {"mode": d["mode"], "paused": d["paused"], "source": d["source"], "lost": d["lost"], "window_reasons": d["window_reasons"],
                "control_allowed": control_allowed(d["source"], self.adapter) if self.adapter else (False, ""),
                "coach_allowed": coach_allowed(d["source"], self.adapter) if self.adapter else (False, ""),
                "executor": None if ex is None else {"halted": ex.halted, "n_actions": ex.n_actions},
                "identity": getattr(getattr(self.src, "ident", None), "to_dict", lambda: None)(),
                "sha": getattr(getattr(self, "journal", None), "sha", None), "frame_seq": getattr(self, "_frame_seq", 0),
                "resume_refused": d.get("resume_refused")}

    def frame_jpeg(self, quality: int = 70) -> tuple[int, bytes] | None:
        with self.lock:
            fr = self.last_frame
            seq = getattr(self, "_frame_seq", 0)
        if fr is None:
            return None
        ok, buf = cv2.imencode(".jpg", fr.bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return (seq, buf.tobytes()) if ok else None

    def overlay_json(self) -> dict | None:
        with self.lock:
            fr, st, cm = self.last_frame, self.last_state, self.committed
            seq = getattr(self, "_frame_seq", 0)
        if fr is None or st is None:
            return None
        boxes = [{"x": b["x"], "y": b["y"], "w": b["w"], "h": b["h"], "field": b["field"], "label": str(b["label"]), "ok": bool(b["ok"]), "conf": b.get("conf")}
                 for b in (st.quality or {}).get("boxes", [])]
        return {"seq": seq, "frame_id": fr.frame_id, "t_ms": fr.t_ms, "w": fr.w, "h": fr.h, "boxes": boxes,
                "blocked": list(cm.blocked) if cm else [], "hero_turn": cm.hero_turn if cm else None}

    def recommendation_view(self) -> dict:
        d = getattr(self, "desk", None)
        if not d:
            return {"ok": False, "why_not": "нет сессии"}
        if d["mode"] == "observe":
            return {"ok": False, "why_not": "режим «Наблюдение»: рекомендации выключены"}
        rc = d.get("recommendation")
        return rc.to_dict() if rc else {"ok": False, "why_not": "ожидание подтверждённого состояния"}

    def journal_view(self) -> dict:
        j = getattr(self, "journal", None)
        return {"sha": j.sha if j else None, "records": j.read() if j else [], "dir": str(j.dir) if j else None}
