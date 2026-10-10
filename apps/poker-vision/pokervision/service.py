"""One backend for UX and CMD: the Bossman page, the CLI and tests all call this class (via the HTTP API in api.py).

Pipeline per frame: source -> adapter.read -> reconcile (validated, committed) -> history; optional gated action in
the owner's own trainer. STOP is checked before every frame and every click and always wins.
"""
from __future__ import annotations

import json
import random
import statistics
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from . import __version__
from .actuator import ActionRefused, TrainerActuator
from .adapters import registry
from .adapters.base import Frame
from .history import POKER_TRAIN_UNOBSERVABLE, derive_events, to_phh
from .reconcile import Config, Reconciler
from .schema import Money
from .sources import DirSource, LoopbackBrowserSource, NotLoopback, ScreenWindowSource, VideoSource, list_windows, window_support
from .strategy import VisibleInfo, decide
from .desk import DeskMixin

EXPLAIN = {
    "no_anchor": "на кадре не найдены карты героя — нечего привязывать (нет раздачи или карты закрыты)",
    "card_scale_mismatch_animating": "карта в анимации (размер не совпадает) — значение не читаю",
    "board_layout_inconsistent": "карты борда расположены неправильно (перекрытие/анимация) — борд не фиксирую",
    "geometry_mismatch": "число обрезано или перекрыто другим элементом — не читаю",
    "glyph_ambiguous": "цифра неоднозначна — лучше UNKNOWN, чем угадывать",
    "glyph_height_off": "высота символов не совпала с эталоном (обрезка/перекрытие)",
    "not_found": "поле не найдено на кадре",
    "suit_votes_disagree": "две проверки масти не согласны",
    "rank_ambiguous": "ранг карты неоднозначен",
    "too_occluded": "карта закрыта больше чем на 70%",
    "layout_not_calibrated": "раскладка не калибрована — для неё нужна калибровка и проверка на отложенных кадрах",
    "layout_not_verified": "профиль раскладки не прошёл проверку на отложенных кадрах",
    "button_text_unreadable": "подписи кнопок не читаются",
    "incomplete_button_set": "набор кнопок прочитан не полностью — действия не фиксирую",
}


def explain(reason: str) -> str:
    for k, v in EXPLAIN.items():
        if reason and k in reason:
            return v
    return reason or ""


class VisionService(DeskMixin):
    def __init__(self, data_dir: str | Path, profile_dir: Path | None = None):
        self.data_dir = Path(data_dir); self.data_dir.mkdir(parents=True, exist_ok=True)
        self.profile_dir = profile_dir
        self.lock = threading.RLock()
        self.stop_ev = threading.Event()
        self.thread: threading.Thread | None = None
        self.reset_session_state()

    # ------------------------------------------------------------ state
    def reset_session_state(self) -> None:
        self.session = None
        self.src = self.adapter = self.rec = self.actuator = None
        self.last_frame = None; self.last_state = None; self.committed = None
        self.latency_ms: list[float] = []
        self.frames = 0; self.error = None
        self.events: list[dict] = []
        self.decisions: list[dict] = []
        self.auto_act = False
        self.finished = False
        self.desk = None; self.journal = None; self._frame_seq = 0

    def capabilities(self) -> dict:
        return {"version": __version__, "adapters": registry.available(), "windows": list_windows(), "window_support": window_support(),
                "modes": ["replay", "trainer", "window"],
                "policy": {"act": "only the owner's own trainer on loopback; never an external client",
                           "external_live_advice": "disabled"}}

    # ------------------------------------------------------------ control
    def start(self, mode: str, adapter: str = "poker_train", path: str | None = None, url: str | None = None,
              act: bool = False, window: dict | None = None, max_frames: int = 100000, interval_s: float = 0.0,
              seed: int = 1, sims: int = 300, bootstrap: str | None = None, max_hands: int = 40) -> dict:
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise RuntimeError("a session is already running: STOP it first")
            self.reset_session_state(); self.stop_ev.clear()
            ad = registry.get(adapter, self.profile_dir / f"{adapter}.json" if self.profile_dir else None)
            if mode == "replay":
                if not (ad.capabilities.replay):
                    raise ValueError(f"{adapter}: replay not supported")
                src = VideoSource(path) if str(path).lower().endswith((".mp4", ".mkv", ".avi", ".webm")) else DirSource(path)
                act = False
            elif mode == "trainer":
                if adapter != "poker_train":
                    raise ValueError("live trainer mode exists only for the owner's own Poker Train")
                from .sources import assert_loopback
                assert_loopback(url or "http://127.0.0.1:3000/")                         # refuse synchronously; the browser is built in the loop thread
                src = ("lazy-trainer", url or "http://127.0.0.1:3000/", bootstrap)
            elif mode == "window":
                if adapter == "poker_train":
                    # the Poker Train profile is verified only for the owner's trainer inside the browser Bossman opens itself; its theme
                    # imitates the commercial WSOP client, so reading an arbitrary captured window with it would be live reading of a
                    # third-party game. Other windows need their own ROI calibration + held-out verification (ton_poker / RoiAdapter).
                    raise ActionRefused("poker_train reads only the owner's trainer opened by Bossman (trainer/desk), not a captured window")
                if not ad.capabilities.observe:
                    raise ValueError(f"{adapter}: observe not supported")
                if act:
                    raise ActionRefused("acting through a captured window is not allowed: observation only")
                if not window or "rect" not in window:
                    raise ValueError("window mode needs {'rect': [x, y, w, h]} chosen by the owner")
                src = ScreenWindowSource(window["rect"], window.get("title", "window"))
            else:
                raise ValueError(f"unknown mode {mode!r}")
            if act and not ad.capabilities.act:
                raise ActionRefused(f"{adapter} has no act capability")
            ad.reset()
            self.src, self.adapter = src, ad
            self.rec = Reconciler(Config(stale_ms=1500))
            self.actuator = None                                  # built in the loop thread once the browser exists
            self.auto_act = bool(act and mode == "trainer")
            self.session = {"id": f"s{int(time.time())}", "mode": mode, "adapter": adapter, "act": self.auto_act, "started": time.time(),
                            "path": path, "url": url}
            self._rng = random.Random(seed); self._sims = sims; self._max_frames = max_frames; self._interval = interval_s; self._max_hands = max_hands; self._stuck_s = 12.0
            self.thread = threading.Thread(target=self._loop, name="pokervision-loop", daemon=True)
            self.thread.start()
            return self.status()

    def stop(self, join_s: float = 8.0) -> dict:
        """STOP: no further frames are processed and no further clicks are made, even mid-decision."""
        self.stop_ev.set()
        t = self.thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(join_s)
        return self.status()

    def wait(self, timeout: float = 120.0) -> bool:
        t = self.thread
        if t:
            t.join(timeout)
            return not t.is_alive()
        return True

    # ------------------------------------------------------------ loop
    def _loop(self) -> None:
        try:
            if isinstance(self.src, tuple):                      # Playwright objects are thread-bound: create them here
                _, url, boot = self.src
                self.src = LoopbackBrowserSource(url, bootstrap=boot)
                if self.auto_act:
                    self.actuator = TrainerActuator(self.src, self.stop_ev)
            n = 0
            while not self.stop_ev.is_set() and n < self._max_frames:
                t0 = time.perf_counter()
                fr = self.src.read()
                if fr is None:
                    break
                st = self.adapter.read(fr)
                now_ms = self.src.now_ms() if hasattr(self.src, "now_ms") else None     # live: age = capture clock now - frame time
                has_probe = hasattr(self.src, "alive")
                cm = self.rec.push(st, now_ms=now_ms, frame_bytes=None if has_probe else fr.bgr.tobytes())     # pixel-freeze test only without a liveness probe
                if has_probe and not self.src.alive():
                    cm.blocked.append("SOURCE_NOT_RESPONDING (page liveness probe failed)")
                with self.lock:
                    self.last_frame, self.last_state, self.committed = fr, st, cm
                    self.frames += 1
                    self.latency_ms.append((time.perf_counter() - t0) * 1000)
                n += 1
                self._queue_for_labelling(fr, st)
                if self.auto_act and self.actuator:
                    self._maybe_deal(cm)
                    self._maybe_act(cm, fr)
                if self._interval:
                    time.sleep(self._interval)
        except Exception as exc:  # noqa: BLE001 - surface, never swallow
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                self.rec.finish()
            except Exception:
                pass
            try:
                self.src.close()
            except Exception:
                pass
            self.finished = True

    def _maybe_deal(self, cm) -> None:
        """Start the next hand in the owner's trainer (its DEAL button) when no hand is in progress. Same gates as any click."""
        act = self.actuator
        if self.stop_ev.is_set() or act.halted or cm.hero_turn is True or time.time() - getattr(self, "_last_deal", 0) < 1.5:
            return
        if len(self.decisions) >= self._max_hands:
            return
        btns = [b for b in self.src.page.query_selector_all("button") if b.inner_text().strip() == "DEAL"]
        if len(btns) == 1:
            self._last_deal = time.time()
            act.click("DEAL")

    def _maybe_act(self, cm, fr: Frame) -> None:
        act = self.actuator
        # watchdog: it is hero's turn but the state stays unactionable (buttons unreadable, blocked) -> stop acting, say why
        if cm.hero_turn is True:
            self._turn_since = getattr(self, "_turn_since", None) or time.time()
            if time.time() - self._turn_since > self._stuck_s and not act.halted:
                act.halt(f"hero's turn for {self._stuck_s:.0f}s but the state is not actionable: " + ("; ".join(cm.blocked) or "actions unreadable"))
        else:
            self._turn_since = None
        why = act.guard(cm, self.src.now_ms() if hasattr(self.src, 'now_ms') else fr.t_ms, self.adapter.capabilities)
        if why:
            return
        if not cm.actions or not cm.pot or not cm.hero_stack:
            return
        cur = self.last_state.actions if self.last_state is not None else None
        if cur is None or not cur.known or [tuple(a) for a in cur.value] != [tuple(a) for a in cm.actions]:
            return                                                  # the committed buttons are not what is on screen right now: wait
        labels = tuple(a[0] for a in cm.actions)
        amounts = {a[0]: a[1] for a in cm.actions}
        # the amount to call comes from what is on the buttons (CHECK => 0, CALL n => n); never silently 0 when it is unknown
        if "CHECK" in amounts:
            to_call = 0.0
        elif amounts.get("CALL") is not None:
            to_call = float(amounts["CALL"])
        elif amounts.get("ALL IN") is not None:
            to_call = min(float(amounts["ALL IN"]), cm.hero_stack.amount)
        else:
            return
        if cm.to_call is not None and abs(cm.to_call.amount - to_call) > max(cm.to_call.step, 1.0) and "CHECK" not in amounts:
            return                                                    # two readings disagree: do not act
        n_opp = max(1, len([s for s in cm.seats.values() if s.get("stack")]))
        info = VisibleInfo(tuple(cm.hero_cards), tuple(c for c in cm.board if c), cm.pot.amount, to_call,
                           cm.hero_stack.amount, min(n_opp, 5), labels)
        ch = decide(info, self._rng, self._sims)
        label = ch.action
        if label not in labels:
            label = "CHECK" if "CHECK" in labels else "FOLD" if "FOLD" in labels else None
        if label is None:
            act.halt("no legal label"); return
        rec = {"hand": cm.hero_cards, "board": info.board, "pot": info.pot, "to_call": info.to_call, "equity": round(ch.equity, 3),
               "pot_odds": round(ch.pot_odds, 3), "choice": ch.action, "clicked": label, "reason": ch.reason, "t": fr.t_ms,
               "note": "equity vs random hands; not optimal play, not a profit claim"}
        try:
            act.click(label)
            if label == "RAISE":
                time.sleep(0.4); act.click("CONFIRM")
        except ActionRefused as exc:
            rec["refused"] = str(exc)
            self._refused = getattr(self, "_refused", 0) + 1
            if self._refused >= 3 or not str(exc).startswith("button"):
                act.halt(str(exc))                                  # repeated/unknown refusal: stop and say why
            self.decisions.append(rec); return
        self._refused = 0
        self.decisions.append(rec)
        # verify: within a few frames hero must no longer be on turn with the same state; otherwise halt (ambiguity)
        deadline = time.time() + 6.0
        base = (cm.pot.amount, len(cm.board))
        while time.time() < deadline and not self.stop_ev.is_set():
            fr2 = self.src.read(); st2 = self.adapter.read(fr2)
            cm2 = self.rec.push(st2, now_ms=fr2.t_ms)
            with self.lock:
                self.last_frame, self.last_state, self.committed = fr2, st2, cm2
                self.frames += 1
            if cm2.hero_turn is False or (cm2.pot and (cm2.pot.amount, len(cm2.board)) != base):
                rec["verified"] = True; return
            time.sleep(0.15)
        rec["verified"] = False
        act.halt("click not confirmed by a state change")

    # ------------------------------------------------------------ labelling queue
    def _queue_for_labelling(self, fr: Frame, st) -> None:
        reasons = []
        core = [("hero", st.hero_cards[0] if st.hero_cards else None), ("pot", st.pot)]
        if st.issues:
            reasons.append("validator:" + ",".join(sorted({i["code"] for i in st.issues})))
        if st.quality.get("anchor") and any(f is not None and not f.known and f.confidence >= 0.2 for _, f in core):
            reasons.append("low_confidence_core_field")
        if not reasons or self.frames % 5:
            return
        d = self.data_dir / "label_queue"; d.mkdir(exist_ok=True)
        name = f"{self.session['id']}_{self.frames:06d}.png"
        cv2.imwrite(str(d / name), fr.bgr)
        with (d / "index.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"frame": name, "source": fr.frame_id, "t_ms": fr.t_ms, "reasons": reasons, "state": "needs_label"}) + "\n")

    # ------------------------------------------------------------ calibration of a NEW layout (ROI adapters)
    def calibrate_roi(self, adapter: str, labelled_dir: str, heldout_dir: str, rois: dict) -> dict:
        """Calibrate on labelled frames, verify on HELD-OUT labelled frames; only a passing profile is saved (and then usable).

        Each directory holds images + ``truth.json`` {"<file>": {"hero_cards": ["As","Kd"], "board": [...], "pot": "1,250", ...}}."""
        import cv2
        from .adapters.roi import RoiAdapter
        if adapter != "ton_poker":
            raise ValueError("only ROI adapters (ton_poker) are calibrated this way; poker_train uses tools/calibrate_poker_train.py")
        def load(d):
            d = Path(d)
            truth = json.loads((d / "truth.json").read_text(encoding="utf-8"))
            return [(Frame(cv2.imread(str(d / name)), 0, f"cal:{d.name}", name), t) for name, t in truth.items() if (d / name).exists()]
        cal, held = load(labelled_dir), load(heldout_dir)
        names_cal, names_held = {f.frame_id for f, _ in cal}, {f.frame_id for f, _ in held}
        if Path(labelled_dir).resolve() == Path(heldout_dir).resolve() or (names_cal & names_held and Path(labelled_dir).name == Path(heldout_dir).name):
            raise ValueError("held-out frames must come from a different directory (and different hands) than the calibration frames")
        ad = registry.get(adapter)
        ad.calibrate(cal, rois, note=f"labelled={labelled_dir}")
        rep = ad.verify(held)
        out = {"adapter": adapter, "calibration_frames": len(cal), "heldout_frames": len(held), "report": rep.__dict__, "saved": False}
        if rep.passed and self.profile_dir:
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            ad.profile.save(self.profile_dir / f"{adapter}.json"); out["saved"] = True
        return out

    def verify_roi(self, adapter: str, heldout_dir: str, context: str = "") -> dict:
        """Re-check a SAVED profile on new held-out frames (changed scale, theme or window layout). The result is appended to a log;
        a failing check withdraws trust: the saved profile is marked unverified until a passing check."""
        import cv2
        d = Path(heldout_dir)
        truth = json.loads((d / "truth.json").read_text(encoding="utf-8"))
        held = [(Frame(cv2.imread(str(d / name)), 0, f"verify:{d.name}", name), t) for name, t in truth.items() if (d / name).exists()]
        ad = registry.get(adapter, self.profile_dir / f"{adapter}.json" if self.profile_dir else None)
        if not getattr(ad, "profile", None):
            raise ValueError(f"{adapter}: no saved profile to verify; calibrate first")
        rep = ad.verify(held)
        rec = {"t": time.time(), "adapter": adapter, "profile": ad.profile_id(), "context": context, "frames": len(held), "passed": rep.passed, "report": rep.__dict__}
        if self.profile_dir:
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            with (self.profile_dir / "verifications.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n")
            if not rep.passed:
                ad.profile.data["verified"] = False
                ad.profile.save(self.profile_dir / f"{adapter}.json")
        return rec

    # ------------------------------------------------------------ views
    def status(self) -> dict:
        with self.lock:
            lat = sorted(self.latency_ms)
            def pct(p): return round(lat[min(len(lat) - 1, int(p * len(lat)))], 1) if lat else None
            return {"session": self.session, "running": bool(self.thread and self.thread.is_alive()), "finished": self.finished,
                    "stopped": self.stop_ev.is_set(), "frames": self.frames, "error": self.error,
                    "latency_ms": {"p50": pct(0.5), "p95": pct(0.95), "n": len(lat)},
                    "hands": (len(self.rec.hands) + (1 if self.rec and self.rec.cur["frames"] else 0)) if self.rec else 0,
                    "actuator": ({"halted": self.actuator.halted, "clicks": len(self.actuator.log.entries)} if self.actuator else None),
                    "layout": self.adapter.profile_id() if self.adapter else None, "desk": self.desk_status()}

    def state(self) -> dict:
        with self.lock:
            if not self.last_state:
                return {"state": None}
            st = self.last_state
            def fv(f):
                v = f.value
                if isinstance(v, Money): v = v.raw or v.amount
                return {"value": v, "status": f.status, "confidence": round(f.confidence, 2), "t_ms": f.t_ms, "source": f.source,
                        "reason": f.reason, "why": explain(f.reason)}
            fields = {"hero_cards": [fv(f) for f in st.hero_cards], "board": [fv(f) for f in st.board], "board_count": fv(st.board_count),
                      "street": fv(st.street), "pot": fv(st.pot), "to_call": fv(st.to_call), "hero_stack": fv(st.hero_stack),
                      "dealer": fv(st.dealer_slot), "seats_visible": fv(st.num_seats), "hero_turn": fv(st.hero_turn), "actions": fv(st.actions)}
            unc = []
            def walk(name, f):
                if isinstance(f, list):
                    for i, x in enumerate(f): walk(f"{name}[{i}]", x)
                elif f["status"] != "OK":
                    unc.append({"field": name, "reason": f["reason"], "why": f["why"]})
            for k, v in fields.items(): walk(k, v)
            return {"frame": st.frame_id, "t_ms": st.t_ms, "layout": st.layout, "fields": fields, "uncertainty": unc,
                    "issues": st.issues, "quality": {k: v for k, v in st.quality.items() if k != "boxes"},
                    "committed": self.committed.to_dict() if self.committed else None,
                    "can_act": bool(self.committed and not self.committed.blocked and self.committed.hero_turn)}

    def overlay_png(self) -> bytes | None:
        with self.lock:
            if self.last_frame is None or self.last_state is None:
                return None
            img = self.last_frame.bgr.copy()
            for b in self.last_state.quality.get("boxes", []):
                col = (80, 200, 80) if b["ok"] else (60, 60, 230)
                cv2.rectangle(img, (b["x"], b["y"]), (b["x"] + b["w"], b["y"] + b["h"]), col, 2)
                cv2.putText(img, str(b["label"])[:14], (b["x"], max(b["y"] - 3, 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1, cv2.LINE_AA)
            ok, buf = cv2.imencode(".png", img)
            return buf.tobytes() if ok else None

    def history(self) -> dict:
        with self.lock:
            hands = list(self.rec.hands) if self.rec else []
            if self.rec and self.rec.cur["frames"]:
                hands = hands + [dict(self.rec.cur)]
            out = []
            for h in hands:
                h = dict(h)
                derive_events(h, POKER_TRAIN_UNOBSERVABLE if self.session and self.session["adapter"] == "poker_train" else ["everything not shown"])
                out.append({k: h[k] for k in ("id", "t_start", "t_end", "status", "linked", "frames", "gaps", "issues", "end_reason", "events", "unobservable", "complete", "flags") if k in h} | {"phh": to_phh(h)})
            return {"hands": out, "decisions": self.decisions}

    def export_history(self) -> Path:
        h = self.history()
        p = self.data_dir / f"history_{self.session['id'] if self.session else 'none'}.json"
        p.write_text(json.dumps(h, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        return p
