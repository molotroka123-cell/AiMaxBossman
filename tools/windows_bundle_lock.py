#!/usr/bin/env python3
"""Pin every input of the Windows archive BEFORE the build reads it (OA-03).

    python tools/windows_bundle_lock.py record --wheels <dir> --dist <dir> --out <dir>
    python tools/windows_bundle_lock.py show

The lock is two committed files beside this one:

* ``windows_bundle_lock.json`` — the embeddable CPython patch and the SHA-256 of
  its zip, the FFmpeg release asset (an immutable ``autobuild-*`` release, not
  the rolling ``latest`` tag) and its SHA-256, the Chromium directory the
  locked Playwright installs, and the SHA-256 of the requirements file;
* ``windows_bundle_lock.txt`` — every third-party distribution the product
  needs on ``win_amd64`` / ``cp312``, ``name==version`` with the SHA-256 of
  the exact file pip resolved, in pip's hash-checking format.

``build_windows_bundle.py`` reads both. With the lock present it downloads
nothing whose digest it was not told, lets pip resolve nothing, and fails on
the first mismatch — before extracting an archive or installing a package.
Without the lock it still builds, says ``BOSSMAN_BUILD_INPUTS=UNLOCKED`` and
writes ``build_inputs.locked=false`` into MANIFEST.json, which the freeze
aggregator refuses to release.

``record`` runs on the Windows build runner (the only place the real
resolution for that platform exists) and prints the two files between
``BOSSMAN_LOCK_*_BEGIN/END`` markers, so they can be copied into the
repository from the job log. It resolves with ``pip install --dry-run
--report`` (no wheel is installed), queries the FFmpeg release list for the
newest immutable release-branch asset, downloads it, hashes it, compares the
hash with the digest the API publishes, and refuses to lock an FFmpeg that
cannot encode and fully decode libx264/AAC.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

TOOLS = Path(__file__).resolve().parent
LOCK_JSON = TOOLS / "windows_bundle_lock.json"
LOCK_TXT = TOOLS / "windows_bundle_lock.txt"
EMBED_URL = "https://www.python.org/ftp/python/{v}/python-{v}-embed-amd64.zip"
FFMPEG_RELEASES = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases?per_page=20"
# A release-branch build of one exact upstream commit, e.g.
# ffmpeg-n8.0-12-gabcdef1234-win64-gpl-8.0.zip. Never the rolling
# ffmpeg-master-latest-* or ffmpeg-n8.0-latest-* names.
FFMPEG_ASSET = re.compile(r"^ffmpeg-n(?P<major>\d+)\.(?P<minor>\d+)-\d+-g[0-9a-f]+-win64-gpl-(?P=major)\.(?P=minor)\.zip$")
BOSSMAN_DISTRIBUTIONS = ("bossman-shared", "bossman-core", "bossman-command-center")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


# ------------------------------------------------------------------ reading

def load(json_path: Path = LOCK_JSON, txt_path: Path = LOCK_TXT) -> dict | None:
    """The committed lock, validated, or None when the repository has none."""
    if not json_path.is_file() or not txt_path.is_file():
        return None
    lock = json.loads(json_path.read_text(encoding="utf-8"))
    if lock.get("schema_version") != 1:
        raise ValueError("windows bundle lock: unsupported schema_version")
    for section, keys in (("python", ("version", "embeddable_url", "embeddable_sha256")),
                          ("ffmpeg", ("url", "sha256", "asset")),
                          ("requirements", ("sha256", "count"))):
        block = lock.get(section)
        if not isinstance(block, dict) or any(key not in block for key in keys):
            raise ValueError(f"windows bundle lock: {section} section incomplete")
    for digest in (lock["python"]["embeddable_sha256"], lock["ffmpeg"]["sha256"], lock["requirements"]["sha256"]):
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise ValueError("windows bundle lock: a digest is not a SHA-256 hex string")
    if sha256_file(txt_path) != lock["requirements"]["sha256"]:
        raise ValueError("windows bundle lock: windows_bundle_lock.txt does not match the digest in the JSON")
    pins = requirement_pins(txt_path.read_text(encoding="utf-8"))
    if len(pins) != lock["requirements"]["count"]:
        raise ValueError("windows bundle lock: requirement count does not match")
    lock["_pins"] = pins
    lock["_txt"] = txt_path
    return lock


def requirement_pins(text: str) -> dict[str, str]:
    """``{normalized name: version}`` from a hash-checking requirements file."""
    pins: dict[str, str] = {}
    for raw in text.replace("\\\n", " ").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s]+)(\s+--hash=sha256:[0-9a-f]{64})+\s*$", line)
        if not match:
            raise ValueError(f"windows bundle lock: not a pinned, hashed requirement: {raw!r}")
        name = normalize(match.group(1))
        if name in pins:
            raise ValueError(f"windows bundle lock: {name} pinned twice")
        pins[name] = match.group(2)
    return pins


# ---------------------------------------------------------------- recording

def requirements_from_report(report: dict) -> list[dict]:
    """Every distribution pip resolved from an index, with the file digest it chose."""
    rows = []
    for item in report.get("install", []):
        info = item.get("download_info") or {}
        url = info.get("url", "")
        if url.startswith("file:"):
            continue  # the Bossman wheels built from the source snapshot
        digest = ((info.get("archive_info") or {}).get("hashes") or {}).get("sha256")
        metadata = item.get("metadata") or {}
        name, version = metadata.get("name"), metadata.get("version")
        if not (name and version and isinstance(digest, str) and DIGEST.fullmatch(digest)):
            raise ValueError(f"pip report entry without name/version/sha256: {item.get('metadata')}")
        rows.append({"name": normalize(name), "version": version, "sha256": digest, "url": url})
    rows.sort(key=lambda row: row["name"])
    names = [row["name"] for row in rows]
    if len(set(names)) != len(names):
        raise ValueError("pip report resolved one distribution twice")
    return rows


def requirements_text(rows: list[dict], *, recorded_at: str, platform_tag: str) -> str:
    lines = [f"# Generated by tools/windows_bundle_lock.py record on {recorded_at}; {platform_tag}.",
             "# pip hash-checking mode: every line is name==version --hash=sha256:<digest of the exact file>.",
             "# Edit by re-recording on the Windows build runner, never by hand.", ""]
    for row in rows:
        lines.append(f"# {row['url']}")
        lines.append(f"{row['name']}=={row['version']} \\")
        lines.append(f"    --hash=sha256:{row['sha256']}")
    return "\n".join(lines) + "\n"


def select_ffmpeg_asset(releases: list[dict]) -> dict:
    """The newest immutable autobuild release's highest release-branch asset."""
    dated = [rel for rel in releases if isinstance(rel, dict)
             and str(rel.get("tag_name", "")).startswith("autobuild-") and not rel.get("draft")]
    dated.sort(key=lambda rel: rel.get("published_at") or "", reverse=True)
    for rel in dated:
        candidates = []
        for asset in rel.get("assets") or []:
            match = FFMPEG_ASSET.match(str(asset.get("name", "")))
            if match:
                candidates.append(((int(match["major"]), int(match["minor"])), asset))
        if candidates:
            candidates.sort(key=lambda pair: pair[0], reverse=True)
            asset = candidates[0][1]
            digest = asset.get("digest")
            return {"release": rel["tag_name"], "asset": asset["name"], "url": asset["browser_download_url"],
                    "size": asset.get("size"),
                    "api_digest": digest.split(":", 1)[1] if isinstance(digest, str) and digest.startswith("sha256:") else None}
    raise RuntimeError("no autobuild release with a release-branch win64-gpl asset was found")


def _fetch(url: str, target: Path, headers: dict | None = None) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    request = Request(url, headers=headers or {})
    with urlopen(request, timeout=900) as response, target.open("wb") as out:
        shutil.copyfileobj(response, out)
    return target


def _github_json(url: str) -> list:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "bossman-windows-bundle-lock"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urlopen(Request(url, headers=headers), timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def _probe_ffmpeg(archive: Path, scratch: Path) -> dict:
    """The same encode → probe → decode the archive verifier runs, on the pinned build."""
    sys.path.insert(0, str(TOOLS))
    import verify_windows_bundle as verify
    home = scratch / "ffmpeg-probe"
    (home / "media").mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        for member in zf.namelist():
            name = Path(member).name.lower()
            if name in {"ffmpeg.exe", "ffprobe.exe"}:
                with zf.open(member) as src, (home / "media" / name).open("wb") as dst:
                    shutil.copyfileobj(src, dst)
    env = dict(os.environ)
    env["PATH"] = str(home / "media") + os.pathsep + env.get("PATH", "")
    problems, details = verify.check_media(home, env)
    if problems:
        raise RuntimeError("the pinned FFmpeg failed the encode/probe/decode test: " + "; ".join(problems))
    return details


def record(wheels: Path, dist: Path | None, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    recorded_at = datetime.now(timezone.utc).isoformat()
    version = platform.python_version()
    platform_tag = f"win_amd64 cp{sys.version_info.major}{sys.version_info.minor}"
    if os.name != "nt" or sys.version_info[:2] != (3, 12):
        raise RuntimeError(f"record the lock on the Windows 3.12 build runner, not {platform.platform()}")

    built = sorted(wheels.glob("*.whl"))
    if not built:
        raise RuntimeError(f"no Bossman wheels in {wheels}")
    import build_windows_bundle as builder  # the same extras the archive installs
    report_path = out / "pip-report.json"
    subprocess.run([sys.executable, "-m", "pip", "install", "--dry-run", "--ignore-installed",
                    "--report", str(report_path), *builder.pip_requirements(built)],
                   check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
    rows = requirements_from_report(json.loads(report_path.read_text(encoding="utf-8")))
    txt = requirements_text(rows, recorded_at=recorded_at, platform_tag=platform_tag)
    (out / LOCK_TXT.name).write_text(txt, encoding="utf-8")

    embed = _fetch(EMBED_URL.format(v=version), out / f"python-{version}-embed-amd64.zip")
    python_lock = {"version": version, "embeddable_url": EMBED_URL.format(v=version),
                   "embeddable_sha256": sha256_file(embed)}

    ffmpeg = select_ffmpeg_asset(_github_json(FFMPEG_RELEASES))
    archive = _fetch(ffmpeg["url"], out / ffmpeg["asset"])
    ffmpeg["sha256"] = sha256_file(archive)
    ffmpeg["size"] = archive.stat().st_size
    if ffmpeg["api_digest"] and ffmpeg["api_digest"] != ffmpeg["sha256"]:
        raise RuntimeError(f"FFmpeg asset digest mismatch: API {ffmpeg['api_digest']} vs downloaded {ffmpeg['sha256']}")
    ffmpeg["api_digest_matched"] = bool(ffmpeg["api_digest"]) and ffmpeg["api_digest"] == ffmpeg["sha256"]
    ffmpeg["probe"] = _probe_ffmpeg(archive, out)

    chromium = None
    if dist is not None:
        manifests = list(dist.glob("BOSSMAN-Windows-x64-*/MANIFEST.json"))
        if len(manifests) == 1:
            manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
            dirs = manifest.get("contents", {}).get("browser", {}).get("chromium_dirs") or []
            packages = {normalize(p.get("name", "")): p.get("version")
                        for p in manifest.get("contents", {}).get("runtime", {}).get("packages", [])}
            chromium = {"directories": dirs, "playwright": packages.get("playwright")}
    lock = {
        "schema_version": 1,
        "recorded_at": recorded_at,
        "recorded_from": {"run_id": os.environ.get("GITHUB_RUN_ID"),
                          "source_sha": os.environ.get("BOSSMAN_ACCEPTANCE_SHA") or os.environ.get("GITHUB_SHA")},
        "platform": {"os": "windows", "arch": "amd64", "python_tag": f"cp{sys.version_info.major}{sys.version_info.minor}"},
        "python": python_lock,
        "ffmpeg": ffmpeg,
        "chromium": chromium,
        "requirements": {"file": LOCK_TXT.name, "sha256": hashlib.sha256(txt.encode("utf-8")).hexdigest(),
                         "count": len(rows)},
    }
    (out / LOCK_JSON.name).write_text(json.dumps(lock, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("BOSSMAN_LOCK_JSON_BEGIN")
    print(json.dumps(lock, indent=2, ensure_ascii=False))
    print("BOSSMAN_LOCK_JSON_END")
    print("BOSSMAN_LOCK_TXT_BEGIN")
    print(txt, end="")
    print("BOSSMAN_LOCK_TXT_END")
    print(f"BOSSMAN_LOCK_RECORDED requirements={len(rows)} python={version} ffmpeg={ffmpeg['asset']}")
    return lock


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="resolve and print the lock on the Windows build runner")
    rec.add_argument("--wheels", type=Path, required=True, help="the Bossman wheels the build produced")
    rec.add_argument("--dist", type=Path, default=None, help="the --out of build_windows_bundle.py (for the Chromium directory)")
    rec.add_argument("--out", type=Path, required=True)
    sub.add_parser("show", help="print what the committed lock pins")
    args = parser.parse_args(argv)
    if args.command == "record":
        record(args.wheels, args.dist, args.out)
        return 0
    lock = load()
    if lock is None:
        print("BOSSMAN_BUILD_INPUTS=UNLOCKED (tools/windows_bundle_lock.json is absent)")
        return 1
    pins = lock.pop("_pins")
    lock.pop("_txt")
    print(json.dumps({**lock, "requirements": {**lock["requirements"], "pins": pins}}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
