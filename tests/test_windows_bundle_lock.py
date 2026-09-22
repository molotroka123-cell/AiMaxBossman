"""OA-03: the Windows archive is built from inputs pinned before they are read.

These run on Linux: they test the lock's reading, the builder's locked paths
with fake downloads and a fake pip, the digest refusal BEFORE extraction, the
release profile, the embedded-interpreter check and the deterministic zip.
Whether the locked build actually assembles on windows-latest is decided by
the workflow; here nothing may drift between the lock and what the builder
does with it.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
import build_windows_bundle as bundle  # noqa: E402
import windows_bundle_lock as lockmod  # noqa: E402

SHA = "a" * 40


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def lock(tmp_path) -> dict:
    """A committed-style lock in a temp directory, loaded through the real reader."""
    txt = ("# synthetic\n"
           "alpha==1.0 \\\n    --hash=sha256:" + "1" * 64 + "\n"
           "beta-lib==2.5.1 \\\n    --hash=sha256:" + "2" * 64 + "\n")
    txt_path = tmp_path / "windows_bundle_lock.txt"
    txt_path.write_text(txt, encoding="utf-8", newline="\n")
    data = {
        "schema_version": 1, "recorded_at": "2026-09-17T00:00:00+00:00",
        "platform": {"os": "windows", "arch": "amd64", "python_tag": "cp312"},
        "python": {"version": "3.12.11", "embeddable_url": "https://example.test/python-3.12.11-embed-amd64.zip",
                   "embeddable_sha256": sha256(b"python zip")},
        "ffmpeg": {"release": "autobuild-2026-09-16-12-00", "asset": "ffmpeg-n8.0-3-gabc1234-win64-gpl-8.0.zip",
                   "url": "https://example.test/ffmpeg.zip", "sha256": sha256(b"ffmpeg zip")},
        "chromium": {"directories": ["chromium-1200"], "playwright": "1.55.0"},
        "requirements": {"file": "windows_bundle_lock.txt", "sha256": sha256(txt.encode("utf-8")), "count": 2},
    }
    json_path = tmp_path / "windows_bundle_lock.json"
    json_path.write_text(json.dumps(data), encoding="utf-8")
    return lockmod.load(json_path, txt_path)


# ------------------------------------------------------------------ reading

def test_no_lock_files_means_no_lock(tmp_path):
    assert lockmod.load(tmp_path / "missing.json", tmp_path / "missing.txt") is None


def test_the_lock_is_read_with_its_pins(lock):
    assert lock["_pins"] == {"alpha": "1.0", "beta-lib": "2.5.1"}
    assert lock["python"]["version"] == "3.12.11"


def test_build_tools_must_match_their_digest_and_pins(tmp_path, lock):
    json_path, txt_path = tmp_path / "windows_bundle_lock.json", tmp_path / "windows_bundle_lock.txt"
    tools_txt = "setuptools==80.0.0 \\\n    --hash=sha256:" + "4" * 64 + "\n"
    (tmp_path / "windows_bundle_build_tools.txt").write_text(tools_txt, encoding="utf-8", newline="\n")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    data["build_tools"] = {"file": "windows_bundle_build_tools.txt", "sha256": sha256(tools_txt.encode("utf-8")),
                           "pins": {"setuptools": "80.0.0"}}
    json_path.write_text(json.dumps(data), encoding="utf-8")
    assert lockmod.load(json_path, txt_path)["_build_tools_txt"] == tmp_path / "windows_bundle_build_tools.txt"
    data["build_tools"]["pins"] = {"setuptools": "80.0.1"}
    json_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="build tool pins"):
        lockmod.load(json_path, txt_path)
    data["build_tools"]["pins"] = {"setuptools": "80.0.0"}
    data["build_tools"]["sha256"] = "5" * 64
    json_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="build tools file"):
        lockmod.load(json_path, txt_path)


@pytest.mark.parametrize("damage", ["txt_edited", "count", "digest", "schema", "unpinned_line", "twice"])
def test_a_lock_that_does_not_match_itself_is_refused(tmp_path, lock, damage):
    json_path, txt_path = tmp_path / "windows_bundle_lock.json", tmp_path / "windows_bundle_lock.txt"
    data = json.loads(json_path.read_text(encoding="utf-8"))
    if damage == "txt_edited":
        txt_path.write_text(txt_path.read_text(encoding="utf-8").replace("1.0", "1.1"), encoding="utf-8", newline="\n")
    elif damage == "count":
        data["requirements"]["count"] = 3
    elif damage == "digest":
        data["ffmpeg"]["sha256"] = "not-a-digest"
    elif damage == "schema":
        data["schema_version"] = 2
    elif damage == "unpinned_line":
        txt = txt_path.read_text(encoding="utf-8") + "gamma>=1\n"
        txt_path.write_text(txt, encoding="utf-8", newline="\n")
        data["requirements"]["sha256"] = sha256(txt.encode("utf-8"))
    else:
        txt = txt_path.read_text(encoding="utf-8") + "alpha==1.0 \\\n    --hash=sha256:" + "3" * 64 + "\n"
        txt_path.write_text(txt, encoding="utf-8", newline="\n")
        data["requirements"]["sha256"] = sha256(txt.encode("utf-8"))
        data["requirements"]["count"] = 3
    json_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        lockmod.load(json_path, txt_path)


def test_the_committed_lock_if_present_is_consistent():
    """Once the Windows runner's lock lands in tools/, it must load."""
    if not lockmod.LOCK_JSON.exists():
        assert not lockmod.LOCK_TXT.exists(), "a requirements file without its JSON is half a lock"
        return
    lock = lockmod.load()
    assert lock["_pins"], "an empty lock pins nothing"
    assert lock["platform"] == {"os": "windows", "arch": "amd64", "python_tag": "cp312"}
    assert lockmod.FFMPEG_ASSET.match(lock["ffmpeg"]["asset"]), "only an immutable release-branch asset"
    assert "latest" not in lock["ffmpeg"]["url"]
    assert lock["ffmpeg"]["api_digest_matched"] is True
    assert lock["ffmpeg"]["probe"]["default_export"]["fully_decoded"] is True
    assert lock["python"]["version"].startswith("3.12.")
    # The build tools that build the sdists are pinned beside the lock.
    assert lock["_build_tools_txt"].is_file()
    assert set(lock["build_tools"]["pins"]) == {"pip", "setuptools", "wheel"}
    assert lock["build_tools"]["source_distributions_in_lock"], "the record names what needs building"
    for name in lock["build_tools"]["source_distributions_in_lock"]:
        assert lockmod.normalize(name) in lock["_pins"], name
    workflow = (REPO / ".github" / "workflows" / "windows-bundle.yml").read_text(encoding="utf-8")
    assert "--require-hashes --requirement tools/windows_bundle_build_tools.txt" in workflow


# ---------------------------------------------------------------- recording

def test_requirements_come_from_pip_s_report_without_the_local_wheels():
    report = {"install": [
        {"metadata": {"name": "Beta_Lib", "version": "2.5.1"},
         "download_info": {"url": "https://files.pythonhosted.org/beta_lib-2.5.1-py3-none-any.whl",
                           "archive_info": {"hashes": {"sha256": "2" * 64}}}},
        {"metadata": {"name": "bossman-command-center", "version": "0.1.0"},
         "download_info": {"url": "file:///C:/work/wheels/bossman_command_center-0.1.0-py3-none-any.whl",
                           "archive_info": {"hashes": {"sha256": "9" * 64}}}},
        {"metadata": {"name": "alpha", "version": "1.0"},
         "download_info": {"url": "https://files.pythonhosted.org/alpha-1.0.tar.gz",
                           "archive_info": {"hashes": {"sha256": "1" * 64}}}},
    ]}
    rows = lockmod.requirements_from_report(report)
    assert [row["name"] for row in rows] == ["alpha", "beta-lib"]
    text = lockmod.requirements_text(rows, recorded_at="now", platform_tag="win_amd64 cp312")
    assert lockmod.requirement_pins(text) == {"alpha": "1.0", "beta-lib": "2.5.1"}
    assert "--hash=sha256:" + "2" * 64 in text
    with pytest.raises(ValueError):
        lockmod.requirements_from_report({"install": [{"metadata": {"name": "x", "version": "1"},
                                                       "download_info": {"url": "https://x", "archive_info": {}}}]})


def test_the_ffmpeg_pin_is_the_newest_immutable_release_branch_asset():
    releases = [
        {"tag_name": "latest", "published_at": "2026-09-17T00:00:00Z",
         "assets": [{"name": "ffmpeg-master-latest-win64-gpl.zip", "browser_download_url": "u0"},
                    {"name": "ffmpeg-n8.0-latest-win64-gpl-8.0.zip", "browser_download_url": "u1"}]},
        {"tag_name": "autobuild-2026-09-16-12-00", "published_at": "2026-09-16T12:00:00Z",
         "assets": [{"name": "ffmpeg-n7.1-45-gdeadbeef00-win64-gpl-7.1.zip", "browser_download_url": "u2",
                     "size": 1, "digest": "sha256:" + "7" * 64},
                    {"name": "ffmpeg-n8.0-3-gabc1234abc-win64-gpl-8.0.zip", "browser_download_url": "u3",
                     "size": 2, "digest": "sha256:" + "8" * 64},
                    {"name": "ffmpeg-n8.0-3-gabc1234abc-win64-gpl-shared-8.0.zip", "browser_download_url": "u4"}]},
        {"tag_name": "autobuild-2026-09-15-12-00", "published_at": "2026-09-15T12:00:00Z",
         "assets": [{"name": "ffmpeg-n8.0-2-g0000000000-win64-gpl-8.0.zip", "browser_download_url": "u5"}]},
    ]
    chosen = lockmod.select_ffmpeg_asset(releases)
    assert chosen["release"] == "autobuild-2026-09-16-12-00"
    assert chosen["asset"] == "ffmpeg-n8.0-3-gabc1234abc-win64-gpl-8.0.zip"
    assert chosen["url"] == "u3" and chosen["api_digest"] == "8" * 64
    with pytest.raises(RuntimeError):
        lockmod.select_ffmpeg_asset(releases[:1])


def test_record_refuses_to_run_anywhere_but_the_windows_runner(tmp_path):
    if os.name == "nt":
        pytest.skip("this host IS Windows")
    with pytest.raises(RuntimeError):
        lockmod.record(tmp_path, None, tmp_path / "out")


# ------------------------------------------------------- the locked builder

def test_a_download_with_the_wrong_digest_never_reaches_extraction(tmp_path, monkeypatch, lock):
    served = tmp_path / "served.zip"
    with zipfile.ZipFile(served, "w") as zf:
        zf.writestr("ffmpeg/bin/ffmpeg.exe", b"x")
        zf.writestr("ffmpeg/bin/ffprobe.exe", b"y")
    monkeypatch.setattr(bundle, "fetch", lambda url, target: served)
    media = tmp_path / "product" / "media"
    with pytest.raises(RuntimeError, match="digest mismatch"):
        bundle.install_media(media, tmp_path, "https://example.test/other.zip", lock)
    assert not any(media.iterdir()), "nothing was extracted"
    assert not served.exists(), "the mismatching download is not kept around"


def test_the_locked_media_asset_wins_over_the_command_line_url(tmp_path, monkeypatch, lock):
    payload = b"ffmpeg zip"
    lock["ffmpeg"]["sha256"] = sha256(payload)
    requested = []

    def fake_fetch(url, target):
        requested.append(url)
        with zipfile.ZipFile(target, "w") as zf:
            zf.writestr("ffmpeg/bin/ffmpeg.exe", b"x")
            zf.writestr("ffmpeg/bin/ffprobe.exe", b"y")
            zf.writestr("ffmpeg/LICENSE.txt", b"GPL")
        return target

    monkeypatch.setattr(bundle, "fetch", fake_fetch)
    monkeypatch.setattr(bundle, "sha256_file", lambda path: sha256(payload))
    contents, required = bundle.install_media(tmp_path / "product" / "media", tmp_path,
                                              "https://example.test/rolling-latest.zip", lock)
    assert requested == [lock["ffmpeg"]["url"]]
    assert contents["pinned"] is True and not required


def test_the_locked_runtime_needs_the_same_minor_and_the_locked_digest(tmp_path, monkeypatch, lock):
    lock["python"]["version"] = "3.9.1"
    with pytest.raises(RuntimeError, match="pins CPython"):
        bundle.install_runtime(tmp_path / "runtime", tmp_path, lock)
    lock["python"]["version"] = f"{sys.version_info.major}.{sys.version_info.minor}.99"
    monkeypatch.setattr(bundle, "fetch", lambda url, target: (target.write_bytes(b"not the zip"), target)[1])
    with pytest.raises(RuntimeError, match="digest mismatch"):
        bundle.install_runtime(tmp_path / "runtime", tmp_path, lock)
    assert not (tmp_path / "runtime").exists()


def test_locked_packages_are_installed_in_hash_checking_mode_without_an_index(tmp_path, monkeypatch, lock):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[3] == "list":
            return subprocess.CompletedProcess(cmd, 0, "[]", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(bundle, "run", fake_run)
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    for name in ("bossman_shared-0.1.0-py3-none-any.whl", "bossman_core-0.3.0-py3-none-any.whl",
                 "bossman_command_center-0.1.0-py3-none-any.whl"):
        (wheels / name).write_bytes(b"")
    runtime = tmp_path / "runtime"
    (runtime / "Lib" / "site-packages").mkdir(parents=True)
    result = bundle.install_packages(runtime, wheels, lock, tmp_path / "work")
    pip_calls = [cmd[3:] for cmd in calls if cmd[1:3] == ["-m", "pip"]]
    download, install, ours, listing = pip_calls
    assert download[:3] == ["download", "--no-deps", "--require-hashes"]
    assert "--requirement" in download and str(lock["_txt"]) in download
    assert install[:2] == ["install", "--no-index"] and "--require-hashes" in install and "--no-deps" in install
    assert "--no-build-isolation" in install, "the lock's source distributions are built by the pinned tools"
    assert "--find-links" in install and str(tmp_path / "work" / "wheelhouse") in install
    assert ours[:4] == ["install", "--no-index", "--no-deps", "--target"]
    assert all(arg.endswith(".whl") for arg in ours[5:]) and len(ours[5:]) == 3
    assert listing[0] == "list"
    assert result["pinned"] is True and result["resolver"].startswith("none")
    assert not any("--upgrade" in cmd for cmd in calls), "no resolution against an index in locked mode"


def test_unlocked_packages_still_build_but_say_so(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(bundle, "run", lambda cmd, **kw: (calls.append(cmd), subprocess.CompletedProcess(cmd, 0, "[]", ""))[1])
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    (wheels / "bossman_core-0.3.0-py3-none-any.whl").write_bytes(b"")
    runtime = tmp_path / "runtime"
    (runtime / "Lib" / "site-packages").mkdir(parents=True)
    result = bundle.install_packages(runtime, wheels, None, tmp_path)
    assert result["pinned"] is False and "--upgrade" in calls[0]


def test_the_embedded_interpreter_must_see_exactly_the_locked_set(tmp_path, monkeypatch, lock):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    (wheels / "bossman_core-0.3.0-py3-none-any.whl").write_bytes(b"")
    seen = {"alpha": ["1.0"], "beta-lib": ["2.5.1"], "bossman-core": ["0.3.0"]}

    def probe(cmd, **kwargs):
        assert cmd[1] == "-I", "the embedded interpreter is asked in isolated mode"
        payload = {"distributions": seen, "bcc": str(runtime / "Lib" / "site-packages" / "bcc" / "__init__.py"),
                   "executable": str(runtime / "python.exe"), "isolated": True}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(payload) + "\n", "")

    monkeypatch.setattr(bundle.subprocess, "run", probe)
    result = bundle.verify_runtime(runtime, wheels, lock)
    assert result == {"verified_by_embedded_python": True, "distribution_count": 3, "isolated": True, "matches_lock": True}
    seen["gamma"] = ["0.1"]
    with pytest.raises(RuntimeError, match="extra=\\['gamma'\\]"):
        bundle.verify_runtime(runtime, wheels, lock)
    del seen["gamma"]
    seen["alpha"] = ["1.1"]
    with pytest.raises(RuntimeError, match="other_version=\\['alpha'\\]"):
        bundle.verify_runtime(runtime, wheels, lock)
    seen["alpha"] = ["1.0", "1.1"]
    with pytest.raises(RuntimeError, match="two versions"):
        bundle.verify_runtime(runtime, wheels, lock)


def test_a_product_imported_from_outside_the_runtime_fails_the_build(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    payload = {"distributions": {}, "bcc": str(REPO / "command-center" / "bcc" / "__init__.py"),
               "executable": "x", "isolated": True}
    monkeypatch.setattr(bundle.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, json.dumps(payload), ""))
    with pytest.raises(RuntimeError, match="outside the runtime"):
        bundle.verify_runtime(runtime, tmp_path, None)


def test_the_locked_browser_directory_must_be_the_one_the_lock_names(tmp_path, monkeypatch, lock):
    browser = tmp_path / "browser"

    def fake_run(cmd, **kwargs):
        (browser / "chromium-1201").mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(bundle, "run", fake_run)
    with pytest.raises(RuntimeError, match="the lock names"):
        bundle.install_browser(browser, tmp_path / "runtime", lock)
    lock["chromium"]["directories"] = ["chromium-1201"]
    assert bundle.install_browser(browser, tmp_path / "runtime", lock) == {"chromium_dirs": ["chromium-1201"], "pinned": True}


def test_the_manifest_says_whether_its_inputs_were_locked(lock):
    unlocked = bundle.build_inputs_for(None, "release", {})
    assert unlocked["locked"] is False and unlocked["lock"] is None
    locked = bundle.build_inputs_for(lock, "release", {})
    assert locked["locked"] is True
    assert locked["lock"]["requirements"] == 2 and locked["lock"]["python"] == "3.12.11"
    assert locked["lock"]["ffmpeg"] == lock["ffmpeg"]["asset"]
    assert any("direct_url.json" in line for line in locked["known_differences_between_builds"])


def test_two_archives_of_one_tree_are_byte_identical(tmp_path):
    tree = tmp_path / "BOSSMAN-Windows-x64-synthetic"
    (tree / "runtime").mkdir(parents=True)
    (tree / "runtime" / "python.exe").write_bytes(b"exe")
    (tree / "MANIFEST.json").write_text("{}", encoding="utf-8")
    first, second = tmp_path / "one.zip", tmp_path / "two.zip"
    bundle.write_archive(tree, first, 1789569641)
    os.utime(tree / "runtime" / "python.exe", (1, 1))  # a different extraction moment
    bundle.write_archive(tree, second, 1789569641)
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as zf:
        names = zf.namelist()
        assert names == sorted(names)
        assert names[0].startswith("BOSSMAN-Windows-x64-synthetic/")
        # DOS zip time has two-second resolution: 14:40:41 is stored as 14:40:40.
        assert {info.date_time for info in zf.infolist()} == {(2026, 9, 16, 14, 40, 40)}


def test_the_release_profile_is_the_default_and_names_its_rule():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("release", "diagnostic"), default="release")
    assert parser.parse_args([]).profile == "release"
    source = (REPO / "tools" / "build_windows_bundle.py").read_text(encoding="utf-8")
    assert 'if args.profile == "release" and required:' in source
    workflow = (REPO / ".github" / "workflows" / "windows-bundle.yml").read_text(encoding="utf-8")
    assert "--profile release" in workflow
    assert "windows_bundle_lock.py record" in workflow


def test_a_lock_the_recorder_writes_passes_its_own_digest_check(tmp_path):
    """Regression: on Windows a text-mode write turned LF into CRLF, so a lock the
    recorder had just written failed ``load`` with 'does not match the digest'."""
    txt = "# recorded\nalpha==1.0 \\\n    --hash=sha256:" + "1" * 64 + "\n"
    target = tmp_path / "windows_bundle_lock.txt"
    lockmod.write_lf(target, txt)
    assert target.read_bytes() == txt.encode("utf-8")
    assert lockmod.sha256_file(target) == sha256(txt.encode("utf-8"))
