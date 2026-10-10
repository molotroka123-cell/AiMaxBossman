import sys, time
from playwright.sync_api import sync_playwright

OUT = r"C:\Users\asd\Bossman\main\.claude\worktrees\agent-a544b3f69558ee242\_scratch"
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 430, "height": 900})
    pg.goto("http://127.0.0.1:8935/")
    time.sleep(1.5)
    pg.locator("input").first.fill("BotLab")
    pg.get_by_role("button", name="+").click()
    time.sleep(1.0)
    pg.get_by_role("button", name="BotLab").click()
    time.sleep(1.5)
    pg.screenshot(path=OUT + r"\s2.png", full_page=True)
    print(pg.inner_text("body")[:3000])
    pg.get_by_text("NL10 (5c/10c)", exact=True).click()
    time.sleep(2)
    pg.screenshot(path=OUT + r"\s3.png")
    print("=====TABLE\n", pg.inner_text("body")[:3000])
    print("-----BUTTONS", [x.inner_text() for x in pg.locator("button").all()][:40])
    pg.get_by_role("button", name="DEAL", exact=True).click()
    for i in range(10):
        time.sleep(1)
        btns = [x.inner_text() for x in pg.locator("button").all()]
        print(i, btns)
        if any("FOLD" in t for t in btns):
            break
    pg.screenshot(path=OUT + r"\s4.png")
    print("=====HAND\n", pg.inner_text("body")[:3000])
    html = pg.content()
    open(OUT + r"\hand.html", "w", encoding="utf-8").write(html)
    b.close()
