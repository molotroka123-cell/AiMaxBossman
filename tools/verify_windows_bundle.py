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


def _console_utf8() -> None:
    """This script relays Russian output from the bundle to the console.

    The Windows runner console is cp1252, so the relay itself died with
    UnicodeEncodeError and hid the very failure it was printing.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


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
    """Encode, inspect and decode with the same binaries Video Studio uses.

    An LGPL build can print its version successfully but lacks libx264, which
    the product needs for its default MP4 exports and preview proxies.
    """
    details, problems = {}, []
    for name in ("ffmpeg", "ffprobe"):
        binary = home / "media" / (f"{name}.exe" if os.name == "nt" else name)
        if not binary.exists():
            details[name] = "NOT_BUNDLED"
            problems.append(f"required media binary is not bundled: {name}")
            continue
        done = _run([str(binary), "-version"], env=env, timeout=120)
        lines = (done.stdout or "").splitlines()
        details[name] = lines[0] if done.returncode == 0 and lines else "FAILED"
        if done.returncode:
            problems.append(f"bundled {name} did not run: {(done.stderr or '')[:200]}")
    if problems:
        return problems, details

    ffmpeg = home / "media" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    ffprobe = home / "media" / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
    with tempfile.TemporaryDirectory(prefix="bossman media probe ") as scratch:
        output = Path(scratch) / "default export.mp4"
        encoded = _run([
            str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", "0.4", "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(output),
        ], env=env, timeout=120)
        if encoded.returncode or not output.is_file() or output.stat().st_size == 0:
            details["default_export"] = "FAILED"
            problems.append("bundled FFmpeg cannot export the default libx264/AAC MP4: "
                            + (encoded.stderr or "no output")[-400:])
            return problems, details
        probed = _run([str(ffprobe), "-v", "error", "-show_streams", "-of", "json",
                       str(output)], env=env, timeout=120)
        try:
            streams = json.loads(probed.stdout)["streams"] if probed.returncode == 0 else []
            codecs = {(stream.get("codec_type"), stream.get("codec_name")) for stream in streams}
        except (ValueError, KeyError, TypeError, AttributeError):
            codecs = set()
        if not {("video", "h264"), ("audio", "aac")}.issubset(codecs):
            details["default_export"] = "FAILED"
            problems.append("bundled ffprobe did not confirm H264 video and AAC audio")
            return problems, details
        decoded = _run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-xerror",
                        "-nostdin", "-i", str(output), "-map", "0:v", "-map", "0:a",
                        "-f", "null", "-"], env=env, timeout=120)
        if decoded.returncode:
            details["default_export"] = "FAILED"
            problems.append("bundled FFmpeg could not fully decode its export: "
                            + (decoded.stderr or "")[-400:])
        else:
            details["default_export"] = {"status": "PASS", "video_codec": "h264",
                                         "audio_codec": "aac", "fully_decoded": True,
                                         "bytes": output.stat().st_size}
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


def _bundle_python(home: Path) -> Path:
    return home / "runtime" / ("python.exe" if os.name == "nt" else "bin/python")


def _evening_result(stdout: str) -> dict | None:
    """The structured result the evening test wrote for THIS run, by its own pointer."""
    for line in (stdout or "").splitlines():
        if line.startswith("OWNER_EVENING_EVIDENCE="):
            path = Path(line.partition("=")[2].strip()) / "OWNER_EVENING_RESULT.json"
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            return loaded if isinstance(loaded, dict) else None
    return None


def run_evening(home: Path, env: dict) -> tuple[subprocess.CompletedProcess, dict | None]:
    done = _run([str(_bundle_python(home)), str(home / "app-support" / "bundle_evening_test.py")],
                env=env, cwd=home, timeout=1800)
    return done, _evening_result(done.stdout)


def evening_negative_control(home: Path, env: dict) -> str:
    """OA-01 on the extracted archive itself, not only in the source tree.

    The doctor is hidden for one run; the SHIPPED evening test must then say
    FAIL with ``doctor_not_shipped`` and exit 1 (it refuses before starting
    the product, so this costs seconds). A control that passes anyway means
    the archive carries an evening test whose verdict cannot be trusted.
    """
    doctor = home / "app-support" / "bossman_doctor.py"
    hidden = doctor.with_name("bossman_doctor.py.negative-control")
    doctor.rename(hidden)
    try:
        done, result = run_evening(home, env)
    finally:
        hidden.rename(doctor)
    codes = [reason.get("code") for reason in (result or {}).get("reasons", []) if isinstance(reason, dict)]
    if done.returncode == 1 and result and result.get("verdict") == "FAIL" and "doctor_not_shipped" in codes:
        return "PASS"
    return f"FAILED: exit {done.returncode}, verdict {(result or {}).get('verdict')!r}, reasons {codes}"


def _harness_sha() -> str | None:
    """The checkout that drove this verification (OA-02 binding)."""
    try:
        found = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True,
                               text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        found = ""
    if not found:
        found = os.environ.get("GITHUB_SHA", "")
    return found if len(found) == 40 else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    _console_utf8()

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
        manifest = json.loads((home / "MANIFEST.json").read_text(encoding="utf-8"))
        inputs = manifest.get("build_inputs") if isinstance(manifest.get("build_inputs"), dict) else {}
        # OA-03: a release candidate names every input it was built from.
        details["build_inputs_locked"] = inputs.get("locked") is True
        details["build_profile"] = inputs.get("profile")
        details["required_downloads"] = len(manifest.get("required_downloads") or [])
        problems += check_icons(home)
        problems += check_no_repo_dependency(home, env)
        media_problems, details["media"] = check_media(home, env)
        problems += media_problems
        browser_problems, details["browser"] = check_browser(home, env)
        problems += browser_problems

        # The product itself: boot, HTTP identity for this SHA, assets, restart
        # and persistence — run through the archive's own evening entry point.
        # The verdict is read from the structured result the run wrote, and
        # the exit code must agree with it; a run that left no result is a
        # failure, not a pass by silence (OA-01).
        evening, result = run_evening(home, env)
        details["evening_stdout"] = (evening.stdout or "")[-8000:]
        details["evening_returncode"] = evening.returncode
        verdict = result.get("verdict") if result else "NO_RESULT"
        details["evening_verdict"] = verdict
        details["evening_run_id"] = result.get("run_id") if result else None
        details["evening_reasons"] = [reason.get("code") for reason in (result or {}).get("reasons", [])
                                      if isinstance(reason, dict)]
        expected_code = {"PASS": 0, "OWNER_REQUIRED": 2}.get(verdict)
        if result is None:
            problems.append("bundled evening acceptance left no readable OWNER_EVENING_RESULT.json")
        elif evening.returncode != expected_code:
            problems.append(f"bundled evening acceptance verdict {verdict} (exit {evening.returncode}) "
                            "is not a full PASS or OWNER_REQUIRED: " + ", ".join(details["evening_reasons"]))
        if problems and evening.returncode not in (0, 2):
            details["evening_stderr"] = (evening.stderr or "")[-4000:]
        if evening.returncode:
            # Print it: the JSON report travels in the artifact, but whoever is
            # reading a red build is reading the log, and "reported FAIL" with
            # no reason costs a whole build to re-learn.
            print("--- bundled evening acceptance output ---")
            print((evening.stdout or "")[-6000:])
            print("--- stderr ---")
            print((evening.stderr or "")[-3000:])
        details["evening_negative_control"] = evening_negative_control(home, env)
        if details["evening_negative_control"] != "PASS":
            problems.append("the shipped evening test accepted an archive without its doctor: "
                            + details["evening_negative_control"])
    finally:
        shutil.rmtree(parent, ignore_errors=True)

    owner_required = details.get("evening_verdict") == "OWNER_REQUIRED"
    status = "FAIL" if problems else ("OWNER_REQUIRED" if owner_required else "PASS")
    report = {"status": status, "expected_sha": args.expected_sha,
              "checked_at": datetime.now(timezone.utc).isoformat(),
              "problems": problems, "details": details,
              # OA-02: which bytes, which run and which checkout this report is about.
              "binding": {"source_sha": args.expected_sha, "archive_sha256": details["archive_sha256"],
                          "run_id": os.environ.get("GITHUB_RUN_ID") or "local",
                          "harness_sha": _harness_sha()}}
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(f"BOSSMAN_BUNDLE_ACCEPTANCE={status}")
    for problem in problems:
        print(f"  - {problem}")
    return {"PASS": 0, "OWNER_REQUIRED": 2}.get(status, 1)


if __name__ == "__main__":
    raise SystemExit(main())
