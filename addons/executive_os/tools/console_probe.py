"""Real browser smoke test against the running loopback sidecar."""
import argparse
import json
from pathlib import Path
import time

from playwright.sync_api import sync_playwright, expect


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    token = (args.data_dir / "token").read_text(encoding="utf-8").strip()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 1100})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        response = page.goto("http://127.0.0.1:8812/executive-os")
        assert response.status == 200
        page.locator("#status").click()
        expect(page.locator("#progress")).to_contain_text("токен")
        page.locator("#token").fill(token)
        page.locator("#status").click()
        expect(page.locator("#result")).to_contain_text('"enabled": true')
        mid = "browser-" + str(time.time_ns())
        content = '<img src=x onerror="window.untrustedExecuted=true"> Реальная проверка интерфейса.'
        page.locator("#mission").fill(mid)
        page.locator("#content").fill(content)
        page.locator("#execute").click()
        expect(page.locator("#result")).to_contain_text('"done": true', timeout=15000)
        result = json.loads(page.locator("#result").inner_text())
        assert result["done"] and result["verified_now"] == ["write", "verify"]
        assert (args.data_dir / "artifacts" / mid / "report.txt").read_text(encoding="utf-8") == content
        assert page.evaluate("window.untrustedExecuted === undefined")
        assert page.evaluate("Object.keys(localStorage).length === 0 && Object.keys(sessionStorage).length === 0")
        assert not errors, errors
        page.locator("#token").fill("")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(args.output.with_suffix(".png")), full_page=True)
        report = {"browser": "Chromium", "page_http": response.status,
                  "missing_token_blocked": True, "mission": mid, "verified_now": result["verified_now"],
                  "raw_html_rendered_as_text": True, "token_not_persisted": True, "page_errors": errors}
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
        browser.close()


if __name__ == "__main__":
    main()
