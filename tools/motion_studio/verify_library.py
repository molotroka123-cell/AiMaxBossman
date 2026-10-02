"""Render-check the scenario library: every scene of every spec is drawn by the real engine.

    python tools/motion_studio/verify_library.py --out DIR [--chromium PATH] [--dir library]

For each spec: validate, load its Lottie assets, render the middle frame of every scene (motion
blur off for speed) and fail on any engine error or a frame that is nearly uniform (nothing
drawn). Writes DIR/library_sheet.png (one tile per scene) and DIR/library_report.json.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lottie_assets  # noqa: E402
import spec as spec_mod  # noqa: E402

PROBE = r"""
(t) => {
  drawScene(t);
  const d = ctx.getImageData(0, 0, W, H).data; let lo = 255, hi = 0;
  for (let i = 0; i < d.length; i += 4 * 97) { const v = (d[i] + d[i + 1] + d[i + 2]) / 3; lo = Math.min(lo, v); hi = Math.max(hi, v); }
  const tile = document.createElement('canvas'); tile.width = 320; tile.height = 180;
  tile.getContext('2d').drawImage(work, 0, 0, 320, 180);
  return {contrast: hi - lo, png: tile.toDataURL('image/png')};
}
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--dir", type=Path, default=HERE / "library")
    ap.add_argument("--chromium")
    args = ap.parse_args()
    from playwright.sync_api import sync_playwright
    specs = sorted(args.dir.glob("*.json"))
    args.out.mkdir(parents=True, exist_ok=True)
    report, tiles = {}, []
    with sync_playwright() as pw:
        kw = {"args": ["--allow-file-access-from-files"]}
        if args.chromium:
            kw["executable_path"] = args.chromium
        browser = pw.chromium.launch(**kw)
        for path in specs:
            spec = json.loads(path.read_text(encoding="utf-8"))
            problems = spec_mod.validate(spec)
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
            page.goto((HERE / "engine.html").as_uri())
            page.evaluate("window.ready")
            page.evaluate("([s, h]) => window.loadSpec(s, h)", [spec, spec_mod.hits(spec)])
            used = lottie_assets.used_ids(spec)
            if used:
                page.evaluate("m => window.loadLottie(m)", lottie_assets.load(used))
            scenes = []
            for sc in spec["scenes"]:
                t = (sc["start"] + sc["end"]) / 2
                r = page.evaluate(PROBE, t)
                scenes.append({"type": sc["type"], "t": t, "contrast": round(r["contrast"], 1)})
                tiles.append((path.stem, sc["type"], r["png"]))
            page.close()
            blank = [s for s in scenes if s["contrast"] < 40]
            report[path.stem] = {"pass": not problems and not errors and not blank, "validation": problems,
                                 "page_errors": errors, "blank_scenes": blank, "scenes": len(scenes)}
        # contact sheet: 8 tiles per row
        sheet = browser.new_page(viewport={"width": 2560, "height": 180 * ((len(tiles) + 7) // 8)})
        html = "<body style='margin:0;background:#000;display:flex;flex-wrap:wrap;width:2560px'>" + "".join(
            f"<img src='{png}' width=320 height=180>" for _, _, png in tiles) + "</body>"
        sheet.set_content(html)
        sheet.screenshot(path=str(args.out / "library_sheet.png"), full_page=True)
        browser.close()
    passed = sum(r["pass"] for r in report.values())
    (args.out / "library_report.json").write_text(json.dumps({"passed": passed, "total": len(report), "specs": report},
                                                             indent=1), encoding="utf-8")
    for k, r in report.items():
        if not r["pass"]:
            print("FAIL", k, r)
    print(f"LIBRARY_RENDER {passed}/{len(report)} specs pass, {len(tiles)} scenes drawn")
    raise SystemExit(0 if passed == len(report) else 1)


if __name__ == "__main__":
    main()
