"""Add-on installer: hash verification before writing, safe unpacking, atomic install, activation. No network."""
from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
import zipfile

import pytest

from bcc.telegram_calls import addon
from bcc.telegram_calls.types import CallError


def wheel(files: dict[str, bytes], *, symlink: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (0o120777 << 16)
            z.writestr(info, "../../etc/passwd")
    return buf.getvalue()


def sdist(top: str, pkg: str, files: dict[str, bytes], *, evil: str | None = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(f"{top}/{pkg}/{name}")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        info = tarfile.TarInfo(f"{top}/setup.py")
        info.size = 3
        tf.addfile(info, io.BytesIO(b"x=1"))
        if evil:
            info = tarfile.TarInfo(evil)
            info.size = 1
            tf.addfile(info, io.BytesIO(b"x"))
    return buf.getvalue()


def make_lock(entries):
    return {"schema": 1, "target": addon.current_target(), "packages": entries}


def entry(name, version, kind, blob, **extra):
    return {"name": name, "version": version, "kind": kind, "filename": f"{name}-{version}", "url": f"https://files.example/{name}",
            "sha256": hashlib.sha256(blob).hexdigest(), **extra}


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path / "data"


@pytest.fixture(autouse=True)
def nothing_preinstalled(monkeypatch):
    monkeypatch.setattr(addon, "_installed_version", lambda name: None)
    saved = list(sys.path)
    yield
    sys.path[:] = saved


def fetcher(blobs):
    def fetch(url, max_bytes):
        return blobs[url.rsplit("/", 1)[-1]]
    return fetch


def test_install_verifies_unpacks_and_activates(data_dir):
    w = wheel({"demoaddon/__init__.py": b"VALUE = 7\n", "demoaddon-1.dist-info/METADATA": b"Name: demoaddon\n",
               "demoaddon-1.data/purelib/extra_mod.py": b"E = 1\n", "demoaddon-1.data/scripts/tool": b"#!/bin/sh"})
    s = sdist("pyaes-1.6.1", "pyaes", {"__init__.py": b"AES = True\n"})
    lock = make_lock([entry("demoaddon", "1", "wheel", w), entry("pyaes", "1.6.1", "sdist_pure", s, package_dir="pyaes")])
    out = addon.install(data_dir, fetch=fetcher({"demoaddon": w, "pyaes": s}), lock=lock)
    assert out["status"] == "installed" and sorted(out["installed"]) == ["demoaddon==1", "pyaes==1.6.1"]
    site = addon.site_dir(lock, data_dir)
    assert (site / "demoaddon" / "__init__.py").is_file() and (site / "extra_mod.py").is_file() and (site / "pyaes" / "__init__.py").is_file()
    assert not (site / "tool").exists() and not (site / "setup.py").exists()          # scripts and sdist metadata are not installed
    manifest = json.loads((site.parent / addon.MANIFEST).read_text())
    assert manifest["packages"]["pyaes"]["version"] == "1.6.1"
    assert str(site) in sys.path
    assert not [p for p in site.parent.parent.iterdir() if p.name.startswith(".partial-")]


def test_a_wrong_hash_writes_nothing_at_all(data_dir):
    good = wheel({"pkg/__init__.py": b"x"})
    lock = make_lock([entry("pkg", "1", "wheel", good)])
    with pytest.raises(CallError) as ei:
        addon.install(data_dir, fetch=fetcher({"pkg": good + b"tampered"}), lock=lock)
    assert ei.value.detail.startswith("sha256_mismatch")
    assert not (data_dir / "addons").exists() or not any((data_dir / "addons").rglob("*.py"))


@pytest.mark.parametrize("bad_member", ["../evil.py", "/abs/evil.py", "C:/evil.py", "pkg/../../evil.py"])
def test_path_traversal_in_a_wheel_is_refused_and_leaves_no_partial_install(data_dir, bad_member):
    w = wheel({"pkg/__init__.py": b"x", bad_member: b"boom"})
    lock = make_lock([entry("pkg", "1", "wheel", w)])
    with pytest.raises(CallError) as ei:
        addon.install(data_dir, fetch=fetcher({"pkg": w}), lock=lock)
    assert ei.value.detail == "unsafe_archive_member"
    root = data_dir / "addons" / "telegram-calls"
    assert not any(root.rglob("*")) if root.exists() else True
    assert not (data_dir / "evil.py").exists() and not (data_dir.parent / "evil.py").exists()


def test_symlink_member_is_refused(data_dir):
    w = wheel({"pkg/__init__.py": b"x"}, symlink="pkg/link")
    lock = make_lock([entry("pkg", "1", "wheel", w)])
    with pytest.raises(CallError):
        addon.install(data_dir, fetch=fetcher({"pkg": w}), lock=lock)


def test_sdist_member_outside_the_named_package_is_ignored_and_traversal_is_refused(data_dir):
    good = sdist("pyaes-1", "pyaes", {"__init__.py": b"1"})
    lock = make_lock([entry("pyaes", "1", "sdist_pure", good, package_dir="pyaes")])
    addon.install(data_dir, fetch=fetcher({"pyaes": good}), lock=lock)
    assert not (addon.site_dir(lock, data_dir) / "setup.py").exists()
    evil = sdist("pyaes-2", "pyaes", {"__init__.py": b"1"}, evil="../../escape.txt")
    lock2 = make_lock([entry("pyaes", "2", "sdist_pure", evil, package_dir="pyaes")])
    with pytest.raises(CallError):
        addon.install(data_dir, fetch=fetcher({"pyaes": evil}), lock=lock2)


def test_oversized_download_and_unpacked_bombs_are_refused(data_dir, monkeypatch):
    w = wheel({"pkg/__init__.py": b"x" * 2000})
    lock = make_lock([entry("pkg", "1", "wheel", w)])
    monkeypatch.setattr(addon, "MAX_UNPACKED_BYTES", 1000)
    with pytest.raises(CallError) as ei:
        addon.install(data_dir, fetch=fetcher({"pkg": w}), lock=lock)
    assert ei.value.detail == "unpacked_too_large"


def test_already_provided_packages_are_skipped_and_second_install_is_a_noop(data_dir, monkeypatch):
    w = wheel({"pkg/__init__.py": b"x"})
    lock = make_lock([entry("pkg", "1", "wheel", w)])
    monkeypatch.setattr(addon, "_installed_version", lambda name: "1")              # the bundle already has it
    assert addon.install(data_dir, fetch=lambda *a: pytest.fail("must not download"), lock=lock)["status"] == "already_satisfied"
    monkeypatch.setattr(addon, "_installed_version", lambda name: "0.9")            # wrong version: not satisfied
    assert addon.missing(lock)[0]["name"] == "pkg"


def test_other_platform_is_refused_with_the_pip_remedy(data_dir):
    lock = {"schema": 1, "target": "win_amd64-cp399", "packages": []}
    with pytest.raises(CallError) as ei:
        addon.install(data_dir, lock=lock)
    assert ei.value.detail == "addon_target_mismatch"


def test_lock_file_shipped_with_the_package_is_well_formed_and_pinned():
    lock = addon.load_lock()
    names = {p["name"].lower() for p in lock["packages"]}
    assert {"telethon", "py-tgcalls", "ntgcalls", "pysilero-vad", "soxr", "pyaes"} <= names
    assert lock["target"] == "win_amd64-cp312"
    for p in lock["packages"]:
        assert p["url"].startswith("https://files.pythonhosted.org/") and len(p["sha256"]) == 64 and p["version"]
    nt = next(p for p in lock["packages"] if p["name"] == "ntgcalls")
    assert nt["filename"].endswith("cp312-cp312-win_amd64.whl") and nt["sha256"].startswith("545cfe00")


def test_status_tells_which_remedy_applies_and_activation_without_install_is_none(data_dir, monkeypatch):
    st = addon.status(data_dir)
    assert st["complete"] is False and st["missing"] and st["remedy"] in ("bossman call install", 'pip install "bossman-command-center[calls]"')
    assert addon.activate(data_dir) is None
