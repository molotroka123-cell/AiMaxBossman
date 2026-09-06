"""Higgsfield Unlimited — BOSSMAN browser automation (Seedance 2.5 / Extend chain).

Работает в живом Edge с CDP-портом (профиль-копия с логинами).

Команды:
  ensure                       - поднять Edge с CDP, если 9222 не слушается; проверить логин
  status                       - логин, Unlimited, последние видео в истории
  generate [--extend] [--prompt "..."] - нажать Generate (опц. вкладка Extend Video + промт)
  poll [--wait]                - дождаться конца Processing, скачать новое видео, лог
  chain [--segments N]         - цикл: extend-промты до N сегментов по 10s, скачивание+проверка каждого

Состояние: artifacts/higgsfield/state.json
Логи:      artifacts/higgsfield/log.txt  (append, то что читает локальная модель)
Видео:     artifacts/higgsfield/videos/segNN.mp4
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "artifacts" / "higgsfield"
VIDS = BASE / "videos"
STATE = BASE / "state.json"
LOG = BASE / "log.txt"
CDP = "http://127.0.0.1:9222"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = r"C:\Users\timur\BossmanEdgeCDP"

EXTEND_PROMPTS = [
    "@Image 3 откладывает стакан и оглядывается по сторонам, ничего не замечает, "
    "камера из-за дерева: @Image 2 крадётся на цыпочках вдоль кустов, потирает руки и хихикает",
    "@Image 3 поднимает стакан делает последний глоток, @Image 2 выглядывает из-за дерева "
    "делает хитрое лицо и жестами показывает план, зажимая рот ладонью от смеха",
    "@Image 3 ставит стакан на стол тянется к телефону в кармане, камера за деревом: "
    "@Image 2 замирает и прячется обратно за ствол, только плечо и ухмылка видны",
    "@Image 3 встаёт со стула и уходит с терассы, @Image 2 выходит из-за дерева торжествующе "
    "поднимает кулак делает победный танец подпрыгивая",
    "@Image 2 садится за освободившийся столик наливает себе кофе из чужой чашки, "
    "делает хитрое лицо хихикает и смотрит прямо в камеру из-за дерева, большой палец вверх",
]


def log(msg: str) -> None:
    BASE.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"segments": [], "next_idx": 1}


def save_state(st: dict) -> None:
    BASE.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


async def get_page():
    from playwright.async_api import async_playwright
    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp(CDP)
    ctx = browser.contexts[0]
    page = next((p for p in ctx.pages if "higgsfield.ai" in p.url), None)
    if not page:
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto("https://higgsfield.ai/ai/video", wait_until="domcontentloaded", timeout=60000)
    return pw, browser, page


async def accept_cookies(page) -> None:
    try:
        btn = page.get_by_text("Принять все", exact=True).first
        if await btn.count():
            await btn.click(timeout=4000)
            await page.wait_for_timeout(600)
            log("cookies accepted")
    except Exception as e:
        log(f"cookies: skip ({str(e)[:80]})")


async def unlim_on(page) -> bool:
    try:
        sw = page.locator("button[role='switch'], [role='switch']").first
        if await sw.count() and await sw.get_attribute("aria-checked") == "false":
            await sw.click(timeout=5000)
            await page.wait_for_timeout(600)
            log("unlimited mode ON")
            return True
        return False
    except Exception as e:
        log(f"unlim toggle error: {str(e)[:100]}")
        return False


async def logged_in(page) -> bool:
    body = await page.evaluate("document.body.innerText.slice(0, 3000)")
    return "Login" not in body.split("Enterprise")[0] and "Create Video" in body


async def history_srcs(page) -> list[str]:
    """URL генераций с CDN, отсортированные: самые свежие первыми.

    Генерации живут на cloudfront /user_*/hf_YYYYMMDD_HHMMSS_uuid.mp4.
    Баннеры static.higgsfield.ai и прочий мусор не считаем."""
    srcs = await page.evaluate(
        "Array.from(document.querySelectorAll('video')).map(v => v.currentSrc || v.src).filter(Boolean)"
    )
    gen = [u for u in srcs if "cloudfront.net/user_" in u]
    def key(u: str):
        m = re.search(r"hf_(\d{8}_\d{6})", u)
        return m.group(1) if m else ""
    return sorted(set(gen), key=key, reverse=True)


def ffprobe(path: Path) -> float | None:
    ff = shutil.which("ffprobe")
    if not ff:
        return None
    try:
        out = subprocess.run(
            [ff, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        return float(out)
    except Exception:
        return None


async def download_video(page, url: str, out: Path) -> bool:
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=120).read()
        if len(data) > 100_000:
            out.write_bytes(data)
            return True
    except Exception:
        pass
    try:
        b64 = await page.evaluate(
            """async (u) => {
                 const r = await fetch(u);
                 if (!r.ok) return '';
                 const b = await r.arrayBuffer();
                 let s = ''; const bytes = new Uint8Array(b); const chunk = 0x8000;
                 for (let i = 0; i < bytes.length; i += chunk)
                   s += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
                 return btoa(s);
               }""", url)
        if b64 and len(b64) > 100_000:
            out.write_bytes(base64.b64decode(b64))
            return True
    except Exception as e:
        log(f"in-page fetch failed: {str(e)[:100]}")
    return False


async def cmd_status() -> None:
    pw, browser, page = await get_page()
    try:
        await accept_cookies(page)
        ok = await logged_in(page)
        log(f"login={'yes' if ok else 'NO'} url={page.url}")
        sw = page.locator("button[role='switch'], [role='switch']").first
        if await sw.count():
            log(f"unlimited={await sw.get_attribute('aria-checked')}")
        srcs = await history_srcs(page)
        log(f"history videos on page: {len(srcs)}")
        processing = await page.get_by_text("Processing", exact=False).count()
        log(f"processing indicators: {processing}")
    finally:
        await pw.stop()


async def cmd_generate(extend: bool, prompt: str | None) -> None:
    pw, browser, page = await get_page()
    try:
        await accept_cookies(page)
        if not await logged_in(page):
            log("FATAL: not logged in — открыть Edge-копию и залогиниться руками")
            sys.exit(2)
        unlim_on(page)
        if extend:
            tab = page.get_by_text("Extend Video", exact=True).first
            await tab.click(timeout=10000)
            await page.wait_for_timeout(2000)
            log("extend tab opened")
        if prompt:
            ta = page.locator("textarea").first
            await ta.click()
            await ta.fill(prompt)
            log(f"prompt set: {prompt[:80]}...")
        gen = page.get_by_text("Generate", exact=False).first
        await gen.click(timeout=10000)
        await page.wait_for_timeout(2500)
        proc = await page.get_by_text("Processing", exact=False).count()
        log(f"generate clicked, processing={proc}")
        if not proc:
            await page.screenshot(path=str(BASE / "gen_warn.png"))
            log("WARN: Processing не появился — см. artifacts/higgsfield/gen_warn.png")
    finally:
        await pw.stop()


async def cmd_poll(wait: bool) -> None:
    pw, browser, page = await get_page()
    try:
        deadline = time.time() + (900 if wait else 15)
        while True:
            proc = await page.get_by_text("Processing", exact=False).count()
            if not proc:
                break
            if time.time() > deadline:
                log(f"poll: still processing, deadline hit")
                return
            await page.wait_for_timeout(15000)
        await page.wait_for_timeout(4000)
        st = load_state()
        known = {s.get("url") for s in st["segments"]}
        srcs = await history_srcs(page)
        log(f"poll: cdn videos on page={len(srcs)}")
        new = [u for u in srcs if u not in known]
        if not new:
            log("poll: nothing new")
            return
        idx = st["next_idx"]
        VIDS.mkdir(parents=True, exist_ok=True)
        out = VIDS / f"seg{idx:02d}.mp4"
        ok = await download_video(page, new[0], out)
        if not ok:
            log(f"poll: download FAILED for {new[0][:120]}")
            return
        dur = ffprobe(out)
        size_kb = out.stat().st_size // 1024
        seg = {"idx": idx, "file": out.name, "url": new[0], "time": datetime.now().isoformat(timespec="seconds"),
               "kb": size_kb, "dur": dur}
        st["segments"].append(seg)
        st["next_idx"] = idx + 1
        save_state(st)
        log(f"downloaded seg{idx:02d}.mp4 {size_kb}KB dur={dur} url={new[0][:100]}")
        total = sum(s["dur"] or 0 for s in st["segments"])
        log(f"total segments={len(st['segments'])} approx_total_dur={total:.1f}s")
    finally:
        await pw.stop()


async def cmd_chain(n: int) -> None:
    log(f"chain start: target total segments = {n}")
    while True:
        st = load_state()
        if len(st["segments"]) >= n:
            log("chain: target reached")
            return
        await cmd_poll(wait=False)
        st = load_state()
        if len(st["segments"]) >= n:
            log("chain: target reached")
            return
        i = len(st["segments"])
        prompt = EXTEND_PROMPTS[min(i, len(EXTEND_PROMPTS) - 1)] if i else None
        await cmd_generate(extend=i > 0, prompt=prompt)
        await cmd_poll(wait=True)


def ensure_edge() -> None:
    import socket
    s = socket.socket()
    s.settimeout(1)
    up = s.connect_ex(("127.0.0.1", 9222)) == 0
    s.close()
    if up:
        log("cdp: 9222 already up")
        return
    subprocess.Popen([
        EDGE, f"--remote-debugging-port=9222", f"--user-data-dir={PROFILE}",
        "--no-first-run", "--no-default-browser-check", "--window-size=1440,900",
        "--hide-crash-restore-bubble",
    ])
    time.sleep(6)
    log("cdp: edge launched with 9222")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["ensure", "status", "generate", "poll", "chain"])
    ap.add_argument("--extend", action="store_true")
    ap.add_argument("--prompt")
    ap.add_argument("--wait", action="store_true")
    ap.add_argument("--segments", type=int, default=6)
    a = ap.parse_args()
    if a.cmd == "ensure":
        ensure_edge()
        asyncio.run(cmd_status())
        return
    ensure_edge()
    fn = {"status": cmd_status, "generate": lambda: cmd_generate(a.extend, a.prompt),
          "poll": lambda: cmd_poll(a.wait), "chain": lambda: cmd_chain(a.segments)}[a.cmd]
    asyncio.run(fn())


if __name__ == "__main__":
    main()
