"""Findings of the 12.10 release scan, pinned at the builder's own functions.

No network, no Windows runner and no real build: every test works on a tiny synthetic
output tree in ``tmp_path``. What they protect:

* the archive carries no builder path (``browser/.links`` is gone, no shipped text file
  names the build output directory or the user profile);
* README.md is listed in MANIFEST.json and nothing else is unlisted;
* a zero-byte ``*.whl`` is not shipped;
* the GPL FFmpeg's corresponding-source pointer is derived from the lock, and a missing
  field is ``NOT RECORDED`` and fails the release profile;
* licence notices are copied, never composed: an absent text is ``NOT RECORDED``.
"""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
import os
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
import build_windows_bundle as bundle  # noqa: E402
import windows_bundle_hygiene as hygiene  # noqa: E402
import windows_bundle_lock as lockmod  # noqa: E402
import windows_bundle_notices as notices  # noqa: E402

WHEN = "2026-10-12T00:00:00+00:00"
SHA = "a" * 40
SCANNER_FFMPEG_POINTER = re.compile(
    r"(github\.com/FFmpeg|git\.ffmpeg\.org|ffmpeg\.org/releases|BtbN/FFmpeg-Builds/(?:tree|blob|commit))")


def _put(path: Path, content: bytes | str = b"") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
    return path


def _bundle_tree(tmp_path: Path) -> tuple[Path, Path]:
    """(output root, bundle directory) with the minimum a manifest needs."""
    root = tmp_path / "dist"
    out = root / "BOSSMAN-Windows-x64-aaaaaaaaaaaa"
    _put(out / "Start-Bossman.cmd", "@echo off\r\n")
    _put(out / "LICENSES" / "SOURCE.md", "# source\n")
    return root, out


def _seal(root: Path, out: Path, tmp_path: Path, **overrides):
    arguments = dict(sha=SHA, when=WHEN, contents={"runtime": {}}, required=[], lock=None, profile="release",
                     builder_paths={"build output directory": root,
                                    "user profile directory": tmp_path / "profile"})
    arguments.update(overrides)
    return bundle.seal_output(out, **arguments)


def _listed(out: Path) -> set[str]:
    return {item["path"] for item in json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))["files"]}


# ================================================================ 1. no builder path in the archive

def test_playwright_links_directory_is_deleted_before_the_manifest_is_computed(tmp_path) -> None:
    root, out = _bundle_tree(tmp_path)
    # What the 12.10 archive carried: the build-time driver package path, as one link file.
    _put(out / "browser" / ".links" / ("9" * 40),
         str(out / "runtime" / "Lib" / "site-packages" / "playwright" / "driver" / "package"))
    _put(out / "browser" / "chromium-1243" / "INSTALLATION_COMPLETE")

    manifest, files = _seal(root, out, tmp_path)

    assert not (out / "browser" / ".links").exists()
    listed = _listed(out)
    assert not any(".links" in path for path in listed)
    assert "browser/chromium-1243/INSTALLATION_COMPLETE" in listed, "only the link cache goes"
    assert manifest["contents"]["hygiene"]["playwright_links_removed"] == 1
    assert "9" * 40 not in (out / "SHA256SUMS").read_text(encoding="utf-8")
    assert {item["path"] for item in files} == listed


@pytest.mark.parametrize("spelling", ["native", "forward", "json-escaped", "upper"])
def test_a_build_directory_path_in_a_shipped_text_file_fails_the_build_before_the_manifest(
        tmp_path, spelling) -> None:
    root, out = _bundle_tree(tmp_path)
    leaked = str(root / "_work" / "wheels")
    text = {"native": leaked, "forward": leaked.replace("\\", "/"),
            "json-escaped": leaked.replace("\\", "\\\\"), "upper": leaked.upper()}[spelling]
    _put(out / "app-support" / "settings.json", '{"wheel_dir": "' + text + '"}')

    with pytest.raises(RuntimeError) as refused:
        _seal(root, out, tmp_path)

    assert "app-support/settings.json" in str(refused.value)
    assert "build output directory" in str(refused.value)
    assert not (out / "MANIFEST.json").exists(), "the assertion must run before the manifest is written"
    assert not (out / "SHA256SUMS").exists()


def test_the_user_profile_path_in_a_shipped_text_file_fails_the_build(tmp_path) -> None:
    root, out = _bundle_tree(tmp_path)
    profile = tmp_path / "profile"
    _put(out / "docs" / "note.md", f"see {profile}{'/' if '/' in str(profile) else chr(92)}Bossman\n")
    with pytest.raises(RuntimeError) as refused:
        _seal(root, out, tmp_path)
    assert "docs/note.md" in str(refused.value) and "user profile directory" in str(refused.value)


def _with_site_packages(out: Path, profile: Path) -> dict:
    """Two installed distributions, each with one text file that names the builder's profile."""
    site = out / "runtime" / "Lib" / "site-packages"
    _put(site / "numpy-2.5.3.dist-info" / "RECORD",
         "numpy/__config__.py,sha256=x,1\r\nnumpy-2.5.3.dist-info/RECORD,,\r\n")
    _put(site / "numpy" / "__config__.py", f'include = "{profile}/AppData/Local/Temp/cibw-run"\n')
    _put(site / "bossman_core-0.3.0.dist-info" / "RECORD", "bcc/build_info.py,sha256=y,1\n")
    _put(site / "bcc" / "build_info.py", f'checkout = "{profile}/Bossman"\n')
    return {"runtime": {"packages": [{"name": "numpy", "version": "2.5.3"},
                                     {"name": "bossman-core", "version": "0.3.0"}],
                        "bossman_wheels": ["bossman_core-0.3.0-py3-none-any.whl"]}}


def test_upstream_wheel_files_are_exempt_from_the_profile_check_only(tmp_path) -> None:
    """numpy/scipy wheels built on a GitHub runner print that runner's profile in __config__.py."""
    root, out = _bundle_tree(tmp_path)
    profile = tmp_path / "profile"
    contents = _with_site_packages(out, profile)
    # the first-party file naming the profile is still a leak ...
    with pytest.raises(RuntimeError) as refused:
        _seal(root, out, tmp_path, contents=contents)
    message = str(refused.value)
    assert "bcc/build_info.py" in message and "numpy/__config__.py" not in message

    # ... and once it is gone the upstream file does not fail the build
    (out / "runtime" / "Lib" / "site-packages" / "bcc" / "build_info.py").write_text("ok\n", encoding="utf-8")
    manifest, _ = _seal(root, out, tmp_path, contents=contents)
    scan = manifest["contents"]["hygiene"]["path_leak_scan"]
    assert scan["result"] == "PASS" and scan["exempt_matches"] == 1
    assert scan["third_party_files_exempt_from_profile_check"] == 1


def test_a_build_directory_path_inside_an_upstream_wheel_file_is_still_a_leak(tmp_path) -> None:
    root, out = _bundle_tree(tmp_path)
    contents = _with_site_packages(out, tmp_path / "profile")
    (out / "runtime" / "Lib" / "site-packages" / "bcc" / "build_info.py").write_text("ok\n", encoding="utf-8")
    _put(out / "runtime" / "Lib" / "site-packages" / "numpy" / "__config__.py", f'wheel = "{root}"\n')
    with pytest.raises(RuntimeError) as refused:
        _seal(root, out, tmp_path, contents=contents)
    assert "numpy/__config__.py" in str(refused.value) and "build output directory" in str(refused.value)


def test_the_leak_scan_reads_only_small_text_files(tmp_path) -> None:
    root = tmp_path / "tree"
    needle = tmp_path / "builder" / "output"
    _put(root / "text.json", json.dumps({"p": str(needle)}))
    _put(root / "blob.bin", b"\x00\x01" + str(needle).encode() + b"\x00")       # binary: skipped
    _put(root / "big.txt", str(needle).encode() + b" " + b"x" * 2000)          # over the cap: skipped
    _put(root / "clean.txt", "nothing here\n")
    report = hygiene.find_path_leaks(root, {"builder": needle}, max_bytes=1000)
    assert report["leaks"] == [("text.json", "builder")]
    assert report["skipped_binary"] == 1 and report["skipped_large"] == 1 and report["scanned"] == 2
    # a path that is not present anywhere passes, and a too-short needle is never searched
    assert hygiene.assert_no_path_leaks(root, {"elsewhere": tmp_path / "nope" / "nowhere"})["scanned"] == 3
    assert hygiene.find_path_leaks(root, {"root": "/"})["leaks"] == []


# ================================================================ 5. README is in the manifest

def test_readme_is_listed_in_the_manifest_and_in_sha256sums(tmp_path) -> None:
    root, out = _bundle_tree(tmp_path)
    _seal(root, out, tmp_path)
    assert (out / "README.md").is_file()
    assert "README.md" in _listed(out)
    sums = (out / "SHA256SUMS").read_text(encoding="utf-8")
    digest = hashlib.sha256((out / "README.md").read_bytes()).hexdigest()
    assert f"{digest}  README.md" in sums
    assert hygiene.unlisted_files(out, _listed(out)) == []


def test_readme_is_written_before_the_file_list_and_the_manifest_in_the_sealing_order() -> None:
    source = inspect.getsource(bundle.seal_output)
    assert (source.index("README.md") < source.index("hygiene.finalize_tree(")
            < source.index("shipped_files(out)") < source.index('"MANIFEST.json"')
            < source.index("assert_manifest_complete("))


def test_every_file_except_the_two_self_entries_must_be_listed(tmp_path) -> None:
    _, out = _bundle_tree(tmp_path)
    files = bundle.shipped_files(out)
    assert hygiene.assert_manifest_complete(out, files) == len(files)

    _put(out / "MANIFEST.json", "{}")          # the two self-entries are exempt
    _put(out / "SHA256SUMS", "")
    assert hygiene.assert_manifest_complete(out, files) == len(files)

    _put(out / "README.md", "written after the manifest was computed\n")
    with pytest.raises(RuntimeError) as refused:
        hygiene.assert_manifest_complete(out, files)
    assert "README.md" in str(refused.value)

    (out / "README.md").unlink()
    ghost = files + [{"path": "gone.txt", "bytes": 0, "sha256": "0" * 64}]
    with pytest.raises(RuntimeError) as refused:
        hygiene.assert_manifest_complete(out, ghost)
    assert "gone.txt" in str(refused.value)


# ================================================================ 6. no zero-byte wheel

def test_zero_byte_wheels_are_dropped_and_none_remain(tmp_path) -> None:
    site = tmp_path / "runtime" / "Lib" / "site-packages"
    _put(site / "scipy-1.18.1-cp312-cp312-win_amd64.whl", b"")
    _put(site / "pkg" / "_vendor" / "nested-1.0-py3-none-any.whl", b"")
    _put(site / "real-1.0-py3-none-any.whl", b"PK\x03\x04")
    _put(site / "empty.txt", b"")
    assert hygiene.zero_byte_wheels(site) == ["pkg/_vendor/nested-1.0-py3-none-any.whl",
                                              "scipy-1.18.1-cp312-cp312-win_amd64.whl"]

    removed = hygiene.drop_zero_byte_wheels(site)

    assert removed == ["pkg/_vendor/nested-1.0-py3-none-any.whl", "scipy-1.18.1-cp312-cp312-win_amd64.whl"]
    assert hygiene.zero_byte_wheels(site) == []
    assert (site / "real-1.0-py3-none-any.whl").is_file(), "a non-empty wheel is not this check's business"
    assert (site / "empty.txt").is_file(), "only *.whl files are removed"
    assert hygiene.drop_zero_byte_wheels(site) == [], "idempotent"


def test_sealing_a_tree_with_an_empty_wheel_ships_and_lists_no_empty_wheel(tmp_path) -> None:
    root, out = _bundle_tree(tmp_path)
    _put(out / "runtime" / "Lib" / "site-packages" / "scipy-1.18.1-cp312-cp312-win_amd64.whl", b"")
    manifest, _ = _seal(root, out, tmp_path)
    assert not any(path.endswith(".whl") for path in _listed(out))
    assert manifest["contents"]["hygiene"]["zero_byte_wheels_removed"] == [
        "scipy-1.18.1-cp312-cp312-win_amd64.whl"]


def test_assemble_and_main_hand_the_profile_to_the_notices_step() -> None:
    assert inspect.signature(bundle.assemble).parameters["profile"].default == "release"
    assert "profile=args.profile" in inspect.getsource(bundle.main)
    assert "notices.install_notices(" in inspect.getsource(bundle.assemble)


# ================================================================ 2. FFmpeg corresponding source

LOCK = lockmod.load()
MEDIA = {"ffmpeg": "media/ffmpeg.exe", "ffprobe": "media/ffprobe.exe"}


def test_the_committed_lock_yields_a_complete_source_offer(tmp_path) -> None:
    assert LOCK is not None
    pin = LOCK["ffmpeg"]
    report = notices.write_ffmpeg_source_offer(tmp_path / "LICENSES", LOCK, MEDIA, profile="release")
    assert report["status"] == "COMPLETE" and report["missing"] == []
    text = (tmp_path / "LICENSES" / "ffmpeg" / "SOURCE_OFFER.md").read_text(encoding="utf-8")

    commit = re.search(r"-g([0-9a-f]{7,40})-win64-", pin["asset"]).group(1)
    assert pin["probe"]["ffmpeg"] in text, "the exact version string comes from the lock"
    assert pin["asset"] in text and pin["sha256"] in text and pin["url"] in text
    assert f"https://git.ffmpeg.org/gitweb/ffmpeg.git/commit/{commit}" in text
    assert f"https://github.com/BtbN/FFmpeg-Builds/releases/tag/{pin['release']}" in text
    assert f"https://github.com/BtbN/FFmpeg-Builds/tree/{pin['release']}" in text
    assert SCANNER_FFMPEG_POINTER.search(text)
    assert "x264" in text and "x265" in text
    assert "AGPL-3.0-only" in text and "may request" in text
    # Only what the lock does not record is marked: the x264/x265 revisions, nothing else.
    assert text.count(notices.NOT_RECORDED) == 2


def _lock_with_ffmpeg(**changes) -> dict:
    lock = copy.deepcopy(LOCK)
    for key, value in changes.items():
        if value is None:
            lock["ffmpeg"].pop(key, None)
        else:
            lock["ffmpeg"][key] = value
    return lock


ROLLING = {
    "asset": "ffmpeg-master-latest-win64-gpl.zip",
    "url": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
    "probe": None,
}


def test_a_missing_lock_field_is_not_recorded_and_fails_the_release_profile(tmp_path) -> None:
    lock = _lock_with_ffmpeg(**ROLLING)
    licenses = tmp_path / "LICENSES"
    with pytest.raises(RuntimeError) as refused:
        notices.write_ffmpeg_source_offer(licenses, lock, MEDIA, profile="release")
    message = str(refused.value)
    assert "NOT RECORDED" in message and "version" in message and "upstream_commit" in message
    assert "release profile" in message

    # The file is still written, so the reason is readable next to the build log.
    text = (licenses / "ffmpeg" / "SOURCE_OFFER.md").read_text(encoding="utf-8")
    assert "NOT RECORDED" in text
    assert "ffmpeg-master-latest-win64-gpl.zip" in text, "what IS known stays visible"
    assert "git.ffmpeg.org/gitweb/ffmpeg.git/commit/" not in text, "no commit URL is invented"


def test_the_diagnostic_profile_records_the_gap_without_failing(tmp_path) -> None:
    lock = _lock_with_ffmpeg(**ROLLING)
    report = notices.write_ffmpeg_source_offer(tmp_path / "LICENSES", lock, MEDIA, profile="diagnostic")
    assert report["status"] == "INCOMPLETE"
    assert {"version", "upstream_commit"} <= set(report["missing"])


@pytest.mark.parametrize("field", ["url", "sha256"])
def test_every_required_lock_field_is_enforced(tmp_path, field) -> None:
    lock = _lock_with_ffmpeg(**{field: None})
    media = dict(MEDIA)  # no fallback from the downloaded asset either
    with pytest.raises(RuntimeError) as refused:
        notices.write_ffmpeg_source_offer(tmp_path / "LICENSES", lock, media, profile="release")
    assert "NOT RECORDED" in str(refused.value)


def test_an_asset_name_and_probe_that_disagree_do_not_produce_a_commit() -> None:
    lock = _lock_with_ffmpeg(probe={"ffmpeg": "ffmpeg version n8.1-11-gdeadbeef00-20260430 Copyright"})
    fields = notices.ffmpeg_source_fields(lock, MEDIA)
    assert fields["upstream_commit"] is None
    assert "upstream_commit" in notices.missing_ffmpeg_fields(fields)


def test_no_offer_is_written_when_no_ffmpeg_is_bundled(tmp_path) -> None:
    report = notices.write_ffmpeg_source_offer(tmp_path / "LICENSES", LOCK, {"ffmpeg": None}, profile="release")
    assert report["status"] == "NOT_APPLICABLE"
    assert not (tmp_path / "LICENSES" / "ffmpeg").exists()


def test_the_release_profile_stops_the_notices_step_on_an_incomplete_offer(tmp_path) -> None:
    _, out = _bundle_tree(tmp_path)
    with pytest.raises(RuntimeError):
        notices.install_notices(out, lock=_lock_with_ffmpeg(**ROLLING), media=MEDIA, profile="release")


@pytest.mark.skipif(os.name != "nt", reason="main() refuses a non-Windows host before it reaches the lock")
def test_the_release_profile_refuses_a_lock_without_the_ffmpeg_source_before_building(
        tmp_path, monkeypatch) -> None:
    import build_local_bundle as local
    monkeypatch.setattr(local, "_source_sha", lambda: SHA)
    monkeypatch.setattr(local, "_source_dirty", lambda: False)
    monkeypatch.setattr(local, "build_wheels", lambda *a, **k: pytest.fail("the long build must not start"))
    monkeypatch.setattr(bundle.lockmod, "load", lambda: _lock_with_ffmpeg(**ROLLING))
    with pytest.raises(SystemExit) as refused:
        bundle.main(["--out", str(tmp_path / "dist"), "--profile", "release"])
    assert "NOT RECORDED" in str(refused.value) and "upstream_commit" in str(refused.value)
    assert not (tmp_path / "dist").exists(), "nothing is created before the pre-flight passes"


# ================================================================ 3. component notices

APACHE = "                                 Apache License\n   Version 2.0 ... Portions Copyright (c) Microsoft\n"
CREDITS = "--------------------\nThe Chromium Project\n--------------------\n// Copyright 2015 The Chromium Authors\n"
CC_BY = "Creative Commons Attribution 4.0 International Public License\n..."


def _components(tmp_path: Path, *, headless: bool = True) -> tuple[Path, Path]:
    out = tmp_path / "bundle"
    site = out / "runtime" / "Lib" / "site-packages"
    _put(out / "browser" / "chromium-1243" / "chrome-win64" / "chrome.exe", b"MZ")
    _put(out / "browser" / "chromium-1243" / "chrome-win64" / "153.0.8010.12.manifest", b"")
    _put(out / "browser" / "chromium-1243" / "chrome-win64" / "ABOUT", "Google Chrome\n")
    if headless:
        _put(out / "browser" / "chromium_headless_shell-1243" / "chrome-headless-shell-win64"
             / "LICENSE.headless_shell", CREDITS)
    _put(out / "browser" / "winldd-1007" / "PrintDeps.exe", b"MZ")
    package = site / "playwright" / "driver" / "package"
    _put(package / "LICENSE", APACHE)
    _put(package / "NOTICE", "Playwright\nCopyright (c) Microsoft Corporation\n")
    _put(package / "package.json", json.dumps({
        "name": "playwright-core", "version": "1.63.0",
        "repository": {"type": "git", "url": "git+https://github.com/microsoft/playwright.git"}}))
    _put(package / "lib" / "vite" / "dashboard" / "assets" / "codicon-DCmgc-ay.ttf", b"\x00\x01font")
    _put(package / "lib" / "vite" / "traceViewer" / "codicon.DCmgc-ay.ttf", b"\x00\x01font")
    _put(site / "faster_whisper" / "assets" / "silero_vad_v6.onnx", b"\x08onnx")
    _put(site / "faster_whisper-1.2.1.dist-info" / "METADATA", "Name: faster-whisper\nVersion: 1.2.1\n")
    src = tmp_path / "packaging_licenses"
    _put(src / "CC-BY-4.0.txt", CC_BY)
    return out, src


def test_component_notices_copy_real_texts_next_to_the_components(tmp_path) -> None:
    out, src = _components(tmp_path)
    report = notices.install_component_notices(out, licenses_src=src)

    chromium = out / "browser" / "chromium-1243" / "chrome-win64" / "LICENSE.chromium"
    assert chromium.read_bytes() == CREDITS.encode()
    chromium_notice = (out / "LICENSES" / "chromium" / "NOTICE.md").read_text(encoding="utf-8")
    assert "153.0.8010.12" in chromium_notice and "BSD-3-Clause" in chromium_notice
    assert report["chromium"]["status"] == "RECORDED"

    winldd = out / "browser" / "winldd-1007"
    assert (winldd / "LICENSE").read_bytes() == APACHE.encode()
    assert (winldd / "NOTICE").is_file()
    assert "Apache-2.0" in (out / "LICENSES" / "playwright-winldd" / "NOTICE.md").read_text(encoding="utf-8")
    assert report["playwright_winldd"]["status"] == "RECORDED"

    codicon = (out / "LICENSES" / "codicon" / "NOTICE.md").read_text(encoding="utf-8")
    assert ("Copyright (c) Microsoft Corporation, CC-BY-4.0, "
            "https://github.com/microsoft/vscode-codicons") in codicon
    assert (out / "LICENSES" / "codicon" / "CC-BY-4.0.txt").read_bytes() == CC_BY.encode()
    assert len(report["codicon"]["files"]) == 2


def test_the_silero_licence_text_is_not_recorded_until_it_is_in_the_repo(tmp_path) -> None:
    out, src = _components(tmp_path)
    report = notices.install_component_notices(out, licenses_src=src)
    notice = (out / "LICENSES" / "silero-vad" / "NOTICE.md").read_text(encoding="utf-8")
    assert report["silero_vad"]["status"] == "NOT RECORDED"
    assert "MIT" in notice and "https://github.com/snakers4/silero-vad" in notice
    assert "NOT RECORDED" in notice and "faster_whisper-1.2.1" in notice
    assert not (out / "LICENSES" / "silero-vad" / "LICENSE").exists(), "no text is composed"

    _put(src / "silero-vad" / "LICENSE", "MIT License\nupstream text\n")
    again = notices.install_component_notices(out, licenses_src=src)
    assert again["silero_vad"]["status"] == "RECORDED"
    assert (out / "LICENSES" / "silero-vad" / "LICENSE").read_text(encoding="utf-8").startswith("MIT License")


def test_a_chromium_without_any_credits_source_is_not_recorded_and_gets_no_invented_file(tmp_path) -> None:
    out, src = _components(tmp_path, headless=False)
    report = notices.install_component_notices(out, licenses_src=src)
    assert report["chromium"]["status"] == "NOT RECORDED"
    assert not (out / "browser" / "chromium-1243" / "chrome-win64" / "LICENSE.chromium").exists()
    notice = (out / "LICENSES" / "chromium" / "NOTICE.md").read_text(encoding="utf-8")
    assert "NOT RECORDED" in notice


def test_a_credits_file_the_browser_already_ships_is_not_duplicated(tmp_path) -> None:
    out, src = _components(tmp_path)
    shipped = _put(out / "browser" / "chromium-1243" / "chrome-win64" / "LICENSE", "shipped by the browser\n")
    report = notices.install_component_notices(out, licenses_src=src)
    assert not (out / "browser" / "chromium-1243" / "chrome-win64" / "LICENSE.chromium").exists()
    assert report["chromium"]["entries"][0]["notice"].endswith("chrome-win64/LICENSE")
    assert shipped.read_text(encoding="utf-8") == "shipped by the browser\n"


def test_components_that_are_not_in_the_output_are_not_claimed(tmp_path) -> None:
    out = tmp_path / "bundle"
    out.mkdir()
    report = notices.install_component_notices(out, licenses_src=tmp_path / "none")
    assert {item["status"] for item in report.values()} == {"NOT_PRESENT"}
    assert not (out / "LICENSES").exists()


# ================================================================ 4. python distributions

def _dist(site: Path, name: str, version: str, headers: str, *files: tuple[str, str]) -> Path:
    info = site / f"{name}-{version}.dist-info"
    _put(info / "METADATA", f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n{headers}\n\nbody\n")
    _put(info / "RECORD", f"{name}-{version}.dist-info/METADATA,,\r\n")
    for relative, content in files:
        _put(site / relative, content)
    return info


def _site_with_distributions(tmp_path: Path) -> Path:
    site = tmp_path / "bundle" / "runtime" / "Lib" / "site-packages"
    _dist(site, "plain", "1.0", "License-Expression: MIT",
          ("plain-1.0.dist-info/licenses/LICENSE", "MIT License\nCopyright (c) someone\n"))
    _dist(site, "legacy", "2.0", "License: BSD", ("legacy/LICENSE.txt", "BSD text\n"))
    site.joinpath("legacy-2.0.dist-info", "RECORD").write_text("legacy/LICENSE.txt,,\n", encoding="utf-8")
    _dist(site, "flatbuffers", "25.12.19",
          "License: Apache 2.0\nClassifier: License :: OSI Approved :: Apache Software License")
    _dist(site, "tokenizers", "0.23.2",
          "Classifier: License :: OSI Approved :: Apache Software License",
          ("tokenizers-0.23.2.dist-info/sboms/tokenizers-python.cyclonedx.json", json.dumps(
              {"components": [{"name": "tokenizers", "licenses": [{"expression": "Apache-2.0"}]},
                              {"name": "serde", "licenses": [{"expression": "MIT OR Apache-2.0"}]}]})))
    _dist(site, "MouseInfo", "0.1.3",
          "License: GPLv3+\nClassifier: License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)")
    _dist(site, "latex2mathml", "3.81.1", "License: MIT")
    _dist(site, "PyGetWindow", "0.0.9", "License: BSD\nClassifier: License :: OSI Approved :: BSD License")
    _dist(site, "onlyclassifier", "1.0", "Classifier: License :: OSI Approved :: Apache Software License")
    _dist(site, "dual", "1.0", "License: Apache-2.0 OR MIT")
    _dist(site, "nolicense", "1.0", "")
    return site


def test_every_distribution_gets_a_row_and_nothing_is_silently_unmapped(tmp_path) -> None:
    site = _site_with_distributions(tmp_path)
    out = site.parents[2]
    dist_infos = sorted(p.name for p in site.glob("*.dist-info"))
    report = notices.install_python_distributions(out)

    table = (out / "LICENSES" / "python-distributions.md").read_text(encoding="utf-8")
    rows = [line for line in table.splitlines() if line.startswith("| ") and not line.startswith("| distribution")]
    assert len(rows) == len(dist_infos) == report["total"] == 10
    assert report["with_licence_file"] + len(report["not_recorded"]) == report["total"]

    by_name = {row.split("|")[1].strip(): row for row in rows}
    assert "| yes |" in by_name["plain"] and "| yes |" in by_name["legacy"]
    assert report["not_recorded"] == sorted([
        "PyGetWindow==0.0.9", "dual==1.0", "latex2mathml==3.81.1", "nolicense==1.0", "onlyclassifier==1.0"])
    for name in ("PyGetWindow", "latex2mathml", "onlyclassifier", "dual", "nolicense"):
        assert "NOT RECORDED" in by_name[name], name
    assert "UNKNOWN" in by_name["nolicense"]
    assert "MIT" in by_name["latex2mathml"] and "BSD" in by_name["PyGetWindow"]


def test_a_declared_apache_or_gpl3_distribution_without_a_file_gets_the_canonical_text(tmp_path) -> None:
    site = _site_with_distributions(tmp_path)
    out = site.parents[2]
    report = notices.install_python_distributions(out)
    assert report["added_by_build"] == ["MouseInfo==0.1.3", "flatbuffers==25.12.19", "tokenizers==0.23.2"]

    apache = (notices.REPO_LICENSES / "Apache-2.0.txt").read_bytes()
    gpl = (notices.REPO_LICENSES / "GPL-3.0.txt").read_bytes()
    for name in ("flatbuffers-25.12.19", "tokenizers-0.23.2"):
        assert (site / f"{name}.dist-info" / "licenses" / "LICENSE").read_bytes() == apache
    assert (site / "MouseInfo-0.1.3.dist-info" / "licenses" / "LICENSE").read_bytes() == gpl

    # METADATA/RECORD stay a consistent description of the installed files
    record = (site / "flatbuffers-25.12.19.dist-info" / "RECORD").read_text(encoding="utf-8")
    digest = __import__("base64").urlsafe_b64encode(hashlib.sha256(apache).digest()).rstrip(b"=").decode()
    assert f"flatbuffers-25.12.19.dist-info/licenses/LICENSE,sha256={digest},{len(apache)}" in record

    # MIT/BSD text carries a per-project copyright line: it is never composed
    for name in ("latex2mathml-3.81.1", "PyGetWindow-0.0.9", "onlyclassifier-1.0", "dual-1.0"):
        assert not (site / f"{name}.dist-info" / "licenses").exists(), name


def test_canonical_text_requires_the_distribution_to_declare_exactly_that_licence() -> None:
    canonical = notices.canonical_licence
    assert canonical(["Apache 2.0", "Apache Software License"]) == "Apache-2.0"
    assert canonical(["Apache Software License", "Apache-2.0"]) == "Apache-2.0"
    assert canonical(["GPLv3+", "GNU General Public License v3 or later (GPLv3+)"]) == "GPL-3.0"
    assert canonical(["Apache Software License"]) is None, "the version is not stated"
    assert canonical(["Apache-2.0 OR MIT"]) is None
    assert canonical(["Apache-2.0", "MIT License"]) is None
    assert canonical(["GPL"]) is None and canonical(["GNU General Public License v2 (GPLv2)"]) is None
    assert canonical(["LGPLv3"]) is None and canonical(["GNU Affero General Public License v3"]) is None
    assert canonical(["MIT"]) is None and canonical(["BSD", "BSD License"]) is None
    assert canonical([]) is None


def test_tracked_licence_texts_are_the_ones_the_readme_names() -> None:
    folder = REPO / "packaging" / "licenses"
    apache = (folder / "Apache-2.0.txt").read_bytes()
    assert hashlib.sha256(apache).hexdigest() == (
        "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30")
    gpl = (folder / "GPL-3.0.txt").read_text(encoding="utf-8")
    assert "GNU GENERAL PUBLIC LICENSE" in gpl and "Version 3, 29 June 2007" in gpl
    assert "Creative Commons Attribution 4.0 International Public License" in (
        folder / "CC-BY-4.0.txt").read_text(encoding="utf-8")
    readme = (folder / "README.md").read_text(encoding="utf-8")
    for path in folder.glob("*.txt"):
        assert b"\r" not in path.read_bytes(), f"{path.name} must keep LF"
        assert hashlib.sha256(path.read_bytes()).hexdigest() in readme, f"{path.name} not in the provenance table"


def test_install_notices_runs_all_three_parts(tmp_path) -> None:
    out, src = _components(tmp_path)
    _site_with_distributions(tmp_path)  # same bundle root: tmp_path/bundle
    report = notices.install_notices(out, lock=LOCK, media=MEDIA, profile="release", licenses_src=src)
    assert report["ffmpeg_source_offer"]["status"] == "COMPLETE"
    assert report["components"]["chromium"]["status"] == "RECORDED"
    assert report["python_distributions"]["total"] >= 10
    assert (out / "LICENSES" / "ffmpeg" / "SOURCE_OFFER.md").is_file()
    assert (out / "LICENSES" / "python-distributions.md").is_file()
    assert json.loads(json.dumps(report)) == report, "the report goes into MANIFEST.json"
    # nothing the notices step wrote carries a CR (they are generated, not typed on Windows)
    for path in (out / "LICENSES").rglob("*.md"):
        assert b"\r" not in path.read_bytes(), path.name
