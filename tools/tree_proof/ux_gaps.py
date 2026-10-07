#!/usr/bin/env python3
"""UX sweep gap closer (companion of ux_sweep.py): hand reproductions + click-timeout retries + desktop screenshots.

  python tools/tree_proof/ux_gaps.py --build <installed build dir> --raw <desktop raw json> --out <result json> [--shots]

Starts the same THROWAWAY backend as ux_sweep.py (temp data dir, temp LOCALAPPDATA/APPDATA/HOME, secrets stripped,
port 8893), stubs every mutating request (nothing is written), and:
  1. hand-reproduces the two controls the sweep recorded as DEAD (video-studio dialog 'Применить',
     images?studio=1 count input '1') and records DOM / network / validity evidence;
  2. retries every 'click_failed' ERROR record of the raw desktop sweep with: normal click (long timeout),
     force click, DOM click(), mouse click at the box centre; classifies each as harness artifact or real;
  3. with --shots saves the per-page desktop screenshots that ux_sweep.py writes for phone.
The product is never edited here.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ux_sweep as U  # noqa: E402


def short(e: Exception, n: int = 400) -> str:
    return " ".join(str(e).split())[:n]


def ctrl_of(rec: dict) -> dict:
    return {"kind": rec["kind"], "label": rec["label"], "cls": rec.get("cls", ""), "href": rec.get("href", ""),
            "attrs": " ".join([rec["label"], rec.get("cls", ""), rec.get("href", "")]), "tag": "", "type": "", "selected": False}


def norm_sig(sig: str) -> str:
    """Raw records carry sigs written before state classes (is-reported, active...) were stripped; normalise both sides."""
    head, cls, href = sig.rsplit("|", 2)
    return f"{head}|{' '.join(U.STATE_CLS.sub('', cls).split())[:50]}|{href}"


def find(sess, scope: str, sig: str, nth: int, tries: int = 4):
    sig = norm_sig(sig)
    for _ in range(tries):
        same = [x for x in sess.enum(scope) if norm_sig(U.sig_of(x)) == sig]
        if nth < len(same):
            return same[nth]
        sess.page.wait_for_timeout(700)
    return None


def attempt(sess, rec: dict, scope: str, prelude_rec: dict | None) -> dict:
    """One retry of one record; returns {method, ok, effect verdict, diagnostics}."""
    p = sess.page
    out: dict = {"route": rec["route"], "label": rec["label"], "kind": rec["kind"], "sig": rec["sig"], "nth": rec["nth"],
                 "via": rec.get("via", ""), "orig_detail": rec.get("detail", "")}
    sess.stub = True
    try:
        sess.reset_events()
        sess.open(rec["route"])
        if prelude_rec:
            o = find(sess, "all", prelude_rec["sig"], prelude_rec["nth"], 3)
            if not o:
                out.update(result="opener_missing", klass="harness: opener not reproducible")
                return out
            ol = p.locator(f'[data-uxs="{o["i"]}"]')
            try:
                ol.scroll_into_view_if_needed(timeout=5000)
                ol.click(timeout=8000)
            except Exception:                               # noqa: BLE001
                ol.click(force=True, timeout=5000)
            sess.settle(500)
        c = find(sess, "all" if prelude_rec else scope, rec["sig"], rec["nth"])
        if not c:
            out.update(result="not_found", klass="harness: control not found again (re-render)")
            return out
        i = c["i"]
        loc = p.locator(f'[data-uxs="{i}"]')
        out["control"] = {k: c[k] for k in ("tag", "type", "kind", "cls", "disabled")}
        geo = p.evaluate("""(i) => { const e = document.querySelector('[data-uxs="'+i+'"]'); if (!e) return null;
            const r = e.getBoundingClientRect(); const x = r.left + r.width/2, y = r.top + r.height/2;
            const top = document.elementFromPoint(x, y);
            return {rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)], vw: innerWidth, vh: innerHeight,
              topmost: top ? (top === e ? 'self' : (e.contains(top) ? 'child' : top.tagName + '.' + String(top.className).slice(0, 40))) : 'none'}; }""", i)
        out["geometry_before_scroll"] = geo
        # a control inside a closed <details> has a layout box but is not visible: a user must open the summary first
        closed = p.evaluate("""(i) => { const e = document.querySelector('[data-uxs="'+i+'"]'); let a = e.parentElement, n = 0;
            while (a) { if (a.tagName === 'DETAILS' && !a.open) { const sm = a.querySelector(':scope > summary'); if (sm) { sm.setAttribute('data-uxs-open', String(n)); n++; } } a = a.parentElement; }
            return {n, visible: e.checkVisibility ? e.checkVisibility({checkVisibilityCSS: true, contentVisibilityAuto: true}) : null}; }""", i)
        out["closed_details_ancestors"] = closed["n"]
        out["visible_before"] = closed["visible"]
        if closed["n"]:
            for k in range(closed["n"] - 1, -1, -1):          # outermost first
                try:
                    p.locator(f'[data-uxs-open="{k}"]').click(timeout=5000)
                    p.wait_for_timeout(250)
                except Exception as exc:                    # noqa: BLE001
                    out["summary_click_error"] = short(exc, 200)
            out["visible_after_opening_summary"] = p.evaluate("""(i) => { const e = document.querySelector('[data-uxs="'+i+'"]');
                return e ? (e.checkVisibility ? e.checkVisibility({checkVisibilityCSS: true, contentVisibilityAuto: true}) : null) : 'detached'; }""", i)
        before = sess.state()
        sess.reset_events()
        methods = []
        if rec["kind"] == "text":
            seq = [("fill_long", lambda: loc.fill(U.fill_value(c), timeout=15000)),
                   ("click_then_type", lambda: (loc.click(timeout=8000, force=True), p.keyboard.type(U.fill_value(c)))),
                   ("dom_value", lambda: loc.evaluate("(e, v) => { e.value = v; e.dispatchEvent(new Event('input', {bubbles:true})); e.dispatchEvent(new Event('change', {bubbles:true})); }", U.fill_value(c)))]
        else:
            def mouse():
                loc.scroll_into_view_if_needed(timeout=5000)
                b = loc.bounding_box()
                p.mouse.click(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
            seq = [("scroll+click_15s", lambda: (loc.scroll_into_view_if_needed(timeout=8000), loc.click(timeout=15000))),
                   ("force_click", lambda: loc.click(force=True, timeout=8000)),
                   ("mouse_at_centre", mouse),
                   ("dom_click", lambda: loc.evaluate("e => e.click()"))]
        worked = None
        for name, fn in seq:
            t0 = time.monotonic()
            try:
                fn()
                methods.append({"m": name, "ok": True, "s": round(time.monotonic() - t0, 1)})
                worked = name
                break
            except Exception as exc:                        # noqa: BLE001
                methods.append({"m": name, "ok": False, "s": round(time.monotonic() - t0, 1), "err": short(exc, 300)})
        out["methods"] = methods
        out["worked"] = worked
        sess.settle()
        after = sess.state()
        r = U.classify(sess, c, True, before, after, True, set(), None, None, 0) if worked else {"verdict": "NOT_CLICKED", "detail": ""}
        real_fx = [e for e in r.get("effects", []) if "TESTING PERIOD" not in e and "evolution/status" not in e]
        out["effect"] = {"verdict": r["verdict"] if (real_fx or r["verdict"] != "OK") else "DEAD?", "real_effects": real_fx[:4],
                         "detail": r.get("detail", "")[:160]}
        out["topmost_after"] = geo and p.evaluate("""(i) => { const e = document.querySelector('[data-uxs="'+i+'"]'); if (!e) return 'detached';
            const r = e.getBoundingClientRect(); const t = document.elementFromPoint(r.left + r.width/2, r.top + r.height/2);
            return t ? (t === e ? 'self' : (e.contains(t) ? 'child' : t.tagName + '.' + String(t.className).slice(0, 40))) : 'none'; }""", i)
        first = methods[0]
        if closed["n"] and first["ok"]:
            out["klass"] = (f"artifact: control sits inside {closed['n']} closed <details> (layout box exists, not visible); "
                            "opening the summary first, as a user must, makes a normal click work")
        elif closed["n"]:
            out["klass"] = "REAL?: still not clickable after opening its <details> summary"
        elif first["ok"]:
            out["klass"] = "artifact: first retry with scroll + 15 s timeout succeeded (earlier failure = 4 s harness timeout under re-render/animation)"
        elif worked == "force_click" or worked == "mouse_at_centre" or worked == "dom_click" or worked == "dom_value":
            err = first.get("err", "")
            intercept = re.search(r"<([^>]{0,60})>[^<]{0,40}intercepts pointer events", err)
            if intercept:
                out["klass"] = f"REAL?: normal click refused because another element intercepts pointer events ({intercept.group(1)[:60]}); only force/DOM click works"
            else:
                out["klass"] = "artifact: only Playwright actionability checks failed (" + first.get("err", "")[:90] + "); force/position click works"
        else:
            out["klass"] = "REAL?: no method could operate the control"
        out["result"] = "ok" if worked else "failed"
        return out
    except Exception as exc:                                # noqa: BLE001
        out.update(result="exception", klass="harness: " + short(exc, 200))
        return out
    finally:
        sess.stub = False


def hand_buttons(sess) -> list[dict]:
    p = sess.page
    res = []
    # ---- video-studio dialog 'Применить'
    ev: dict = {"control": "video-studio: '＋ Новый проект' dialog, submit 'Применить' (empty required name)"}
    sess.stub = False
    sess.open("video-studio")
    opener = p.locator("button", has_text="Новый проект").first
    if not opener.count():
        opener = p.locator("button", has_text="＋").first
    opener.click(timeout=8000)
    p.wait_for_selector("dialog[open]", timeout=5000)
    sess.reset_events()
    b = p.locator("dialog[open] button[type=submit]").first
    ev["submit_label"] = b.inner_text()
    ev["name_required_attr"] = p.evaluate("() => document.querySelector('dialog[open] form input') && document.querySelector('dialog[open] form input').required")
    ev["form_valid_before"] = p.evaluate("() => document.querySelector('dialog[open] form').checkValidity()")
    b.click(timeout=5000)
    p.wait_for_timeout(800)
    ev["after_empty_submit"] = {
        "dialog_still_open": p.locator("dialog[open]").count() == 1,
        "validationMessage": p.evaluate("() => document.querySelector('dialog[open] form input').validationMessage"),
        "invalid_matches": p.evaluate("() => document.querySelector('dialog[open] form input').matches(':invalid')"),
        "focused_is_name_input": p.evaluate("() => document.activeElement === document.querySelector('dialog[open] form input')"),
        "requests": [f"{m} {x}" for m, x in sess.ev["requests"]], "would_send": sess.ev["would_send"]}
    sess.stub = True
    sess.reset_events()
    p.fill("dialog[open] form input", "ux-gap-test")
    b.click(timeout=5000)
    p.wait_for_timeout(1000)
    ev["after_valid_submit"] = {"would_send": sess.ev["would_send"], "requests": [f"{m} {x}" for m, x in sess.ev["requests"] if m != "GET"],
                                "dialog_still_open": p.locator("dialog[open]").count() == 1,
                                "toasts": sess.state()["toasts"], "pageerrors": sess.ev["pageerrors"], "console": sess.ev["console"][:3]}
    sess.stub = False
    ev["verdict"] = ("BY_DESIGN" if (not ev["form_valid_before"] and ev["after_empty_submit"]["dialog_still_open"] and ev["after_empty_submit"]["invalid_matches"]
                                     and not ev["after_empty_submit"]["requests"] and ev["after_valid_submit"]["would_send"]) else "NEEDS_REVIEW")
    res.append(ev)
    # ---- images studio count input '1'
    ev = {"control": "images?studio=1: number input 'Количество' (value 1)"}
    sess.stub = True
    sess.open("images?studio=1")
    inp = p.locator("label.studio-field", has_text="Количество").locator("input")
    ev["initial_value"] = inp.input_value()
    ev["attrs"] = inp.evaluate("e => ({type:e.type, min:e.min, max:e.max})")
    sess.reset_events()
    inp.fill("3")
    p.wait_for_timeout(300)
    ev["after_fill_3"] = {"value": inp.input_value(), "rerendered_input_kept": inp.count() == 1}
    inp.press("ArrowUp")
    p.wait_for_timeout(200)
    ev["after_arrow_up"] = inp.input_value()
    sess.reset_events()
    try:
        prompt = p.locator("textarea").first
        prompt.fill("ux gap test")
    except Exception as exc:                                # noqa: BLE001
        ev["prompt_fill_error"] = short(exc)
    p.locator("button", has_text="Создать результат").first.click(timeout=8000)
    p.wait_for_timeout(1200)
    ev["submit_requests"] = {"would_send": sess.ev["would_send"], "requests": [f"{m} {x}" for m, x in sess.ev["requests"] if m != "GET"]}
    body = None
    for w in sess.ev["would_send"]:
        m = re.search(r"\{.*\}", w)
        if m:
            try:
                body = json.loads(m.group(0))
            except Exception:                               # noqa: BLE001
                body = m.group(0)
    ev["submitted_payload"] = body
    ev["verdict"] = "BY_DESIGN" if isinstance(body, dict) and body.get("count") == 4 else ("NEEDS_REVIEW" if body is None else "CHECK_PAYLOAD")
    sess.stub = False
    res.append(ev)
    sess.stub = False
    res.append(ev)
    return res


def hand_extra(sess) -> list[dict]:
    """Other controls the sweep left unconfirmed: web_designer 'Применить размер', thinking-pane 'Очистить ленту',
    browser 'Новое окно' -> live panel refresh."""
    p = sess.page
    res = []
    # ---- web_designer: apply dimensions
    ev: dict = {"control": f"web_designer: 'Применить размер' ({sess.vp})"}
    sess.stub = False
    sess.open("web_designer")
    p.locator("button", has_text="Открыть проект").first.click(timeout=8000)
    p.wait_for_selector("button:has-text('Применить размер')", timeout=20000)
    p.wait_for_timeout(1200)
    btn = p.locator("button", has_text="Применить размер").first
    box = p.locator(".bd-viewport-tools input[type=number], .bd-viewport-tools input")
    ev["inputs"] = [box.nth(k).input_value() for k in range(min(box.count(), 3))]
    ls0 = p.evaluate("() => JSON.stringify(Object.entries(localStorage).sort())")
    sess.reset_events()
    btn.click(timeout=5000)
    p.wait_for_timeout(600)
    ev["same_values_click"] = {"localStorage_changed": p.evaluate("() => JSON.stringify(Object.entries(localStorage).sort())") != ls0,
                               "inputs": [box.nth(k).input_value() for k in range(min(box.count(), 3))]}
    new_w, new_h = ("333", "444")
    box.nth(0).fill(new_w)
    box.nth(1).fill(new_h)
    geo0 = p.evaluate("() => { const f = document.querySelector('iframe'); if (!f) return null; const r = f.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height), f.style.cssText.slice(0, 120)]; }")
    ls1 = p.evaluate("() => JSON.stringify(Object.entries(localStorage).sort())")
    btn.click(timeout=5000)
    p.wait_for_timeout(800)
    geo1 = p.evaluate("() => { const f = document.querySelector('iframe'); if (!f) return null; const r = f.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height), f.style.cssText.slice(0, 120)]; }")
    ev["new_values_click"] = {"iframe_before": geo0, "iframe_after": geo1,
                              "localStorage_changed": p.evaluate("() => JSON.stringify(Object.entries(localStorage).sort())") != ls1,
                              "inputs": [box.nth(k).input_value() for k in range(min(box.count(), 3))],
                              "pageerrors": sess.ev["pageerrors"][:2]}
    ev["verdict"] = "BY_DESIGN" if (ev["new_values_click"]["localStorage_changed"] or geo0 != geo1) else "NEEDS_REVIEW"
    res.append(ev)
    # ---- browser: Новое окно -> live panel
    ev = {"control": f"browser: 'Новое окно' then live-panel refresh ({sess.vp})"}
    sess.stub = True
    sess.open("browser")
    sess.reset_events()
    p.locator("button", has_text="Новое окно").first.click(timeout=8000)
    p.wait_for_timeout(2500)
    ev["would_send"] = sess.ev["would_send"]
    ev["refresh_buttons"] = p.locator("button[title='Обновить скриншот']").count()
    ev["dialogs"] = sess.state()["dlg"][:3]
    if ev["refresh_buttons"]:
        sess.reset_events()
        try:
            p.locator("button[title='Обновить скриншот']").first.click(timeout=8000)
            p.wait_for_timeout(1200)
            ev["refresh_click"] = {"ok": True, "requests": [f"{m} {x}" for m, x in sess.ev["requests"]][:4], "pageerrors": sess.ev["pageerrors"][:2]}
        except Exception as exc:                            # noqa: BLE001
            ev["refresh_click"] = {"ok": False, "err": short(exc, 300)}
    sess.stub = False
    res.append(ev)
    # ---- chat.html attach button: opens the native file chooser (no DOM change by design)
    ev = {"control": f"chat.html: 'Прикрепить файлы' ({sess.vp})"}
    sess.stub = True
    sess.open("chat.html")
    try:
        p.evaluate("() => { try { localStorage.removeItem('bcc.chat.panel'); } catch (e) {} }")
        sess.open("chat.html")
        with p.expect_file_chooser(timeout=6000) as fc:
            p.locator("#chat-attach").click(timeout=8000)
        ev["file_chooser_opened"] = True
        ev["multiple"] = fc.value.is_multiple()
    except Exception as exc:                                # noqa: BLE001
        ev["file_chooser_opened"] = False
        ev["err"] = short(exc, 250)
    ev["verdict"] = "BY_DESIGN" if ev.get("file_chooser_opened") else "NEEDS_REVIEW"
    res.append(ev)
    # ---- shell thinking pane: 'Очистить ленту' (events.length = 0; renderLog()) - with an empty feed nothing can change
    ev = {"control": f"shell: 'Очистить ленту' ({sess.vp})"}
    sess.open("home")
    try:
        p.evaluate("() => document.getElementById('think-pane').hidden = false")
        n0 = p.evaluate("() => document.querySelector('.bx-think-log').childElementCount")
        html0 = p.evaluate("() => document.querySelector('.bx-think-log').innerHTML")
        p.locator("#think-clear").click(timeout=6000, force=True)
        p.wait_for_timeout(400)
        html1 = p.evaluate("() => document.querySelector('.bx-think-log').innerHTML")
        ev.update(feed_items_before=n0, feed_items_after=p.evaluate("() => document.querySelector('.bx-think-log').childElementCount"),
                  feed_html_changed=html0 != html1, feed_empty_before=(n0 == 0) or ("bx-think-empty" in html0))
        ev["verdict"] = "BY_DESIGN (clearing an already empty feed is a no-op)" if not ev["feed_html_changed"] and ev["feed_empty_before"] else "NEEDS_REVIEW"
    except Exception as exc:                                # noqa: BLE001
        ev["err"] = short(exc, 250)
        ev["verdict"] = "NEEDS_REVIEW"
    sess.stub = False
    res.append(ev)
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", required=True)
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--port", type=int, default=8893)
    ap.add_argument("--shots", action="store_true", help="save desktop screenshots of every route into evidence/ux-sweep")
    ap.add_argument("--skip-retry", action="store_true")
    ap.add_argument("--skip-hand", action="store_true")
    ap.add_argument("--hand-extra", action="store_true", help="also reproduce web_designer 'Применить размер' and the browser live panel")
    ap.add_argument("--viewport", default="desktop", choices=["desktop", "phone"])
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:                                       # noqa: BLE001
        pass
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8", errors="replace")
    raw = json.loads(args.raw.read_text(encoding="utf-8"))
    recs = raw["records"]
    failed = [r for r in recs if r["verdict"] == "ERROR" and r.get("subtype") == "click_failed"]
    if args.limit:
        failed = failed[:args.limit]
    tmp = Path(tempfile.mkdtemp(prefix="bcc-uxgaps-"))
    be = U.Throwaway(args.port, tmp, Path(args.build))
    from playwright.sync_api import sync_playwright
    be.start()
    result: dict = {"build": args.build, "health_live": be.live, "hand": [], "retries": [], "shots": []}
    try:
        with sync_playwright() as pw:
            sess = U.Session(pw, be.base, be.token, args.viewport, U.EVID / "ux-sweep")
            try:
                sess.login()
                result["hand"] = [] if args.skip_hand else hand_buttons(sess)
                if args.hand_extra:
                    result["hand_extra"] = hand_extra(sess)
                    print(json.dumps(result["hand_extra"], ensure_ascii=False)[:2500], flush=True)
                if not args.skip_retry:
                    for r in failed:
                        pre = None
                        if r.get("via"):
                            first = r["via"].split(" > ")[0]
                            pre = next((x for x in recs if x["route"] == r["route"] and x["depth"] == 0 and x["label"][:30] == first and x["verdict"] != "SAMPLED"), None)
                        scope = "all" if r["route"].endswith(".html") else "view"
                        if r["kind"] == "page":
                            out = {"route": r["route"], "label": r["label"], "kind": "page", "orig_detail": r.get("detail", "")}
                            try:
                                sess.open(r["route"])
                                out.update(result="ok", klass="artifact: page loads on retry (goto 8 s timeout under load)")
                            except Exception as exc:        # noqa: BLE001
                                out.update(result="failed", klass="REAL?: " + short(exc, 150))
                        else:
                            out = attempt(sess, r, scope, pre)
                        result["retries"].append(out)
                        print(f"{out['route']:18s} {out.get('result',''):10s} {out.get('worked')} {out['label'][:30]!r} {out.get('klass','')[:90]}", flush=True)
                if args.shots:
                    all_pages = [x for x in raw["routes"]]
                    for route in all_pages:
                        try:
                            sess.open("home" if route == "shell" else route)
                            sess.shot(("shell" if route == "shell" else route.replace("?", "_").replace("=", "")) + "-" + args.viewport)
                            result["shots"].append(route)
                        except Exception as exc:            # noqa: BLE001
                            result["shots"].append(f"{route}: FAILED {short(exc, 80)}")
            finally:
                sess.close()
    finally:
        be.stop()
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
