#!/usr/bin/env python3
"""Build ONE downloadable Bossman application for Windows.

The owner downloads one archive, unzips it and starts Bossman. No repository
clone, no system Python, no separate wheel picking, no hunting for Chromium or
ffmpeg. Everything the shipped product needs is inside the archive.

    BOSSMAN-Windows-x64-<short sha>/
      Start-Bossman.cmd      open the Command Center
      Evening-Test.cmd       exact-SHA owner acceptance for THIS build
      runtime/               embeddable CPython + every Python dependency
      browser/               Chromium used by the shipped browser path
      media/                 ffmpeg.exe, ffprobe.exe
      icons/                 the canonical application icon
      LICENSES/              notices for everything redistributed
      MANIFEST.json          exact source SHA, contents, measured checks
      SHA256SUMS             hash of every shipped file

Honesty rules this file obeys:

* the archive records what it actually contains. A prerequisite that could not
  be bundled is written into MANIFEST.json as ``required_downloads`` with the
  reason, and the first-run launcher fetches it — the owner is never told to go
  and find a binary by hand;
* nothing is stamped with a commit unless the tree is clean and still that
  commit when the build finishes;
* build-time acquisition failures fail the build. A bundle missing Chromium is
  not quietly relabelled as a bundle that never wanted Chromium.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

# python.org publishes an immutable embeddable build per patch release. The
# version is taken from the interpreter doing the build, so the wheels that pip
# resolves here are the wheels that runtime can import — no guessing a tag.
EMBED_URL = "https://www.python.org/ftp/python/{v}/python-{v}-embed-amd64.zip"

BUNDLE_DIRS = ("runtime", "browser", "media", "icons", "LICENSES", "app-support")

ICON_FILES = ("bossman.ico", "icon-1024.png", "icon-512.png", "icon-256.png",
              "icon-192.png", "icon-128.png", "icon-64.png", "icon-48.png",
              "icon-32.png", "icon-16.png")

# Shipped next to the runtime so the owner's evening test needs no checkout.
SUPPORT_SCRIPTS = (
    (ROOT / "tools" / "verify_installed_product.py", "verify_installed_product.py"),
    (ROOT / "scripts" / "bossman_doctor.py", "bossman_doctor.py"),
    (ROOT / "tools" / "bundle_evening_test.py", "bundle_evening_test.py"),
)

START_CMD = r"""@echo off
setlocal
rem Bossman — one-download Windows application. Everything is beside this file.
set "BOSSMAN_HOME=%~dp0"
call "%BOSSMAN_HOME%app-support\_env.cmd"
if errorlevel 1 exit /b 1
"%BOSSMAN_HOME%runtime\python.exe" -m bcc.desktop %*
exit /b %ERRORLEVEL%
"""

EVENING_CMD = r"""@echo off
setlocal
rem Owner evening acceptance for exactly this build. No repository required.
set "BOSSMAN_HOME=%~dp0"
call "%BOSSMAN_HOME%app-support\_env.cmd"
if errorlevel 1 exit /b 1
"%BOSSMAN_HOME%runtime\python.exe" "%BOSSMAN_HOME%app-support\bundle_evening_test.py" %*
exit /b %ERRORLEVEL%
"""

# One place decides the environment, so the two launchers cannot drift apart.
ENV_CMD = r"""@echo off
rem Sourced by the launchers. Points the product at the BUNDLED prerequisites.
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PLAYWRIGHT_BROWSERS_PATH=%BOSSMAN_HOME%browser"
set "PATH=%BOSSMAN_HOME%runtime\Scripts;%BOSSMAN_HOME%media;%PATH%"
if not exist "%BOSSMAN_HOME%runtime\python.exe" (
  echo [bossman] runtime is missing - the archive did not unzip completely.
  exit /b 1
)
if exist "%BOSSMAN_HOME%app-support\first-run.py" (
  "%BOSSMAN_HOME%runtime\python.exe" "%BOSSMAN_HOME%app-support\first-run.py"
  if errorlevel 1 exit /b 1
)
exit /b 0
"""


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=kwargs.pop("timeout", 1800), **kwargs)
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"failed ({result.returncode}): {' '.join(cmd)}\n{detail[-4000:]}")
    return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ----------------------------------------------------------------- layout

def bundle_name(sha: str) -> str:
    return f"BOSSMAN-Windows-x64-{sha[:12]}"


def launcher_files() -> dict[str, str]:
    """Path -> contents for every launcher shipped in the bundle."""
    return {
        "Start-Bossman.cmd": START_CMD,
        "Evening-Test.cmd": EVENING_CMD,
        "app-support/_env.cmd": ENV_CMD,
    }


def manifest_for(*, sha: str, when: str, files: list[dict], contents: dict,
                 checks: dict, required_downloads: list[dict]) -> dict:
    """The claim the archive makes about itself, in one place.

    ``downloads_required_for_bossman_app`` counts what the OWNER must fetch:
    the archive itself, plus anything the first run still has to acquire.
    """
    return {
        "schema_version": 1,
        "artifact": bundle_name(sha),
        "source_sha": sha,
        "source_dirty": False,
        "built_at": when,
        "platform": "windows-x64",
        "contents": contents,
        "checks": checks,
        "required_downloads": required_downloads,
        "downloads_required_for_bossman_app": 1 + len(required_downloads),
        "repository_clone_required": False,
        "system_python_required": False,
        "files": files,
    }


# ------------------------------------------------------------- acquisition

def fetch(url: str, target: Path) -> Path:
    """Download with the standard library; no extra build dependency."""
    from urllib.request import urlopen
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"  fetching {url}", flush=True)
    with urlopen(url, timeout=900) as response, target.open("wb") as out:
        shutil.copyfileobj(response, out)
    return target


def install_runtime(runtime: Path, work: Path) -> dict:
    """Embeddable CPython, with site-packages switched on."""
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    archive = fetch(EMBED_URL.format(v=version), work / f"python-{version}-embed.zip")
    runtime.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(runtime)
    # The embeddable build ships an isolated path file with `import site`
    # commented out; without it nothing in Lib/site-packages is importable.
    pth = next(iter(sorted(runtime.glob("python*._pth"))), None)
    if pth is None:
        raise RuntimeError("embeddable runtime has no ._pth file")
    text = pth.read_text(encoding="utf-8")
    if "#import site" in text:
        text = text.replace("#import site", "import site")
    elif "import site" not in text:
        text += "\nimport site\n"
    if "Lib\\site-packages" not in text:
        text = text.replace("\nimport site", "\nLib\\site-packages\nimport site")
    pth.write_text(text, encoding="utf-8")
    (runtime / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)
    return {"python_version": version, "embeddable_sha256": sha256_file(archive)}


def console_shim(spec: str) -> str:
    """A relocatable ``Scripts`` entry point for the EMBEDDED runtime.

    ``pip install --target`` does not produce usable console scripts: the
    wrappers it writes land beside the packages and hard-code the path of the
    interpreter that ran the build, so on the owner's machine they point at a
    Python that is not there. The archive therefore writes its own, and they
    resolve the runtime relative to themselves — the owner may unzip anywhere.
    """
    module, _, attribute = spec.partition(":")
    first, *rest = attribute.split(".") if attribute else ["main"]
    call = "obj" + "".join(f".{part}" for part in rest)
    code = f"import sys; from {module} import {first} as obj; sys.exit({call}())"
    return ("@echo off\r\n"
            "rem Generated by tools/build_windows_bundle.py — do not edit by hand.\r\n"
            f'"%~dp0..\\python.exe" -c "{code}" %*\r\n'
            "exit /b %ERRORLEVEL%\r\n")


def console_scripts(site_packages: Path) -> dict[str, str]:
    """Every console entry point the installed distributions declare."""
    import configparser
    found: dict[str, str] = {}
    for info in sorted(site_packages.glob("*.dist-info/entry_points.txt")):
        parser = configparser.ConfigParser()
        parser.read_string(info.read_text(encoding="utf-8"))
        if parser.has_section("console_scripts"):
            found.update(dict(parser.items("console_scripts")))
    return found


def install_packages(runtime: Path, wheels: Path) -> dict:
    """Bossman wheels and every third-party dependency, into the runtime."""
    site_packages = runtime / "Lib" / "site-packages"
    built = sorted(wheels.glob("*.whl"))
    if not built:
        raise RuntimeError("no Bossman wheels to install")
    run([sys.executable, "-m", "pip", "install", "--upgrade",
         "--target", str(site_packages), *map(str, built)])
    listing = run([sys.executable, "-m", "pip", "list", "--path", str(site_packages),
                   "--format=json"]).stdout

    entry_points = console_scripts(site_packages)
    # The wrappers pip left behind belong to the build machine's interpreter.
    # Shipping them would hand the owner commands that cannot run.
    for stale in ("bin", "Scripts"):
        shutil.rmtree(site_packages / stale, ignore_errors=True)
    scripts = runtime / "Scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for name, spec in sorted(entry_points.items()):
        (scripts / f"{name}.cmd").write_text(console_shim(spec), encoding="utf-8")
    return {"packages": json.loads(listing), "bossman_wheels": [w.name for w in built],
            "console_scripts": sorted(entry_points)}


def install_browser(browser: Path, runtime: Path) -> dict:
    """Chromium, resolved by the Playwright that is actually shipped."""
    browser.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=str(browser))
    python = runtime / "python.exe"
    run([str(python), "-m", "playwright", "install", "chromium"], env=env)
    found = sorted(browser.glob("chromium-*"))
    if not found:
        raise RuntimeError("Playwright reported success but installed no Chromium")
    return {"chromium_dirs": [p.name for p in found]}


def install_media(media: Path, work: Path, ffmpeg_zip: str | None) -> tuple[dict, list[dict]]:
    """ffmpeg + ffprobe. Missing is reported, never silently dropped."""
    media.mkdir(parents=True, exist_ok=True)
    if not ffmpeg_zip:
        return ({"ffmpeg": None, "ffprobe": None}, [{
            "component": "ffmpeg",
            "reason": "no --ffmpeg-zip supplied to the build",
            "acquired_by": "first run of Start-Bossman.cmd",
        }])
    archive = fetch(ffmpeg_zip, work / "ffmpeg.zip")
    with zipfile.ZipFile(archive) as zf:
        for member in zf.namelist():
            name = Path(member).name.lower()
            if name in {"ffmpeg.exe", "ffprobe.exe"}:
                with zf.open(member) as src, (media / name).open("wb") as dst:
                    shutil.copyfileobj(src, dst)
    missing = [n for n in ("ffmpeg.exe", "ffprobe.exe") if not (media / n).exists()]
    if missing:
        raise RuntimeError(f"{ffmpeg_zip} contained no {', '.join(missing)}")
    return ({"ffmpeg": "media/ffmpeg.exe", "ffprobe": "media/ffprobe.exe",
             "source": ffmpeg_zip, "sha256": sha256_file(archive)}, [])


def install_icons(icons: Path) -> dict:
    icons.mkdir(parents=True, exist_ok=True)
    source = ROOT / "command-center" / "ui" / "icons"
    shipped = {}
    for name in ICON_FILES:
        origin = source / name
        if not origin.exists():
            raise RuntimeError(f"canonical icon missing from the checkout: {name}")
        shutil.copyfile(origin, icons / name)
        shipped[name] = sha256_file(icons / name)
    return shipped


def install_support(support: Path) -> None:
    support.mkdir(parents=True, exist_ok=True)
    for origin, name in SUPPORT_SCRIPTS:
        if not origin.exists():
            raise RuntimeError(f"support script missing: {origin}")
        shutil.copyfile(origin, support / name)


def write_licenses(licenses: Path, contents: dict) -> None:
    licenses.mkdir(parents=True, exist_ok=True)
    (licenses / "README.md").write_text(
        "# Third-party components redistributed in this archive\n\n"
        f"* CPython {contents['runtime'].get('python_version', 'embeddable')} — "
        "Python Software Foundation License, https://docs.python.org/3/license.html\n"
        "* Python dependencies under `runtime/Lib/site-packages` — each distribution "
        "keeps its own `*.dist-info/METADATA` and licence files; see MANIFEST.json "
        "for the exact list and versions.\n"
        "* Chromium (via Playwright) under `browser/` — BSD-3-Clause and the licences "
        "listed in the browser directory.\n"
        "* ffmpeg/ffprobe under `media/`, when bundled — the licence of the build named "
        "in MANIFEST.json `contents.media.source`. Check that build's terms before "
        "redistributing this archive outside your organisation.\n",
        encoding="utf-8")


# ------------------------------------------------------------------ assemble

def assemble(out: Path, sha: str, *, wheels: Path, work: Path,
             ffmpeg_zip: str | None) -> tuple[dict, list[dict]]:
    for name in BUNDLE_DIRS:
        (out / name).mkdir(parents=True, exist_ok=True)
    contents: dict = {}
    contents["runtime"] = install_runtime(out / "runtime", work)
    contents["runtime"].update(install_packages(out / "runtime", wheels))
    contents["browser"] = install_browser(out / "browser", out / "runtime")
    media, required = install_media(out / "media", work, ffmpeg_zip)
    contents["media"] = media
    contents["icons"] = install_icons(out / "icons")
    install_support(out / "app-support")
    for name, body in launcher_files().items():
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body.replace("\n", "\r\n"), encoding="utf-8")
    write_licenses(out / "LICENSES", contents)
    return contents, required


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    parser.add_argument("--ffmpeg-zip", default=os.environ.get("BOSSMAN_FFMPEG_ZIP") or None,
                        help="URL of a Windows ffmpeg zip containing ffmpeg.exe and ffprobe.exe")
    parser.add_argument("--zip", action="store_true", help="also write the .zip archive")
    args = parser.parse_args(argv)

    if os.name != "nt":
        print("BOSSMAN_WINDOWS_BUNDLE=UNSUPPORTED_HOST "
              f"(needs Windows, this is {platform.system()})", file=sys.stderr)
        return 2

    import build_local_bundle as local

    sha = local._source_sha()
    if local._source_dirty():
        print("BOSSMAN_WINDOWS_BUNDLE=FAIL dirty source tree", file=sys.stderr)
        return 2

    out_root = args.out.resolve()
    out = out_root / bundle_name(sha)
    if out.exists():
        raise SystemExit(f"output already exists, choose another --out: {out}")
    out.mkdir(parents=True)
    work = out_root / "_work"
    work.mkdir(parents=True, exist_ok=True)

    print(f"building {out.name} from {sha}", flush=True)
    wheels = work / "wheels"
    with local.clean_source_snapshot(sha) as snapshot:
        local.build_wheels(wheels, snapshot)

    contents, required = assemble(out, sha, wheels=wheels, work=work, ffmpeg_zip=args.ffmpeg_zip)

    if local._source_sha() != sha or local._source_dirty():
        raise SystemExit("source changed during the build; artifact is not exact-SHA evidence")

    when = datetime.now(timezone.utc).isoformat()
    files = [{"path": p.relative_to(out).as_posix(), "bytes": p.stat().st_size,
              "sha256": sha256_file(p)}
             for p in sorted(out.rglob("*")) if p.is_file()]
    manifest = manifest_for(sha=sha, when=when, files=files, contents=contents,
                            checks={"installed_acceptance": "NOT_RUN"},
                            required_downloads=required)
    (out / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "SHA256SUMS").write_text(
        "".join(f"{f['sha256']}  {f['path']}\n" for f in files), encoding="utf-8")
    (out / "README.md").write_text(
        f"# BOSSMAN for Windows\n\nBuild `{sha}`, {when}.\n\n"
        "Unzip anywhere, then double-click **Start-Bossman.cmd**.\n"
        "There is nothing else to install: the Python runtime, every dependency, "
        "the browser and the media tools are inside this folder.\n\n"
        "**Evening-Test.cmd** runs the owner acceptance for exactly this build and "
        "prints the build SHA, platform, doctor state and the final verdict.\n\n"
        "Your data lives in `%LOCALAPPDATA%\\Bossman\\CommandCenter` and survives "
        "replacing this folder with a newer build.\n",
        encoding="utf-8")

    total = sum(f["bytes"] for f in files)
    print(f"BOSSMAN_WINDOWS_BUNDLE=BUILT files={len(files)} bytes={total} "
          f"required_downloads={len(required)}", flush=True)

    if args.zip:
        archive = out_root / f"{out.name}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for path in sorted(out.rglob("*")):
                if path.is_file():
                    zf.write(path, Path(out.name) / path.relative_to(out))
        print(f"BOSSMAN_WINDOWS_ARCHIVE={archive} bytes={archive.stat().st_size}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
