r"""Turn a website into a standalone desktop-app window (Edge WebView2 engine, `--app` mode) with its own isolated profile.

    python tools/site_app.py https://higgsfield.com --name higgsfield [--launch] [--check]

Why this and not only Pake: Pake (github.com/tw93/Pake, Tauri) needs a Rust toolchain + MSVC Build Tools (several GB, UAC) and its
CLI `pake-cli` is GPL-3.0-or-later, so it is used as an external tool, never vendored into Bossman. This wrapper needs nothing
beyond the Edge that Windows already has and produces the same user-facing result: an app window without browser chrome, a
separate profile (cookies of the site never mix with the owner's browser), and a launcher file.

Rules: https only; no credentials are read, stored or typed; the profile lives under D:\Bossman\work\site-apps\<name>.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

BASE = Path(r"D:\Bossman\work\site-apps")
EDGE_CANDIDATES = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]


def edge() -> str:
    for p in EDGE_CANDIDATES:
        if Path(p).is_file():
            return p
    found = shutil.which("msedge")
    if not found:
        raise SystemExit("Microsoft Edge not found")
    return found


def validate(url: str, name: str) -> tuple[str, str]:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise SystemExit("only plain https URLs without credentials are allowed")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", name):
        raise SystemExit("name: lowercase letters, digits, dashes")
    return url, name


def app_args(url: str, profile: Path) -> list[str]:
    return [edge(), f"--app={url}", f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check", "--window-size=1280,860"]


def make_launcher(url: str, name: str) -> Path:
    profile = BASE / name / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    cmd = BASE / name / f"{name}.cmd"
    cmd.write_text("@echo off\r\nstart \"\" " + " ".join(f'"{a}"' for a in app_args(url, profile)) + "\r\n", encoding="utf-8")
    return cmd


def check(url: str, name: str) -> dict:
    """Headless load of the page in the same engine: proves the site renders in it (title + screenshot), no login, no input."""
    shot = BASE / name / "check.png"
    prof = BASE / name / "check-profile"
    t0 = time.time()
    proc = subprocess.run([edge(), "--headless", "--disable-gpu", f"--user-data-dir={prof}", "--window-size=1280,860",
                           "--virtual-time-budget=15000", f"--screenshot={shot}", url], capture_output=True, text=True, timeout=120)
    # Edge hands the headless job to a background process and returns at once: wait for the screenshot to appear and stop growing
    deadline, last = time.time() + 90, -1
    while time.time() < deadline:
        size = shot.stat().st_size if shot.is_file() else 0
        if size > 10_000 and size == last:
            break
        last = size
        time.sleep(2)
    ok = shot.is_file() and shot.stat().st_size > 10_000
    shutil.rmtree(prof, ignore_errors=True)
    return {"url": url, "screenshot": str(shot), "bytes": shot.stat().st_size if shot.is_file() else 0, "rendered": ok,
            "seconds": round(time.time() - t0, 1), "rc": proc.returncode}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("url")
    ap.add_argument("--name", required=True)
    ap.add_argument("--launch", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    url, name = validate(a.url, a.name)
    launcher = make_launcher(url, name)
    print("launcher:", launcher)
    if a.check:
        print(check(url, name))
    if a.launch:
        subprocess.Popen(app_args(url, BASE / name / "profile"))
        print("launched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
