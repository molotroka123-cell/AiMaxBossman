"""Licence notices the Windows bundle owes its recipients (findings of the 12.10 release scan).

``build_windows_bundle.py`` calls :func:`install_notices` once the browser, media and
``site-packages`` trees are final. Everything here is offline and deterministic. Licence
TEXT is only ever copied, never composed:

1. from a file already inside the output tree (installed package, Playwright browser), or
2. from the tracked ``packaging/licenses`` folder of the repository;

a text that is in neither place is written as ``NOT RECORDED`` together with the SPDX id and
the upstream URL, so the gap is visible in the archive instead of being papered over.

What is written (relative to the bundle root):

* ``LICENSES/ffmpeg/SOURCE_OFFER.md``: where the corresponding source of the GPL
  ``ffmpeg.exe``/``ffprobe.exe`` can be obtained, derived from the lock; a missing field is
  ``NOT RECORDED`` and fails the ``release`` profile;
* ``LICENSES/<component>/NOTICE.md`` (+ the text) for Chromium, Playwright's ``winldd``,
  the VS Code codicon font and the Silero VAD weights;
* ``LICENSES/python-distributions.md``: one row per installed distribution, and the
  canonical Apache-2.0 / GPL-3.0 text for a distribution that declares exactly that licence
  and ships no licence file of its own.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import re
import shutil
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping

NOT_RECORDED = "NOT RECORDED"
REPO_LICENSES = Path(__file__).resolve().parent.parent / "packaging" / "licenses"
BOSSMAN_SOURCE_REPOSITORY = "https://github.com/molotroka123-cell/AiMaxBossman"
SITE_PACKAGES_PARTS = ("runtime", "Lib", "site-packages")
SBOM_MAX_BYTES = 8 * 1024 * 1024

# Canonical, unmodified texts kept in packaging/licenses (see the README there). Only the
# first two can be attached to a Python distribution (their wording has no per-project line).
CANONICAL_TEXTS = {"Apache-2.0": "Apache-2.0.txt", "GPL-3.0": "GPL-3.0.txt",
                   "CC-BY-4.0": "CC-BY-4.0.txt"}
ATTACHABLE_TO_DISTRIBUTIONS = ("Apache-2.0", "GPL-3.0")


def _site(out: Path) -> Path:
    return out.joinpath(*SITE_PACKAGES_PARTS)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy(source: Path, target: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return _sha256(target)


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip() or "-"


# ================================================================ FFmpeg

FFMPEG_UPSTREAM_GIT = "https://git.ffmpeg.org/ffmpeg.git"
FFMPEG_UPSTREAM_COMMIT_URL = "https://git.ffmpeg.org/gitweb/ffmpeg.git/commit/{commit}"
FFMPEG_GITHUB_COMMIT_URL = "https://github.com/FFmpeg/FFmpeg/commit/{commit}"
FFMPEG_REQUIRED_FIELDS = ("version", "upstream_commit", "release_tag_url", "asset_sha256")

_RELEASE_ASSET_URL = re.compile(
    r"^(?P<repo>https://github\.com/[^/\s]+/[^/\s]+)/releases/download/(?P<tag>[^/\s]+)/(?P<asset>[^/\s]+)$")
_VERSION_IN_BANNER = re.compile(r"\bversion\s+(?P<version>\S+)")
_VERSION_IN_ASSET = re.compile(r"^ffmpeg-(?P<version>n\d+\.\d+-\d+-g[0-9a-f]{7,40})-win64-")
_UPSTREAM_COMMIT = re.compile(r"-g(?P<commit>[0-9a-f]{7,40})(?![0-9a-f])")


def ffmpeg_source_fields(lock: Mapping | None, media: Mapping | None) -> dict:
    """What the lock (or, unlocked, the downloaded asset) says about the bundled FFmpeg.

    A value that cannot be derived is ``None``; nothing is guessed or looked up. The exact
    version string comes from the lock's probe of the pinned binary, the upstream commit
    from the ``-g<hash>`` part of that version / of the asset name, the release tag and the
    build repository from the asset URL.
    """
    pin = dict((lock or {}).get("ffmpeg") or {})
    media = dict(media or {})
    asset_url = pin.get("url") or media.get("source") or None
    parsed = _RELEASE_ASSET_URL.match(asset_url or "")
    asset = pin.get("asset") or (parsed.group("asset") if parsed else None)
    banner = (pin.get("probe") or {}).get("ffmpeg") or None
    from_banner = _VERSION_IN_BANNER.search(banner or "")
    from_asset = _VERSION_IN_ASSET.match(asset or "")
    version = ((from_banner.group("version") if from_banner else None)
               or (from_asset.group("version") if from_asset else None))

    commits = []
    for text in (from_banner.group("version") if from_banner else "",
                 from_asset.group("version") if from_asset else ""):
        found = _UPSTREAM_COMMIT.search(text)
        if found:
            commits.append(found.group("commit"))
    commit = None
    if commits:
        longest = max(commits, key=len)
        if all(longest.startswith(item) for item in commits):  # a disagreement stays unrecorded
            commit = longest

    tag = (parsed.group("tag") if parsed else None) or pin.get("release") or None
    repo = parsed.group("repo") if parsed else None
    return {
        "version": version,
        "version_banner": banner,
        "asset": asset,
        "asset_url": asset_url,
        "asset_sha256": pin.get("sha256") or media.get("sha256") or None,
        "release_tag": tag,
        "release_tag_url": f"{repo}/releases/tag/{tag}" if repo and tag else None,
        "build_recipe_url": f"{repo}/tree/{tag}" if repo and tag else None,
        "upstream_commit": commit,
        "upstream_commit_url": FFMPEG_UPSTREAM_COMMIT_URL.format(commit=commit) if commit else None,
        "upstream_mirror_commit_url": FFMPEG_GITHUB_COMMIT_URL.format(commit=commit) if commit else None,
        "x264_revision": pin.get("x264") or None,
        "x265_revision": pin.get("x265") or None,
        "pinned_by_lock": bool(pin),
    }


def missing_ffmpeg_fields(fields: Mapping) -> list[str]:
    return [name for name in FFMPEG_REQUIRED_FIELDS if not fields.get(name)]


def render_ffmpeg_source_offer(fields: Mapping) -> str:
    def value(name: str) -> str:
        return str(fields.get(name) or NOT_RECORDED)

    def code(name: str) -> str:
        return f"`{value(name)}`" if fields.get(name) else NOT_RECORDED

    commit = fields.get("upstream_commit")
    clone = (f"git clone {FFMPEG_UPSTREAM_GIT}\n"
             f"git -C ffmpeg checkout {commit}") if commit else NOT_RECORDED
    return "\n".join([
        "# FFmpeg and ffprobe: where the corresponding source is",
        "",
        "`media/ffmpeg.exe` and `media/ffprobe.exe` in this archive are the unmodified `gpl` build of "
        "FFmpeg named below (GNU General Public License version 3; libx264, libx265 and other GPL "
        "libraries are linked into it; `media\\ffmpeg.exe -version` prints the exact configuration of "
        "the binary you hold). Their licence texts are under `LICENSES/ffmpeg/`. This file is the "
        "written pointer to the corresponding source required by GPLv3 section 6.",
        "",
        "## The build in this archive",
        "",
        f"* Version: {code('version')}",
        f"* Version banner recorded for the pinned binary: {code('version_banner')}",
        f"* Release asset: {code('asset')}",
        f"* Asset URL: {value('asset_url')}",
        f"* Asset SHA-256: {code('asset_sha256')}",
        f"* Build release tag: {code('release_tag')}",
        f"* Upstream FFmpeg commit: {code('upstream_commit')}",
        "",
        "## Where to get the source",
        "",
        "1. **FFmpeg itself**, exact commit:",
        f"   * {value('upstream_commit_url')}",
        f"   * {value('upstream_mirror_commit_url')}",
        "",
        "   ```",
        *(f"   {line}" for line in clone.splitlines()),
        "   ```",
        "",
        "2. **The build recipe** (the scripts, patches and library revisions this binary was "
        "built from) is the repository that produced the release asset, at the release tag:",
        f"   * release: {value('release_tag_url')}",
        f"   * tree at the tag: {value('build_recipe_url')}",
        "",
        "3. **Libraries linked into the binary**, among them x264 and x265. Their revisions are set "
        "by the build recipe in step 2 and are not recorded in `tools/windows_bundle_lock.json`:",
        f"   * x264 revision: {value('x264_revision')}",
        f"   * x265 revision: {value('x265_revision')}",
        "",
        "## If a link stops working",
        "",
        "Bossman itself is AGPL-3.0-only (`LICENSE`); FFmpeg, ffprobe, x264 and x265 stay under their "
        "own GPL terms. Any recipient of this archive may request the complete corresponding source "
        "of these binaries from the Bossman maintainers at "
        f"{BOSSMAN_SOURCE_REPOSITORY} (see `LICENSES/SOURCE.md`); the request is answered with "
        "the source of exactly the build named above.",
        "",
    ])


def write_ffmpeg_source_offer(licenses: Path, lock: Mapping | None, media: Mapping | None, *,
                              profile: str) -> dict:
    """Write ``LICENSES/ffmpeg/SOURCE_OFFER.md``; an incomplete offer fails the release profile."""
    if not (media or {}).get("ffmpeg"):
        return {"status": "NOT_APPLICABLE", "reason": "no ffmpeg binary in this archive", "missing": []}
    fields = ffmpeg_source_fields(lock, media)
    missing = missing_ffmpeg_fields(fields)
    _write(licenses / "ffmpeg" / "SOURCE_OFFER.md", render_ffmpeg_source_offer(fields))
    if missing and profile == "release":
        raise RuntimeError(
            "release profile: LICENSES/ffmpeg/SOURCE_OFFER.md cannot name the corresponding source of the "
            f"GPL ffmpeg/ffprobe binaries ({NOT_RECORDED}: {', '.join(missing)}). Record the FFmpeg release "
            "asset in tools/windows_bundle_lock.json (python tools/windows_bundle_lock.py record).")
    return {"status": "COMPLETE" if not missing else "INCOMPLETE", "missing": missing, "fields": fields}


# ================================================================ component notices

_CREDITS_NAME = re.compile(r"(?i)^(licen[cs]e|copying|credits|chrome_credits)([._-].*)?$")
_BROWSER_VERSION = re.compile(r"\d+(?:\.\d+){3}")


def _notice_md(title: str, rows: Iterable[tuple[str, str]], paragraphs: Iterable[str]) -> str:
    lines = [f"# {title}", ""]
    lines += [f"* **{label}**: {text}" for label, text in rows]
    for paragraph in paragraphs:
        lines += ["", paragraph]
    return "\n".join(lines) + "\n"


def _chromium(out: Path, licenses: Path, licenses_src: Path) -> dict:
    browser = out / "browser"
    directories = sorted(p for p in browser.glob("chromium-*") if p.is_dir()) if browser.is_dir() else []
    if not directories:
        return {"status": "NOT_PRESENT"}
    entries = []
    for directory in directories:
        revision = directory.name.partition("-")[2]
        exe_dirs = sorted({p.parent for p in directory.rglob("chrome.exe")})
        target = exe_dirs[0] if exe_dirs else directory
        version = next((p.stem for p in sorted(target.glob("*.manifest"))
                        if _BROWSER_VERSION.fullmatch(p.stem)), None)
        shipped = sorted(p for p in target.iterdir() if p.is_file() and _CREDITS_NAME.match(p.name))
        source, notice, how = None, None, None
        if shipped:
            notice, how = shipped[0], "shipped in the browser directory by the browser build"
        else:
            candidates = [*sorted(browser.glob(f"chromium_headless_shell-{revision}/**/LICENSE.headless_shell")),
                          *sorted(browser.glob("chromium_headless_shell-*/**/LICENSE.headless_shell"))]
            fallback = licenses_src / "chromium" / "LICENSE.chromium"
            source = next(iter(candidates), None) or (fallback if fallback.is_file() else None)
            if source is not None:
                notice = target / "LICENSE.chromium"
                _copy(source, notice)
                how = ("copied from " + (_rel(source, out) if source.is_relative_to(out)
                                         else f"packaging/licenses/chromium/{source.name}"))
        entries.append({"directory": _rel(directory, out), "revision": revision,
                        "chromium_version": version, "notice": _rel(notice, out) if notice else None,
                        "how": how, "sha256": _sha256(notice) if notice else None})
    recorded = all(item["notice"] for item in entries)
    paragraphs = []
    for item in entries:
        if item["notice"]:
            paragraphs.append(
                f"`{item['directory']}`: Chromium {item['chromium_version'] or NOT_RECORDED}, "
                f"licence and third-party credits file `{item['notice']}` ({item['how']}; "
                f"SHA-256 `{item['sha256']}`).")
        else:
            paragraphs.append(f"`{item['directory']}`: licence text not bundled - {NOT_RECORDED}.")
    paragraphs.append(
        "The credits file is the one the same Chromium revision ships with its headless-shell build "
        "(`browser/chromium_headless_shell-*`). The full browser's own credits page is built into the "
        "browser (`chrome://credits`, see the `ABOUT` file next to `chrome.exe`).")
    _write(licenses / "chromium" / "NOTICE.md", _notice_md(
        "Chromium (Chrome for Testing build installed by Playwright)",
        [("SPDX", "BSD-3-Clause AND third-party components listed in the credits file"),
         ("Upstream", "https://www.chromium.org/"),
         ("Status", "RECORDED" if recorded else NOT_RECORDED)], paragraphs))
    return {"status": "RECORDED" if recorded else NOT_RECORDED, "spdx": "BSD-3-Clause", "entries": entries}


def _playwright_package(site: Path) -> dict:
    package = site / "playwright" / "driver" / "package"
    info: dict = {"license": None, "notice": None, "version": None, "repository": None}
    license_file = package / "LICENSE"
    if not license_file.is_file():
        candidates = sorted(site.glob("playwright-*.dist-info/licenses/LICENSE"))
        license_file = candidates[0] if candidates else license_file
    if license_file.is_file():
        info["license"] = license_file
    if (package / "NOTICE").is_file():
        info["notice"] = package / "NOTICE"
    try:
        data = json.loads((package / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    info["version"] = data.get("version")
    repository = (data.get("repository") or {}).get("url") if isinstance(data.get("repository"), dict) else None
    if isinstance(repository, str):
        info["repository"] = re.sub(r"\.git$", "", re.sub(r"^git\+", "", repository))
    return info


def _winldd(out: Path, licenses: Path, licenses_src: Path) -> dict:
    browser = out / "browser"
    directories = sorted(p for p in browser.glob("winldd-*") if p.is_dir()) if browser.is_dir() else []
    if not directories:
        return {"status": "NOT_PRESENT"}
    package = _playwright_package(_site(out))
    text = package["license"]
    origin = "the installed Playwright package"
    if text is None and (licenses_src / "playwright" / "LICENSE").is_file():
        text, origin = licenses_src / "playwright" / "LICENSE", "packaging/licenses/playwright/LICENSE"
    entries = []
    for directory in directories:
        copied = []
        if text is not None:
            _copy(text, directory / "LICENSE")
            copied.append(_rel(directory / "LICENSE", out))
            if package["notice"] is not None:
                _copy(package["notice"], directory / "NOTICE")
                copied.append(_rel(directory / "NOTICE", out))
        entries.append({"directory": _rel(directory, out), "files": copied})
    recorded = text is not None
    _write(licenses / "playwright-winldd" / "NOTICE.md", _notice_md(
        "Playwright winldd helper (PrintDeps.exe)",
        [("SPDX", "Apache-2.0"),
         ("Upstream", package["repository"] or NOT_RECORDED),
         ("Playwright package", str(package["version"] or NOT_RECORDED)),
         ("Status", "RECORDED" if recorded else NOT_RECORDED)],
        ["The helper is part of the Playwright project and is covered by the Playwright licence. "
         + (f"Its text is copied next to the helper (`"
            + "`, `".join(f"{item['directory']}/LICENSE" for item in entries) + f"`) from {origin}."
            if recorded else f"Licence text not bundled - {NOT_RECORDED}.")]))
    return {"status": "RECORDED" if recorded else NOT_RECORDED, "spdx": "Apache-2.0", "entries": entries}


def _codicon(out: Path, licenses: Path, licenses_src: Path) -> dict:
    site = _site(out)
    fonts = sorted({_rel(p, out) for p in site.rglob("codicon*.ttf")}) if site.is_dir() else []
    if not fonts:
        return {"status": "NOT_PRESENT"}
    text = licenses_src / CANONICAL_TEXTS["CC-BY-4.0"]
    full = text.is_file()
    if full:
        _copy(text, licenses / "codicon" / "CC-BY-4.0.txt")
    _write(licenses / "codicon" / "NOTICE.md", _notice_md(
        "VS Code codicon font (inside the Playwright driver package)",
        [("Attribution", "Copyright (c) Microsoft Corporation, CC-BY-4.0, "
                         "https://github.com/microsoft/vscode-codicons"),
         ("SPDX", "CC-BY-4.0"),
         ("Licence text", "`LICENSES/codicon/CC-BY-4.0.txt`" if full
          else f"not bundled - {NOT_RECORDED}"),
         ("Status", "RECORDED" if full else f"{NOT_RECORDED} (attribution only)")],
        ["Font files, shipped as received from the Playwright package and not modified:",
         "\n".join(f"* `{path}`" for path in fonts)]))
    return {"status": "RECORDED" if full else NOT_RECORDED, "spdx": "CC-BY-4.0", "files": fonts}


def _silero(out: Path, licenses: Path, licenses_src: Path) -> dict:
    site = _site(out)
    models = sorted(site.rglob("silero_vad*.onnx")) if site.is_dir() else []
    if not models:
        return {"status": "NOT_PRESENT"}
    shipped = [_rel(p, out) for p in models]
    texts = sorted((licenses_src / "silero-vad").glob("LICENSE*")) if (licenses_src / "silero-vad").is_dir() else []
    recorded = bool(texts)
    if recorded:
        _copy(texts[0], licenses / "silero-vad" / "LICENSE")
    owner_dist = None
    for path in models:
        top = path.relative_to(site).parts[0]
        found = sorted(site.glob(f"{top}-*.dist-info"))
        if found:
            owner_dist = found[0].name[:-len(".dist-info")]
            break
    _write(licenses / "silero-vad" / "NOTICE.md", _notice_md(
        "Silero VAD weights",
        [("SPDX", "MIT"),
         ("Upstream", "https://github.com/snakers4/silero-vad"),
         ("Shipped by", owner_dist or NOT_RECORDED),
         ("Status", "RECORDED" if recorded else NOT_RECORDED)],
        ["Model files:", "\n".join(f"* `{path}`" for path in shipped),
         ("Licence text copied to `LICENSES/silero-vad/LICENSE`." if recorded else
          f"Licence text not bundled - {NOT_RECORDED}. The text of the upstream project's LICENSE for "
          "this model version was not available offline; add it as "
          "`packaging/licenses/silero-vad/LICENSE` and rebuild.")]))
    return {"status": "RECORDED" if recorded else NOT_RECORDED, "spdx": "MIT", "files": shipped}


def install_component_notices(out: Path, *, licenses_src: Path = REPO_LICENSES) -> dict:
    licenses = out / "LICENSES"
    return {"chromium": _chromium(out, licenses, licenses_src),
            "playwright_winldd": _winldd(out, licenses, licenses_src),
            "codicon": _codicon(out, licenses, licenses_src),
            "silero_vad": _silero(out, licenses, licenses_src)}


# ================================================================ python distributions

_LICENSE_FILE = re.compile(r"(?i)^(licen[cs]e|copying|unlicense)([._-].*)?$")
_LICENSE_DIRS = {"licenses", "licences", "license_files"}


def _norm_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _licence_files(site: Path, info: Path) -> list[str]:
    """Licence files of one distribution (paths relative to ``site-packages``)."""
    found: set[str] = set()
    for path in info.rglob("*"):
        if not path.is_file():
            continue
        parents = path.relative_to(info).parts[:-1]
        if _LICENSE_FILE.match(path.name) or any(part.lower() in _LICENSE_DIRS for part in parents):
            found.add(_rel(path, site))
    record = info / "RECORD"
    if record.is_file():
        text = record.read_text(encoding="utf-8", errors="replace")
        for row in csv.reader(io.StringIO(text)):
            if not row:
                continue
            parts = PurePosixPath(row[0]).parts
            if (len(parts) == 2 and ".." not in parts and _LICENSE_FILE.match(parts[1])
                    and (site / row[0]).is_file()):
                found.add(PurePosixPath(*parts).as_posix())
    return sorted(found)


def _sbom_expressions(info: Path, name: str) -> list[str]:
    folder = info / "sboms"
    expressions: list[str] = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            if path.stat().st_size > SBOM_MAX_BYTES:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for component in (data.get("components") or []) if isinstance(data, dict) else []:
            if not isinstance(component, dict) or _norm_name(str(component.get("name", ""))) != name:
                continue
            for item in component.get("licenses") or []:
                if isinstance(item, dict):
                    expression = item.get("expression") or (item.get("license") or {}).get("id")
                    if expression:
                        expressions.append(str(expression))
    return expressions


def _family(token: str) -> str:
    """Coarse class of one declared licence string; only exact Apache-2.0 / GPL-3 are named."""
    text = re.sub(r"[\s_]+", " ", token.strip().lower())
    if text in {"", "unknown", "osi approved"}:
        return "noise"
    plain = re.sub(r"[-,()]", " ", text)
    plain = re.sub(r"\s+", " ", plain).strip()
    if re.fullmatch(r"apache( software)?( license)?( version)? ?2(\.0)?( license)?", plain):
        return "apache-2.0"
    if plain in {"apache software license", "apache license", "apache"}:
        return "apache-unspecified"
    if (("gpl" in text or "general public license" in text)
            and not re.search(r"lgpl|lesser|affero|agpl|library|v2|2\.0|version 2", text)
            and re.search(r"v3|3\.0|version 3|gplv3|gpl 3|gpl-3", text)):
        return "gpl-3.0"
    return "other"


def canonical_licence(tokens: Iterable[str]) -> str | None:
    """``Apache-2.0`` / ``GPL-3.0`` when everything a distribution declares says exactly that."""
    families = {_family(token) for token in tokens if token}
    families.discard("noise")
    if families and families <= {"apache-2.0", "apache-unspecified"} and "apache-2.0" in families:
        return "Apache-2.0"
    if families == {"gpl-3.0"}:
        return "GPL-3.0"
    return None


def _distribution(site: Path, info: Path) -> dict:
    metadata = info / "METADATA"
    text = metadata.read_text(encoding="utf-8", errors="replace") if metadata.is_file() else ""
    message = Parser().parsestr(text, headersonly=True)
    stem = info.name[:-len(".dist-info")]
    name_hint, _, version_hint = stem.rpartition("-")
    name = (message.get("Name") or name_hint or stem).strip()
    version = (message.get("Version") or version_hint).strip()

    expression = " ".join((message.get("License-Expression") or "").split())
    field = " ".join((message.get("License") or "").split())
    if field.upper() == "UNKNOWN":
        field = ""
    classifiers = [c.split("::")[-1].strip() for c in message.get_all("Classifier", [])
                   if c.strip().startswith("License ::")]
    classifiers = [c for c in classifiers if c.lower() != "osi approved"]
    declared = expression or field or "; ".join(classifiers) or "UNKNOWN"
    if len(declared) > 80:
        declared = declared[:60].rstrip() + " ... (full text in METADATA)"

    upstream = " ".join((message.get("Home-page") or "").split())
    if not upstream:
        urls = [u.partition(",")[2].strip() for u in message.get_all("Project-URL", []) if "," in u]
        upstream = urls[0] if urls else ""
    tokens = [expression, field if len(field) <= 80 else "", *classifiers,
              *_sbom_expressions(info, _norm_name(name))]
    return {"name": name, "version": version, "licence": declared, "upstream": upstream or None,
            "info": info, "files": _licence_files(site, info),
            "canonical": canonical_licence(tokens), "added": None}


def _append_record(info: Path, relative: str, data: bytes) -> None:
    record = info / "RECORD"
    if not record.is_file():
        return
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
    current = record.read_bytes()
    eol = b"\r\n" if b"\r\n" in current else b"\n"
    if current and not current.endswith(b"\n"):
        current += eol
    record.write_bytes(current + f"{relative},sha256={digest},{len(data)}".encode("utf-8") + eol)


def python_distribution_rows(site: Path) -> list[dict]:
    if not site.is_dir():
        return []
    infos = sorted(site.glob("*.dist-info"), key=lambda p: (p.name.lower(), p.name))
    return [_distribution(site, info) for info in infos if info.is_dir()]


def install_python_distributions(out: Path, *, licenses_src: Path = REPO_LICENSES) -> dict:
    """``LICENSES/python-distributions.md`` for ALL distributions; texts for the ones that ship none."""
    site = _site(out)
    rows = python_distribution_rows(site)
    for row in rows:
        if row["files"]:
            continue
        # A project's OWN licence file (per-project copyright line, MIT/BSD) kept byte for byte under
        # packaging/licenses/dist/<normalised name>/LICENSE*: the real upstream text, never a template.
        own_dir = licenses_src / "dist" / _norm_name(row["name"])
        own = sorted(own_dir.glob("LICENSE*")) if own_dir.is_dir() else []
        if own:
            target = row["info"] / "licenses" / "LICENSE"
            _copy(own[0], target)
            relative = _rel(target, site)
            _append_record(row["info"], relative, target.read_bytes())
            row["files"] = [relative]
            row["added"] = f"upstream licence file packaging/licenses/dist/{own_dir.name}/{own[0].name}"
            continue
        if row["canonical"] not in ATTACHABLE_TO_DISTRIBUTIONS:
            continue
        source = licenses_src / CANONICAL_TEXTS[row["canonical"]]
        if not source.is_file():
            continue
        target = row["info"] / "licenses" / "LICENSE"
        _copy(source, target)
        relative = _rel(target, site)
        _append_record(row["info"], relative, target.read_bytes())
        row["files"], row["added"] = [relative], f"{row['canonical']} text from packaging/licenses/{source.name}"

    present = [row for row in rows if row["files"]]
    added = [row for row in rows if row["added"]]
    missing = [row for row in rows if not row["files"]]
    lines = [
        "# Python distributions in `runtime/Lib/site-packages`",
        "",
        f"{len(rows)} distributions; {len(present)} carry a licence file "
        f"({len(added)} of them were given the canonical text of the licence they declare, "
        f"because the distribution ships none); {len(missing)} have no licence file in the bundle "
        f"and are marked {NOT_RECORDED}.",
        "",
        "The licence column is what the distribution's own METADATA declares "
        "(`License-Expression`, else `License`, else the licence classifiers).",
        "",
        "| distribution | version | licence (METADATA) | licence file in the bundle | upstream |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        if row["added"]:
            status = f"yes - added by the build: {row['added']}"
        elif row["files"]:
            status = "yes"
        else:
            status = f"no - licence text not bundled, {NOT_RECORDED}"
        lines.append("| " + " | ".join(_cell(item) for item in (
            row["name"], row["version"], row["licence"], status, row["upstream"] or "-")) + " |")
    lines.append("")
    _write(out / "LICENSES" / "python-distributions.md", "\n".join(lines))
    return {"table": "LICENSES/python-distributions.md", "total": len(rows),
            "with_licence_file": len(present),
            "added_by_build": sorted(f"{row['name']}=={row['version']}" for row in added),
            "not_recorded": sorted(f"{row['name']}=={row['version']}" for row in missing)}


# ================================================================ orchestration

def install_notices(out: Path, *, lock: Mapping | None, media: Mapping | None, profile: str,
                    licenses_src: Path = REPO_LICENSES) -> dict:
    """Every notice of the 12.10 scan, in the order that lets the release profile fail early."""
    offer = write_ffmpeg_source_offer(out / "LICENSES", lock, media, profile=profile)
    return {"ffmpeg_source_offer": offer,
            "components": install_component_notices(out, licenses_src=licenses_src),
            "python_distributions": install_python_distributions(out, licenses_src=licenses_src)}
