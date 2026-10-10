"""Play sessions in the owner's OWN Poker Train (loopback only, no money, simulated opponents).

  python -m pokervision.play_trainer bot   --url http://127.0.0.1:3417/ --hands 20 --out OUT [--headful]
  python -m pokervision.play_trainer duel  --url http://127.0.0.1:3417/ --hands 20 --out OUT          (owner plays, bot only advises)

bot:  screen -> vision -> validated state -> decision (owner's preflop chart, else equity heuristic) -> executor clicks -> verification.
      Every decision is saved as an annotated screenshot and graded against the trainer's own DOM state (scoring only).
duel: the owner plays himself in the opened window; the bot reads the same screen in COACH mode (never clicks) and its recommendation is
      recorded next to the owner's actual action, so the two can be compared hand by hand.

Hard limits are the package's: loopback URL only (anything else is refused before a browser starts), the sandbox desk is the only surface
that can be clicked, STOP / Ctrl+C stops at once. Nothing here can attach to another application or window."""
from __future__ import annotations

import argparse
import json
import re
import threading
import time
from pathlib import Path

import cv2

from .desk import screen_mismatch
from .play_report import annotate, committed_view, grade, summarize, truth_view
from .service import VisionService
from .sources import assert_loopback

TRUTH_JS = (Path(__file__).resolve().parents[1] / "tools" / "truth.js").read_text(encoding="utf-8")
HERO_LOG = re.compile(r"\]\s*Hero\s+(folds|checks|calls|bets|raises|all[- ]?in|goes all[- ]?in)", re.I)


def _hero_lines(lines: list[str]) -> list[str]:
    return [ln for ln in (lines or []) if HERO_LOG.search(ln)]


def owner_action_from_log(lines: list[str], before: list[str] | None = None) -> str | None:
    """The owner's NEW 'Hero ...' line in the trainer's hand log -> FOLD/CHECK/CALL/RAISE (bets, raises and all-ins are RAISE).
    ``before``: the hand log when the decision was opened; only lines added since then count (the log keeps earlier streets)."""
    now = _hero_lines(lines)
    if before is not None:
        old = _hero_lines(before)
        if len(now) <= len(old) and (not now or not old or now[-1] == old[-1]):
            return None
        now = now[len(old):] if len(now) > len(old) else now[-1:]
    for ln in reversed(now):
        m = HERO_LOG.search(ln)
        if m:
            w = m.group(1).lower()
            return {"folds": "FOLD", "checks": "CHECK", "calls": "CALL"}.get(w, "RAISE")
    return None


def _lines(rc: dict, cmv: dict, tv: dict, g: dict, extra: list[tuple[str, str]] | None = None) -> list[tuple[str, str]]:
    mark = lambda f: {"ok": "ok", "wrong": "bad", "unknown": "dim"}.get(g.get(f, "na"), "")
    L = [(f"Раздача #{tv.get('hand') or '?'} · решение бота", "h"),
         (f"Позиция: {cmv['position'] or 'не прочитана'}   (истина {tv.get('position')})", mark("position")),
         (f"Карты: {' '.join(c or '??' for c in cmv['hero_cards'])}   (истина {' '.join(tv.get('hero_cards') or [])})", mark("hero_cards")),
         (f"Борд: {' '.join(c or '??' for c in cmv['board']) or '—'}   (истина {' '.join(tv.get('board') or []) or '—'})", mark("board")),
         (f"Банк: {cmv['pot']}   (истина {tv.get('pot')})", mark("pot")),
         (f"К доплате: {cmv['to_call']}   (истина {tv.get('to_call')})", mark("to_call")),
         (f"Стек: {cmv['hero_stack']}   (истина {tv.get('hero_stack')})", mark("hero_stack")),
         (f"Кнопки: {', '.join(cmv['actions'] or []) or '—'}", mark("actions")),
         ("", ""),
         (f"ДЕЙСТВИЕ: {rc.get('action')}" + (f" до {rc['raise_to']:g}" if rc.get("raise_to") else ""), "h"),
         (f"Источник: {'таблица владельца (префлоп)' if rc.get('source') == 'preflop_chart' else 'эвристика эквити' if rc.get('source') == 'heuristic' else rc.get('source')}", ""),
         (rc.get("explanation") or "", "")]
    if rc.get("equity") is not None:
        L.append((f"Эквити vs случайных рук ≈ {rc['equity']:.0%}, шансы банка требуют {rc['pot_odds']:.0%}", "dim"))
    for e in extra or []:
        L.append(e)
    L.append(("Зелёная рамка = прочитано, красная = UNKNOWN; жёлтый крест = точка клика.", "dim"))
    return L


class Session:
    def __init__(self, args):
        self.args = args
        assert_loopback(args.url)
        self.out = Path(args.out)
        (self.out / "frames").mkdir(parents=True, exist_ok=True)
        self.rows: list[dict] = []
        self.halts: list[dict] = []
        self.interventions: list[dict] = []      # harness presses (stuck detector); never counted as bot decisions
        self.interventions_detail: list[dict] = []
        self.pending: dict = {}
        self.svc = VisionService(self.out / "service")
        self.source = {"kind": "sandbox", "url": args.url, "bootstrap": args.bootstrap, "headless": not args.headful}

    def truth_now(self) -> dict | None:
        try:
            r = self.svc.sandbox_cmd("truth", js=TRUTH_JS)
        except PermissionError:                 # the desk window is still being opened by the loop thread
            return None
        return r.get("result") if r.get("ok") else None

    def _save(self, n: int, hand, kind: str, img) -> str:
        name = f"{n:03d}_hand{hand or 'x'}_{kind}.png"
        cv2.imwrite(str(self.out / "frames" / name), img)
        return f"frames/{name}"

    # ---------------------------------------------------------------- bot mode (executor clicks)
    def _pre(self, svc) -> None:
        d, cm, st, fr = svc.desk, svc.committed, svc.last_state, svc.last_frame
        rc = d["recommendation"]
        self.pending = {"frame": fr.bgr.copy(), "boxes": list((st.quality or {}).get("boxes", [])), "cm": committed_view(cm),
                        "rc": rc.to_dict() | {"source": rc.source}, "truth": svc.src.desk.truth(TRUTH_JS), "t": time.time()}

    def _post(self, svc, decision, res) -> None:
        p, self.pending = self.pending, {}
        if not p:
            return
        tv = truth_view(p["truth"]) if p.get("truth") else {}
        g = grade(p["cm"], tv) if tv else {}
        clicks = [(s["target"]["box"][0] + s["target"]["box"][2] / 2, s["target"]["box"][1] + s["target"]["box"][3] / 2, s["label"])
                  for s in res.steps if s.get("target")]
        extra = [(f"Клики: {', '.join(c[2] for c in clicks) or 'нет'}; подтверждено: {res.ok}" + (f"; остановка: {res.halted}" if res.halted else ""), "ok" if res.ok else "bad")]
        if len(clicks) > 1:
            extra.append(("PRESET/CONFIRM нажаты на панели рейза, открывшейся после RAISE (точки показаны на этом кадре).", "dim"))
        img = annotate(p["frame"], p["boxes"], _lines(p["rc"], p["cm"], tv, g, extra), clicks)
        n = len(self.rows) + 1
        rel = self._save(n, tv.get("hand"), decision.kind, img)
        self.rows.append({"n": n, "hand": tv.get("hand"), "street": decision.street, "decision": decision.kind, "raise_to": decision.raise_to,
                          "source": p["rc"].get("source"), "reason": decision.reason, "read": p["cm"], "truth": tv, "grade": g,
                          "clicks": [{"label": c[2], "x": round(c[0], 1), "y": round(c[1], 1)} for c in clicks], "verified": res.ok,
                          "halted": res.halted, "image": rel, "t": p["t"]})
        with (self.out / "decisions.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(self.rows[-1], ensure_ascii=False) + "\n")

    def run_bot(self) -> dict:
        a = self.args
        svc = self.svc
        svc.start_desk(self.source, desk_mode="control", max_hands=10 ** 6, bench={"pre_execute": self._pre, "post_execute": self._post})
        t0 = time.time()
        start_truth = None
        stuck_key, stuck_since, tick = None, time.time(), 0
        while time.time() - t0 < a.max_minutes * 60:
            time.sleep(0.5)
            tick += 1
            if svc.error:
                break
            if start_truth is None and svc.committed and svc.committed.hero_turn:
                start_truth = self.truth_now()
            if tick % 4 == 0:
                # stuck detector (harness, not the bot): the hero has been on turn with the same table for too long because the bot could
                # not read the buttons. The harness then presses CHECK/FOLD through the trainer's DOM, and the report counts it separately.
                tr = self.truth_now() or {}
                labels = [b["label"] for b in tr.get("buttons", [])]
                key = (tr.get("hand_header"), tr.get("pot_text"), len(tr.get("board", []))) if "FOLD" in labels else None
                if key != stuck_key:
                    stuck_key, stuck_since = key, time.time()
                elif key is not None and time.time() - stuck_since > a.stuck_s:
                    lab = "CHECK" if "CHECK" in labels else "FOLD"
                    rv, cmx = svc.recommendation_view(), svc.committed
                    if svc.last_frame is not None:
                        cv2.imwrite(str(self.out / "frames" / f"stuck_{len(self.interventions) + 1:02d}_hand{tr.get('hand_header')}.png"), svc.last_frame.bgr)
                    self.interventions_detail.append({"why_not": rv.get("why_not"), "blocked": list(cmx.blocked) if cmx else None,
                                                      "hero_turn": cmx.hero_turn if cmx else None, "pending": list(cmx.pending) if cmx else None,
                                                      "waits": dict(svc.desk.get("waits") or {}),
                                                      "read": committed_view(cmx) if cmx else None, "truth": truth_view(tr)})
                    done = svc.sandbox_cmd("dom_click", label=lab).get("result")
                    self.interventions.append({"t": round(time.time() - t0, 1), "hand": tr.get("hand_header"), "pressed": lab, "ok": bool(done),
                                               "why": f"hero on turn {a.stuck_s:g}s without a bot action"})
                    stuck_since = time.time()
            ex = svc.desk.get("executor")
            if ex is not None and ex.halted:
                self.halts.append({"t": round(time.time() - t0, 1), "reason": ex.halted, "after_decision": len(self.rows)})
                if len(self.halts) > a.max_halts:
                    break
                time.sleep(1.0)
                svc.resume_executor()        # recorded as an owner-review step of the harness; every gate still applies afterwards
            hands = {r["hand"] for r in self.rows if r["hand"]}
            if len(hands) > a.hands:
                break
        end_truth = self.truth_now()
        svc.stop()
        return self._report("bot", start_truth, end_truth, time.time() - t0)

    # ---------------------------------------------------------------- duel mode (owner clicks, bot advises)
    def run_duel(self) -> dict:
        a = self.args
        svc = self.svc
        svc.start_desk(self.source, desk_mode="coach", max_hands=10 ** 6, auto_deal=False)
        t0 = time.time()
        start_truth, last_key, open_rec = None, None, None
        print("Окно тренажёра открыто: играйте сами (DEAL — новая раздача). Бот только советует и записывает. Ctrl+C — стоп.", flush=True)
        try:
            while time.time() - t0 < a.max_minutes * 60:
                time.sleep(0.4)
                if svc.error:
                    break
                tr = self.truth_now()
                if tr is None:
                    continue
                if start_truth is None and tr.get("hero_cards"):
                    start_truth = tr
                hero_turn_dom = any(b["label"] == "FOLD" for b in tr.get("buttons", []))
                rv = svc.recommendation_view()
                cm0, st0 = svc.committed, svc.last_state
                synced = bool(cm0 and st0 and screen_mismatch(st0, cm0) is None)      # the same gate the clicking bot uses
                if hero_turn_dom and rv.get("ok") and open_rec is None and synced:
                    key = (tr.get("hand_header"), len(tr.get("board", [])), tr.get("pot_text"))
                    if key != last_key:
                        last_key = key
                        cm, st, fr = svc.committed, svc.last_state, svc.last_frame
                        open_rec = {"frame": fr.bgr.copy(), "boxes": list((st.quality or {}).get("boxes", [])), "cm": committed_view(cm),
                                    "rc": rv, "truth": tr, "t": time.time(), "log": list(tr.get("hand_log", []))}
                if open_rec is not None and not hero_turn_dom:
                    owner = owner_action_from_log(tr.get("hand_log", []), open_rec["log"])
                    tv = truth_view(open_rec["truth"])
                    g = grade(open_rec["cm"], tv)
                    bot = open_rec["rc"].get("action")
                    extra = [(f"Владелец сыграл: {owner or 'не распознано'};  бот советовал: {bot}", "ok" if owner == bot else "bad")]
                    img = annotate(open_rec["frame"], open_rec["boxes"], _lines(open_rec["rc"], open_rec["cm"], tv, g, extra), [])
                    n = len(self.rows) + 1
                    rel = self._save(n, tv.get("hand"), f"owner-{owner}_bot-{bot}", img)
                    self.rows.append({"n": n, "hand": tv.get("hand"), "street": "preflop" if not tv.get("board") else f"board{len(tv['board'])}",
                                      "bot": bot, "owner": owner, "agree": owner == bot, "source": open_rec["rc"].get("source"),
                                      "read": open_rec["cm"], "truth": tv, "grade": g, "image": rel})
                    with (self.out / "decisions.jsonl").open("a", encoding="utf-8") as f:
                        f.write(json.dumps(self.rows[-1], ensure_ascii=False) + "\n")
                    open_rec = None
                if len({r["hand"] for r in self.rows if r["hand"]}) > a.hands:
                    break
        except KeyboardInterrupt:
            pass
        end_truth = self.truth_now()
        svc.stop()
        return self._report("duel", start_truth, end_truth, time.time() - t0)

    # ---------------------------------------------------------------- report
    def _report(self, mode: str, start_truth, end_truth, secs: float) -> dict:
        s0 = truth_view(start_truth).get("hero_stack") if start_truth else None
        s1 = truth_view(end_truth).get("hero_stack") if end_truth else None
        bb = self.svc.desk.get("preflop_bb") if getattr(self.svc, "desk", None) else None
        hands = sorted({r["hand"] for r in self.rows if r["hand"]}, key=lambda h: int(h))
        rep = {"mode": mode, "url": self.args.url, "bootstrap": self.args.bootstrap, "seconds": round(secs, 1), "decisions": len(self.rows),
               "hands_with_decisions": len(hands), "hands": hands, "halts": self.halts, "harness_interventions": self.interventions, "harness_interventions_detail": self.interventions_detail,
               "stack_start": s0, "stack_end": s1, "net_chips": (s1 - s0) if (s0 is not None and s1 is not None) else None, "bb": bb,
               "net_bb": round((s1 - s0) / bb, 2) if (s0 is not None and s1 is not None and bb) else None,
               "reading": summarize(self.rows),
               "control_waits": dict((getattr(self.svc, "desk", None) or {}).get("waits") or {}),   # frames where the screen was ahead of the vote
               "by_source": {k: sum(1 for r in self.rows if r.get("source") == k) for k in {r.get("source") for r in self.rows}},
               "note": "stack_start/stack_end: the trainer's own numbers at the first decision / at the end (posted blinds make this ±1bb); "
                       "20 hands are far too few to measure skill — this is a functional run, not a win-rate claim."}
        if mode == "bot":
            rep["verified_decisions"] = sum(1 for r in self.rows if r.get("verified"))
        else:
            rep["agreement"] = round(sum(1 for r in self.rows if r.get("agree")) / len(self.rows), 3) if self.rows else None
        (self.out / "summary.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pokervision.play_trainer")
    ap.add_argument("mode", choices=("bot", "duel"))
    ap.add_argument("--url", default="http://127.0.0.1:3417/")
    ap.add_argument("--hands", type=int, default=20)
    ap.add_argument("--out", required=True)
    ap.add_argument("--bootstrap", default="cash_nl10", choices=("cash_nl2", "cash_nl5", "cash_nl10", "cash_nl25"))
    ap.add_argument("--headful", action="store_true", help="show the trainer window (duel mode is always headful)")
    ap.add_argument("--max-minutes", type=float, default=40.0)
    ap.add_argument("--max-halts", type=int, default=25)
    ap.add_argument("--stuck-s", type=float, default=25.0, help="bot mode: harness presses CHECK/FOLD after this long on turn without a bot action")
    a = ap.parse_args(argv)
    if a.mode == "duel":
        a.headful = True
    s = Session(a)
    rep = s.run_bot() if a.mode == "bot" else s.run_duel()
    print(json.dumps({k: rep[k] for k in rep if k not in ("reading", "hands")}, ensure_ascii=False, indent=1))
    print(json.dumps(rep["reading"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
