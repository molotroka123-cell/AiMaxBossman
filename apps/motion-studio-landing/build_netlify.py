"""Build the Netlify variant of the landing into <site>/studio/ (25.10: the Vercel account is suspended and Hobby
forbids commercial use; Netlify free allows it). Leads go through Netlify Forms (stored in the owner's Netlify
dashboard + e-mail notification) — no bot token or other secret ever reaches the static site.

    python build_netlify.py C:\\Users\\asd\\Bossman\\site-subtle-baklava
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent
BASE = "/studio/"
PUBLIC_URL = "https://subtle-baklava-676893.netlify.app/studio/"
FILES = ["styles.css", "favicon.svg", "og.png"]

SUBMIT_OLD = re.compile(r'fetch\("/api/lead".*?JSON\.stringify\(payload\) \}\)\s*'
                        r'\.then\(function \(r\) \{.*?\}\)\s*\.then\(function \(res\) \{\s*if \(res\.ok && res\.j\.ok\) \{',
                        re.S)
SUBMIT_NEW = ('var body = new URLSearchParams(Object.assign({"form-name": "lead"}, payload, {consent: "ano"})).toString();\n'
              '    fetch("' + BASE + '", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: body })\n'
              '      .then(function (r) { return { ok: r.ok, j: {} }; })\n'
              '      .then(function (res) {\n'
              '        if (res.ok) {')


def build(site: Path) -> Path:
    out = site / "studio"
    if out.exists():
        shutil.rmtree(out)
    (out / "samples").mkdir(parents=True)
    for name in FILES:
        shutil.copy2(SRC / name, out / name)
    # samples.json keeps root paths (/samples/x.mp4) for the root deploy; under /studio/ they get the prefix
    sj = (SRC / "samples" / "samples.json").read_text(encoding="utf-8").replace('"/samples/', '"' + BASE + 'samples/')
    (out / "samples" / "samples.json").write_text(sj, encoding="utf-8", newline="\n")
    for media in [*(SRC / "samples").glob("*.mp4"), *(SRC / "samples").glob("*.jpg")]:
        shutil.copy2(media, out / "samples" / media.name)

    html = (SRC / "index.html").read_text(encoding="utf-8")
    html = html.replace("https://bossman-motion-studio.vercel.app/", PUBLIC_URL)
    html = re.sub(r'(href|src)="/(?!/)', r'\1="' + BASE, html)
    html = html.replace('<form id="lead" novalidate>',
                        '<form id="lead" name="lead" method="POST" data-netlify="true" netlify-honeypot="website" '
                        'action="' + BASE + '" novalidate>\n        <input type="hidden" name="form-name" value="lead">')
    if 'data-netlify="true"' not in html:
        raise SystemExit("form tag not found: index.html changed, update build_netlify.py")
    (out / "index.html").write_text(html, encoding="utf-8", newline="\n")

    js = (SRC / "app.js").read_text(encoding="utf-8").replace('fetch("/samples/', 'fetch("' + BASE + 'samples/')
    js, n = SUBMIT_OLD.subn(SUBMIT_NEW, js)
    if n != 1:
        raise SystemExit("submit handler not found: app.js changed, update build_netlify.py")
    (out / "app.js").write_text(js, encoding="utf-8", newline="\n")
    return out


if __name__ == "__main__":
    print(build(Path(sys.argv[1])))
