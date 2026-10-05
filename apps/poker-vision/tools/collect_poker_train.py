"""Collect labelled frames from the owner's own Poker Train served on loopback.

Frames are real browser screenshots; labels come from the page DOM (tools/truth.js), taken immediately
before and after each screenshot. A frame whose before/after DOM disagree is marked ``stable=false``.
The vision pipeline never reads the DOM. Loopback only; refuses any other host.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
TRUTH_JS = (Path(__file__).with_name("truth.js")).read_text(encoding="utf-8")

FORMATS = {
    "NL2": ("cash", "NL2 Cash"), "NL5": ("cash", "NL5 Cash"), "NL10": ("cash", "NL10 Cash"), "NL25": ("cash", "NL25 Cash"),
    "WSOPMain": ("tour", "WSOP Main Event"), "WSOPDaily": ("tour", "WSOP Daily"), "EPT": ("tour", "EPT Main Event"),
    "WPT": ("tour", "WPT $500"), "Turbo": ("tour", "Turbo Shot-Clock"), "K100": ("tour", "$100K Guaranteed"),
}


def assert_loopback(url: str) -> None:
    u = urlparse(url)
    if u.scheme not in ("http", "https") or (u.hostname or "") not in LOOPBACK or u.username or u.password:
        raise SystemExit(f"refused: {url!r} is not loopback")


def open_table(pg, fmt: str) -> None:
    kind, label = FORMATS[fmt]
    pg.wait_for_timeout(400)
    pg.click("text=+"); pg.fill("input", "Hero"); pg.keyboard.press("Enter"); pg.wait_for_timeout(300)
    pg.click("text=Hero"); pg.wait_for_timeout(500)
    if kind == "cash":
        pg.click(f"text={label.split(' ')[0]} ({'1c/2c' if fmt=='NL2' else '2c/5c' if fmt=='NL5' else '5c/10c' if fmt=='NL10' else '10c/25c'})")
        pg.wait_for_timeout(1500)
    else:
        pg.click("text=START"); pg.wait_for_timeout(400)
        pg.click(f"text={label}"); pg.wait_for_timeout(300)
        pg.click("text=REGISTER NOW"); pg.wait_for_timeout(2500)


def stable_pair(a: dict, b: dict) -> bool:
    keys = ("pot_text", "to_call_text", "hero_stack_text", "position", "raise_panel")
    same = all(a.get(k) == b.get(k) for k in keys)
    same = same and [c["card"] for c in a["hero_cards"]] == [c["card"] for c in b["hero_cards"]]
    same = same and [c["card"] for c in a["board"]] == [c["card"] for c in b["board"]]
    same = same and sorted(s["text"] for s in a["seats"]) == sorted(s["text"] for s in b["seats"])
    same = same and sorted(s["text"] for s in a["bets"]) == sorted(s["text"] for s in b["bets"])
    return same


def run(args) -> int:
    from playwright.sync_api import sync_playwright
    assert_loopback(args.url)
    out = Path(args.out) / args.session
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    rows = []
    t0 = time.monotonic()
    hand_idx = -1
    frame_n = 0
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME)
        ctx = b.new_context(viewport={"width": args.vw, "height": args.vh}, device_scale_factor=args.dpr)
        # guard: the page may only talk to loopback
        ctx.route("**/*", lambda route: route.continue_() if (urlparse(route.request.url).hostname in LOOPBACK or route.request.url.startswith(("data:", "blob:"))) else route.abort())
        pg = ctx.new_page()
        pg.goto(args.url)
        open_table(pg, args.fmt)

        def snap(tag: str) -> None:
            nonlocal frame_n
            a = pg.evaluate(TRUTH_JS)
            png = pg.screenshot()
            bb = pg.evaluate(TRUTH_JS)
            name = f"{frame_n:04d}.png"
            (out / name).write_bytes(png)
            rows.append({"session": args.session, "fmt": args.fmt, "vw": args.vw, "vh": args.vh, "dpr": args.dpr, "frame": name,
                         "t_ms": int((time.monotonic() - t0) * 1000), "hand_idx": hand_idx, "tag": tag,
                         "stable": stable_pair(a, bb), "truth": bb, "truth_before": {"animating": a["animating"]}})
            frame_n += 1

        steps = 0
        while hand_idx + 1 < args.hands + (0) and steps < args.hands * 14:
            steps += 1
            btns = {x.inner_text().strip().split("\n")[0]: x for x in pg.query_selector_all("button")}
            if "DEAL" in btns:
                if hand_idx >= 0:
                    snap("hand_over")
                if hand_idx + 1 >= args.hands:
                    break
                hand_idx += 1
                btns["DEAL"].click()
                pg.wait_for_timeout(120); snap("after_deal_anim")
                pg.wait_for_timeout(1600); snap("deal_settled")
                continue
            if hand_idx < 0:
                pg.wait_for_timeout(300); continue
            if "VIEW RESULTS" in btns:
                break
            if "CONFIRM" in btns:  # raise panel open
                snap("raise_panel")
                if rng.random() < 0.6:
                    btns["CONFIRM"].click()
                else:
                    btns["FOLD"].click()
                pg.wait_for_timeout(150); snap("after_click_anim")
                pg.wait_for_timeout(1500); snap("settled")
                continue
            r = rng.random()
            pick = None
            tr = pg.evaluate(TRUTH_JS)
            stack_txt = (tr.get("hero_stack_text") or "0").replace(",", "")
            try:
                hero_stack = float(stack_txt[:-1]) * (1e3 if stack_txt.endswith("K") else 1e6) if stack_txt[-1:] in "KM" else float(stack_txt)
            except ValueError:
                hero_stack = 0.0
            call_amt = next((b["amount"] for b in tr["buttons"] if b["label"] in ("CALL", "ALL IN") and b["amount"]), 0) or 0
            if hero_stack and call_amt > 0.25 * hero_stack and "FOLD" in btns:
                snap("decision"); btns["FOLD"].click(); pg.wait_for_timeout(150); snap("after_click_anim"); pg.wait_for_timeout(1400); snap("settled"); continue
            if r < 0.12 and "FOLD" in btns: pick = "FOLD"
            elif r < 0.30 and "RAISE" in btns: pick = "RAISE"
            elif "CHECK" in btns: pick = "CHECK"
            elif "CALL" in btns: pick = "CALL"
            else:
                pg.wait_for_timeout(500); continue
            snap("decision")
            btns[pick].click()
            pg.wait_for_timeout(150); snap("after_click_anim")
            pg.wait_for_timeout(1400); snap("settled")
        b.close()
    with (out / "labels.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{args.session}: {len(rows)} frames, {hand_idx + 1} hands")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:3000/")
    ap.add_argument("--out", required=True)
    ap.add_argument("--session", required=True)
    ap.add_argument("--fmt", choices=sorted(FORMATS), default="NL10")
    ap.add_argument("--hands", type=int, default=8)
    ap.add_argument("--vw", type=int, default=520)
    ap.add_argument("--vh", type=int, default=900)
    ap.add_argument("--dpr", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=1)
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
