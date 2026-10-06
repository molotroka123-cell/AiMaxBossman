"""Render-check every catalog animation in the real engine: fetch (sha256-pinned), load, draw.

    python tools/motion_studio/verify_lottie.py --out DIR [--chromium PATH]

For each animation eight evenly spaced frames are drawn through the engine's own lottie-web
path; an animation passes when at least one frame has visible pixels (some legitimately start
or end empty: a rainbow drawing in, a shark swimming off) and the frames differ (it animates). Writes DIR/lottie_contact_sheet.png (10 x 10, middle
frames) and DIR/lottie_report.json. Exit code 0 only if all pass.
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

PROBE = r"""
([ids]) => {
  const out = {}, sheet = document.createElement('canvas'); sheet.width = 1600; sheet.height = 1600;
  const sc = sheet.getContext('2d'); sc.fillStyle = '#0a0c10'; sc.fillRect(0, 0, 1600, 1600);
  ids.forEach((id, n) => {
    const L = LOTTIE[id], stats = [];
    for (const k of [0, .125, .25, .375, .5, .625, .75, .875]) {
      L.anim.goToAndStop(Math.floor(k * (L.frames - 1)), true);
      const d = L.c.getContext('2d').getImageData(0, 0, 512, 512).data;
      let vis = 0, sum = 0;
      for (let i = 3; i < d.length; i += 16) { if (d[i] > 16) vis++; sum = (sum + d[i - 3] * 3 + d[i - 2] * 5 + d[i - 1] * 7 + d[i]) % 1000000007; }
      stats.push({visible: vis, hash: sum});
      if (k === 0.5) sc.drawImage(L.c, (n % 10) * 160, Math.floor(n / 10) * 160, 160, 160);
    }
    out[id] = stats;
  });
  return {stats: out, sheet: sheet.toDataURL('image/png')};
}
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--chromium")
    args = ap.parse_args()
    from playwright.sync_api import sync_playwright
    ids = sorted(lottie_assets.ids())
    data = lottie_assets.load(set(ids))
    args.out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        kw = {"args": ["--allow-file-access-from-files"]}
        if args.chromium:
            kw["executable_path"] = args.chromium
        browser = pw.chromium.launch(**kw)
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto((HERE / "engine.html").as_uri())
        page.evaluate("window.ready")
        loaded = page.evaluate("m => window.loadLottie(m)", data)
        result = page.evaluate(PROBE, [ids])
        browser.close()
    (args.out / "lottie_contact_sheet.png").write_bytes(base64.b64decode(result["sheet"].split(",", 1)[1]))
    report = {}
    for i in ids:
        s = result["stats"][i]
        visible = max(x["visible"] for x in s) > 200
        animates = len({x["hash"] for x in s}) > 1
        report[i] = {"pass": visible and animates, "visible_px_sampled": [x["visible"] for x in s], "animates": animates}
    passed = sum(r["pass"] for r in report.values())
    (args.out / "lottie_report.json").write_text(json.dumps({"loaded": loaded, "page_errors": errors, "passed": passed,
                                                             "total": len(ids), "items": report}, indent=1), encoding="utf-8")
    for i, r in report.items():
        if not r["pass"]:
            print("FAIL", i, r)
    print(f"LOTTIE_RENDER {passed}/{len(ids)} pass, page errors: {len(errors)}")
    raise SystemExit(0 if passed == len(ids) and not errors else 1)


if __name__ == "__main__":
    main()
