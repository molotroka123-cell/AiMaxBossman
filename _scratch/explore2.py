import json, time
from playwright.sync_api import sync_playwright

OUT = r"C:\Users\asd\Bossman\main\.claude\worktrees\agent-a544b3f69558ee242\_scratch"
READ_JS = open(OUT + r"\read.js", encoding="utf-8").read()

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 430, "height": 900})
    pg.goto("http://127.0.0.1:8935/")
    time.sleep(1.0)
    pg.locator("input").first.fill("BotLab")
    pg.get_by_role("button", name="+").click()
    time.sleep(0.5)
    pg.get_by_role("button", name="BotLab").click()
    time.sleep(1.0)
    pg.get_by_text("NL10 (5c/10c)", exact=True).click()
    time.sleep(1.5)
    acted = 0
    for step in range(200):
        fold = pg.get_by_role("button", name="FOLD", exact=True)
        deal = pg.get_by_role("button", name="DEAL", exact=True)
        if fold.count() and fold.is_visible():
            time.sleep(0.8)
            st = pg.evaluate(READ_JS)
            print("STATE", json.dumps(st, ensure_ascii=False))
            pg.screenshot(path=OUT + rf"\d{acted}.png")
            acted += 1
            if acted == 2:
                pg.get_by_role("button", name="RAISE", exact=True).click()
                time.sleep(0.5)
                pg.screenshot(path=OUT + r"\raise.png")
                print("RAISE PANEL", pg.evaluate(READ_JS))
                sl = pg.locator("input[type=range]")
                print("slider", sl.get_attribute("min"), sl.get_attribute("max"), sl.input_value())
                sl.fill(str(int(sl.get_attribute("min")) + 7))
                time.sleep(0.3)
                print("after fill", sl.input_value(), pg.inner_text("body")[-600:])
                pg.get_by_role("button", name="CONFIRM", exact=True).click()
            elif st.get("canCheck"):
                pg.get_by_role("button", name="CHECK", exact=True).click()
            else:
                pg.get_by_role("button", name="CALL").click()
            if acted > 6:
                break
        elif deal.count() and deal.is_visible():
            print("HAND OVER", json.dumps(pg.evaluate(READ_JS), ensure_ascii=False))
            deal.click()
        time.sleep(0.4)
    print(pg.inner_text("body")[-1500:])
    b.close()
