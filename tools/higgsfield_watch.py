"""Higgsfield Extend-chain watcher v2 — BOSSMAN video factory.

Автономная цепочка Extend-сегментов Seedance 2.5 (Unlimited mode).

Детекция генерации v2 (уроки 14:26-14:38):
  - DOM-слово "Processing" в Extend-режиме НЕ появляется — детектим по сетевому
    ответу fnf-api-gw.higgsfield.ai/fnf/jobs/v2/seedance_2_5 (job создан, queued)
  - новое видео = cloudfront URL с hf_YYYYMMDD_HHMMSS > watermark последнего
    сегмента (защита от скачивания старой истории)
  - после ввода промта читаем редактор обратно; если не заменился —
    fallback через document.execCommand('insertText')

Цикл: дождаться нового сегмента -> скачать segNN.mp4 -> ffprobe >=5s ->
промт следующей сцены -> Generate -> повтор до --segments N.

Лог:  artifacts/higgsfield/log.txt     Состояние: artifacts/higgsfield/state.json
Воркфлоу/уроки: artifacts/higgsfield/WORKFLOW.md
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "artifacts" / "higgsfield"
VIDS = BASE / "videos"
STATE = BASE / "state.json"
CDP = "http://127.0.0.1:9222"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = r"C:\Users\timur\BossmanEdgeCDP"

SCENE_PROMPTS = [
    "камера за деревом: @Image 2 крадётся на цыпочках вдоль кустов потирает руки и хихикает, "
    "@Image 3 откладывает стакан и оглядывается по сторонам ничего не замечает",
    "@Image 3 делает последний глоток кофе, @Image 2 выглядывает из-за дерева "
    "делает хитрое лицо и жестами показывает план зажимая рот ладонью от смеха",
    "@Image 3 ставит стакан на стол тянется к телефону в кармане, камера за деревом: "
    "@Image 2 замирает и прячется обратно за ствол только плечо и ухмылка видны",
    "@Image 3 встаёт со стула и уходит с терассы, @Image 2 выходит из-за дерева торжествующе "
    "поднимает кулак делает победный танец подпрыгивая",
    "@Image 2 садится за освободившийся столик наливает себе кофе из чужой чашки "
    "делает хитрое лицо хихикает и смотрит прямо в камеру большой палец вверх",
    "@Image 3 возвращается на терассу с недоумённым лицом садится за столик "
    "наливает кофе из своей чашки, @Image 2 прячется за деревом хихикает",
    "@Image 3 пьёт кофе читает новости на телефоне, камера из-за дерева: "
    "@Image 2 выглядывает из кустов делает хитрое лицо жестами показывает план сработал",
    "@Image 3 откидывается на спинку стула с довольным лицом и зевает, "
    "@Image 2 выглядывает из-за дерева пожимает плечами и уходит в тень камера следует за ним",
]

POLL_SEC = 20
LOG_EVERY = 120
SEG_TIMEOUT = 1500


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        print(line)
    except Exception:
        pass
    BASE.mkdir(parents=True, exist_ok=True)
    with open(BASE / "log.txt", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8-sig"))
    return {"segments": [], "next_idx": 1}


def save_state(st: dict) -> None:
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def hf_ts(url: str) -> str:
    m = re.search(r"hf_(\d{8}_\d{6})", url)
    return m.group(1) if m else ""


def ffprobe(path: Path) -> float | None:
    import shutil
    import subprocess
    ff = shutil.which("ffprobe")
    if not ff:
        return None
    try:
        out = subprocess.run(
            [ff, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=30).stdout.strip()
        return float(out)
    except Exception:
        return None


def ensure_edge() -> None:
    import socket
    s = socket.socket(); s.settimeout(1)
    up = s.connect_ex(("127.0.0.1", 9222)) == 0
    s.close()
    if not up:
        subprocess.Popen([EDGE, "--remote-debugging-port=9222", f"--user-data-dir={PROFILE}",
                          "--no-first-run", "--no-default-browser-check",
                          "--window-size=1440,900", "--hide-crash-restore-bubble"])
        time.sleep(6)
        log("cdp: edge launched")


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


async def gen_srcs(page) -> list[str]:
    srcs = await page.evaluate(
        "Array.from(document.querySelectorAll('video')).map(v => v.currentSrc || v.src).filter(Boolean)")
    gen = [u for u in srcs if "cloudfront.net/user_" in u]
    return sorted(set(gen), key=hf_ts, reverse=True)


async def wait_for_new_segment(page, st) -> str | None:
    """Поллит историю до появления cloudfront-URL новее watermark. None = таймаут."""
    watermark = max((hf_ts(s["url"]) for s in st["segments"]), default="")
    deadline = time.time() + SEG_TIMEOUT
    last_log = time.time()
    while time.time() < deadline:
        srcs = await gen_srcs(page)
        new = [u for u in srcs if hf_ts(u) > watermark]
        if new:
            log(f"new segment detected: {new[0].split('/')[-1]}")
            return new[0]
        if time.time() - last_log >= LOG_EVERY:
            log(f"waiting generation... watermark={watermark} elapsed={int(time.time()+0-(deadline-SEG_TIMEOUT))}s")
            last_log = time.time()
        await page.wait_for_timeout(POLL_SEC * 1000)
    return None


def download(url: str, out: Path) -> int:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = urllib.request.urlopen(req, timeout=180).read()
    out.write_bytes(data)
    return len(data)


async def set_prompt(page, prompt: str) -> bool:
    """Переставить промт в Lexical-редакторе с ЧИПАМИ-упоминаниями.

    Урок 14:57: keyboard.type/execCommand редактор не берут; @упоминания
    вставляются только через родной дропдаун: typed '@' -> ждём меню ->
    typed search -> клик по пункту ('Image 2'/'Image 3') -> чип.
    Проверка: data-beautiful-mention значения в DOM + отсутствие мусора '@i'.
    """
    ed = page.locator("div[contenteditable=true]").first
    if not await ed.count():
        return False

    async def chips():
        return await page.evaluate(
            "Array.from(document.querySelectorAll('[data-beautiful-mention]'))"
            ".map(m => m.getAttribute('data-beautiful-mention'))")

    async def text():
        return await page.evaluate(
            "document.querySelector('div[contenteditable=true]').innerText.replace(/\\n/g,' ')")

    async def type_token(tok: str) -> None:
        await page.keyboard.type("@", delay=60)
        # ждём открытия дропдауна
        for _ in range(12):
            if await page.locator("[data-radix-popper-content-wrapper]").count():
                break
            await page.wait_for_timeout(250)
        num = tok[-1]
        await page.keyboard.type(f"image_{num}", delay=40)
        await page.wait_for_timeout(500)
        itm = page.locator(
            "[role=menuitem], [role=option], [cmdk-item], "
            "[data-radix-popper-content-wrapper] button").filter(
            has_text=re.compile(rf"image\s*{num}", re.I)).first
        if await itm.count():
            await itm.click(timeout=5000)
        else:
            await page.keyboard.press("Enter")
        await page.wait_for_timeout(500)

    for attempt in (1, 2):
        await ed.click(position={"x": 8, "y": 8})
        await page.wait_for_timeout(300)
        await page.keyboard.press("Control+A")
        await page.wait_for_timeout(200)
        await page.keyboard.press("Backspace")
        await page.wait_for_timeout(500)
        parts = re.split(r"(@image_[23])", prompt)
        for part in parts:
            if part in ("@image_2", "@image_3"):
                await type_token(part)
            elif part:
                await page.keyboard.type(part, delay=8)
        await page.wait_for_timeout(800)
        c = await chips()
        t = await text()
        want = set(re.findall(r"@image_[23]", prompt))
        stray = re.search(r"@[a-z_0-9]{1,8}(?=\s*@(Image|audio))", t)
        ok = want.issubset(set(c)) and "audio_1" not in c and not stray
        log(f"set_prompt attempt {attempt}: chips={c} stray={bool(stray)} ok={ok}")
        if ok:
            return True
    return False


async def extend_generate(page, scene_idx: int) -> bool:
    """Промт сцены + Generate. True = job создан (по сетевому ответу)."""
    sw = page.locator("button[role='switch'], [role='switch']").first
    if await sw.count() and await sw.get_attribute("aria-checked") == "false":
        await sw.click(timeout=5000)
        log("unlimited ON")

    ext = page.locator("button[role=radio]").filter(has_text="Extend Video").first
    if await ext.count() and await ext.get_attribute("aria-checked") != "true":
        await ext.click(timeout=8000, force=True)
        await page.wait_for_timeout(2000)
        log("extend tab clicked")

    # источник extend должен быть ПОСЛЕДНЕЕ видео (hf_ts == watermark)
    watermark = max((hf_ts(s["url"]) for s in load_state()["segments"]), default="")
    slot_srcs = await page.evaluate(
        "Array.from(document.querySelectorAll('video, img'))"
        ".map(e => e.currentSrc || e.src || '').filter(s => s.includes('hf_'))")
    slot_ts = max((hf_ts(s) for s in slot_srcs), default="")
    if watermark and slot_ts and slot_ts < watermark:
        log(f"extend slot STALE ({slot_ts} < {watermark}) — переchoose через пикер")
        add_btn = page.get_by_text("Add video to extend", exact=False).first
        if not await add_btn.count():
            log("слот занят старым видео; слоты замена не реализована — человека в помощь")
        else:
            await add_btn.click(timeout=8000)
            await page.wait_for_timeout(2000)
    elif slot_ts:
        log(f"extend slot ok: {slot_ts}")

    # пикер: слот пустой -> выбрать последнее из Video Generations
    add_btn = page.get_by_text("Add video to extend", exact=False).first
    if await add_btn.count():
        log("extend slot пустой — открываю пикер")
        await add_btn.click(timeout=8000)
        await page.wait_for_timeout(2500)
        vg = page.get_by_text("Video Generations", exact=False).first
        if await vg.count():
            await vg.click(timeout=8000)
            await page.wait_for_timeout(2500)
        tiles = page.locator("video")
        if await tiles.count():
            tile = tiles.first.locator("xpath=ancestor::div[3]")
            try:
                await tile.click(timeout=5000, force=True)
            except Exception:
                await tiles.first.click(timeout=5000, force=True)
            await page.wait_for_timeout(2000)
            log("extend source selected (latest)")
        else:
            log("FATAL picker: нет плиток Video Generations")
            return False

    prompt = SCENE_PROMPTS[scene_idx % len(SCENE_PROMPTS)]
    if not await set_prompt(page, prompt):
        log(f"FATAL: промт не зафиксировался после 2 попыток (scene {scene_idx}) — "
            "Generate НЕ нажимаю, чтобы не уйти старый/кривой промт")
        await page.screenshot(path=str(BASE / f"prompt_fail_{scene_idx}.png"))
        return False
    log(f"prompt set (scene {scene_idx}): {prompt[:60]}...")

    # клик + сетевое подтверждение job
    job_created = asyncio.Event()
    job_info = {}

    def on_response(r):
        if "fnf-api-gw.higgsfield.ai" in r.url and "/jobs/" in r.url and r.request.method == "POST":
            job_info["url"] = r.url
            job_info["status"] = r.status
            job_created.set()

    page.on("response", on_response)
    try:
        gen = page.locator("button[type=submit]").first
        await gen.click(timeout=10000, force=True)
        log(f"generate clicked (scene {scene_idx})")
        try:
            await asyncio.wait_for(job_created.wait(), timeout=20)
        except asyncio.TimeoutError:
            await page.screenshot(path=str(BASE / f"gen_fail_{scene_idx}.png"))
            log("job НЕ создан за 20с (скрин gen_fail) — ретрай")
            await gen.click(timeout=10000, force=True)
            try:
                await asyncio.wait_for(job_created.wait(), timeout=20)
            except asyncio.TimeoutError:
                log("FATAL: job не создан за 2 попытки")
                return False
        log(f"job CONFIRMED: HTTP {job_info.get('status')} {job_info.get('url','')[:90]}")
        return True
    finally:
        page.remove_listener("response", on_response)


async def run(target: int) -> None:
    ensure_edge()
    log(f"watcher v3 start: target={target}")
    pw, browser, page = await get_page()
    try:
        while True:
            st = load_state()
            if len(st["segments"]) >= target:
                log(f"target reached: {len(st['segments'])} segments")
                return
            # 1) сразу запускаем следующую генерацию (скорость важнее порядка)
            ok = await extend_generate(page, len(st["segments"]))
            if not ok:
                log("watcher остановлен: не смог запустить генерацию")
                return
            # 2) ждём её результат и качаем
            url = await wait_for_new_segment(page, st)
            if not url:
                log(f"FATAL: сегмент {st['next_idx']} не появился за {SEG_TIMEOUT}s")
                return
            idx = st["next_idx"]
            VIDS.mkdir(parents=True, exist_ok=True)
            out = VIDS / f"seg{idx:02d}.mp4"
            try:
                kb = download(url, out) // 1024
            except Exception as e:
                log(f"download FAILED seg{idx:02d}: {str(e)[:120]}")
                await asyncio.sleep(30)
                continue
            dur = ffprobe(out)
            if dur is None or dur < 5:
                log(f"seg{idx:02d} REJECTED: probe={dur}")
                st["rejected"] = st.get("rejected", []) + [url]
                save_state(st)
                continue
            st = load_state()
            st["segments"].append({"idx": idx, "file": out.name, "url": url, "kb": kb,
                                   "dur": dur, "time": datetime.now().isoformat(timespec="seconds")})
            st["next_idx"] = idx + 1
            save_state(st)
            log(f"downloaded seg{idx:02d}.mp4 {kb}KB dur={dur} total={len(st['segments'])}")
    finally:
        await pw.stop()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    t = 6
    if "--segments" in sys.argv:
        t = int(sys.argv[sys.argv.index("--segments") + 1])
    asyncio.run(run(t))
