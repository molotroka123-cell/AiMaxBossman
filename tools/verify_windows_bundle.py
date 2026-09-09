#!/usr/bin/env python3
"""Accept the downloaded Bossman archive as a PRODUCT, not as files in a zip.

Extracts the archive into a fresh directory whose path contains spaces, then
drives it only through what the archive itself ships: its own runtime, its own
Chromium, its own ffmpeg. The repository is never on ``sys.path`` for any of it,
so a bundle that silently depends on the checkout fails here instead of on the
owner's machine.

    python tools/verify_windows_bundle.py --archive dist/BOSSMAN-...zip \
        --expected-sha <40 hex> --out bundle-acceptance.json

Exit 0 PASS, 1 FAIL, 2 OWNER_REQUIRED.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _run(args: list[str], *, env: dict | None = None, cwd: Path | None = None,
         timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, env=env, cwd=cwd)


def _clean_env(home: Path) -> dict:
    """Exactly what the launcher gives the product — and nothing of the repo."""
    env = {k: v for k, v in os.environ.items()
           if k.upper() not in {"PYTHONPATH", "BCC_UI_DIR", "BCC_DATA_DIR", "DATABASE_URL"}}
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(home / "browser")
    env["PATH"] = str(home / "media") + os.pathsep + env.get("PATH", "")
    return env


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_manifest(home: Path, expected_sha: str) -> list[str]:
    problems = []
    manifest = json.loads((home / "MANIFEST.json").read_text(encoding="utf-8"))
    if manifest["source_sha"] != expected_sha:
        problems.append(f"MANIFEST source_sha {manifest['source_sha']} != {expected_sha}")
    if manifest.get("source_dirty"):
        problems.append("MANIFEST reports a dirty source tree")
    for item in manifest["files"]:
        path = home / item["path"]
        if not path.exists():
            problems.append(f"missing shipped file: {item['path']}")
        elif _sha256(path) != item["sha256"]:
            problems.append(f"checksum mismatch: {item['path']}")
    return problems


def check_icons(home: Path) -> list[str]:
    """The archive must carry the SAME icon the repository calls canonical."""
    problems = []
    source = REPO / "command-center" / "ui" / "icons"
    for name in ("bossman.ico", "icon-512.png", "icon-256.png", "icon-32.png"):
        shipped, origin = home / "icons" / name, source / name
        if not shipped.exists():
            problems.append(f"icon not shipped: {name}")
        elif origin.exists() and _sha256(shipped) != _sha256(origin):
            problems.append(f"shipped icon differs from the canonical one: {name}")
    return problems


def check_media(home: Path, env: dict) -> tuple[list[str], dict]:
    """A real decode-side probe with the BUNDLED binaries, not a PATH lookup."""
    details, problems = {}, []
    for name in ("ffmpeg", "ffprobe"):
        binary = home / "media" / (f"{name}.exe" if os.name == "nt" else name)
        if not binary.exists():
            details[name] = "NOT_BUNDLED"
            continue
        done = _run([str(binary), "-version"], env=env, timeout=120)
        details[name] = (done.stdout or "").splitlines()[0] if done.returncode == 0 else "FAILED"
        if done.returncode:
            problems.append(f"bundled {name} did not run: {(done.stderr or '')[:200]}")
    return problems, details


def check_browser(home: Path, env: dict) -> tuple[list[str], dict]:
    """Launch the bundled Chromium through the bundled Playwright."""
    python = home / "runtime" / ("python.exe" if os.name == "nt" else "bin/python")
    probe = (
        "import json\n"
        "from playwright.sync_api import sync_playwright\n"
        "with sync_playwright() as p:\n"
        "    browser = p.chromium.launch()\n"
        "    page = browser.new_page()\n"
        "    page.set_content('<h1 id=t>bossman</h1>')\n"
        "    text = page.inner_text('#t')\n"
        "    version = browser.version\n"
        "    browser.close()\n"
        "print(json.dumps({'text': text, 'version': version}))\n"
    )
    done = _run([str(python), "-c", probe], env=env, timeout=600)
    if done.returncode:
        return ([f"bundled Chromium did not launch: {(done.stderr or '')[-400:]}"],
                {"chromium": "FAILED"})
    try:
        payload = json.loads(done.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return (["bundled Chromium probe returned no result"], {"chromium": "UNREADABLE"})
    if payload.get("text") != "bossman":
        return (["bundled Chromium did not render"], {"chromium": "NO_RENDER"})
    return ([], {"chromium": payload.get("version")})


def check_no_repo_dependency(home: Path, env: dict) -> list[str]:
    """Import the product from the bundle with the repo absent from sys.path."""
    python = home / "runtime" / ("python.exe" if os.name == "nt" else "bin/python")
    probe = (
        "import json, sys, bcc\n"
        "print(json.dumps({'file': bcc.__file__, 'path': sys.path}))\n"
    )
    done = _run([str(python), "-c", probe], env=env, cwd=home, timeout=300)
    if done.returncode:
        return [f"the bundled runtime cannot import bcc: {(done.stderr or '')[-400:]}"]
    payload = json.loads(done.stdout.strip().splitlines()[-1])
    if str(REPO) in payload["file"]:
        return [f"the bundle imported the product from the repository: {payload['file']}"]
    leaked = [p for p in payload["path"] if p and Path(p).is_absolute()
              and str(REPO) == str(Path(p))[:len(str(REPO))]]
    return [f"repository on the bundle's sys.path: {leaked}"] if leaked else []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    problems: list[str] = []
    details: dict = {"archive": args.archive.name,
                     "archive_bytes": args.archive.stat().st_size,
                     "archive_sha256": _sha256(args.archive)}

    # A directory with spaces: quoting bugs in launchers must surface here and
    # not the first time the owner unzips into "C:\Program Files".
    parent = Path(tempfile.mkdtemp(prefix="bossman bundle "))
    try:
        target = parent / "Owner Downloads"
        target.mkdir()
        with zipfile.ZipFile(args.archive) as zf:
            zf.extractall(target)
        roots = [p for p in target.iterdir() if p.is_dir()]
        if len(roots) != 1:
            raise SystemExit(f"archive must contain exactly one top directory, got {roots}")
        home = roots[0]
        details["extracted_to"] = str(home)
        env = _clean_env(home)

        problems += check_manifest(home, args.expected_sha)
        problems += check_icons(home)
        problems += check_no_repo_dependency(home, env)
        media_problems, details["media"] = check_media(home, env)
        problems += media_problems
        browser_problems, details["browser"] = check_browser(home, env)
        problems += browser_problems

        # The product itself: boot, HTTP identity for this SHA, assets, restart
        # and persistence — run through the archive's own evening entry point.
        evening = _run([str(home / "runtime" / ("python.exe" if os.name == "nt" else "bin/python")),
                        str(home / "app-support" / "bundle_evening_test.py")],
                       env=env, cwd=home, timeout=1800)
        details["evening_stdout"] = (evening.stdout or "")[-4000:]
        details["evening_returncode"] = evening.returncode
        if evening.returncode == 1:
            problems.append("bundled evening acceptance reported FAIL")
            details["evening_stderr"] = (evening.stderr or "")[-2000:]
    finally:
        shutil.rmtree(parent, ignore_errors=True)

    owner_required = details.get("evening_returncode") == 2
    status = "FAIL" if problems else ("OWNER_REQUIRED" if owner_required else "PASS")
    report = {"status": status, "expected_sha": args.expected_sha,
              "checked_at": datetime.now(timezone.utc).isoformat(),
              "problems": problems, "details": details}
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(f"BOSSMAN_BUNDLE_ACCEPTANCE={status}")
    for problem in problems:
        print(f"  - {problem}")
    return {"PASS": 0, "OWNER_REQUIRED": 2}.get(status, 1)


if __name__ == "__main__":
    raise SystemExit(main())
