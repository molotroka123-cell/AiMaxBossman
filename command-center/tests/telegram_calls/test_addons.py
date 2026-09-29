"""Add-on installer: pinned, hash-verified, traversal-safe, idempotent, refuses unhashed. Fake wheels, no network."""
from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile

import pytest

from bcc.telegram_calls import addons
from bcc.telegram_calls.addons import AddonError, Artifact


def wheel(files: dict[str, bytes], *, symlink: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
        if symlink:
            zi = zipfile.ZipInfo(symlink)
            zi.external_attr = 0o120777 << 16
            z.writestr(zi, "/etc/passwd")
    return buf.getvalue()


class FakePyPI:
    """Serves the JSON API and files; records every requested URL."""

    def __init__(self, blobs: dict[str, tuple[str, bytes]]):
        self.blobs = blobs                       # project -> (filename, bytes)
        self.calls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        for name, (fn, data) in self.blobs.items():
            if url == f"https://pypi.org/pypi/{name}/1.0/json":
                return json.dumps({"urls": [{"url": f"https://files.pythonhosted.org/packages/x/{fn}",
                                             "digests": {"sha256": hashlib.sha256(data).hexdigest()}}]}).encode()
            if url == f"https://files.pythonhosted.org/packages/x/{fn}":
                return data
        raise OSError("not found")


def setup(tmp_path, *, tamper=False):
    good = {"aaa": wheel({"aaa/__init__.py": b"X = 1\n", "aaa-1.0.dist-info/METADATA": b"m"}),
            "bbb": wheel({"bbb.py": b"Y = 2\n"})}
    manifest = tuple(Artifact(n, "1.0", hashlib.sha256(b).hexdigest()) for n, b in good.items())
    served = {n: (f"{n}-1.0-py3-none-any.whl", (b + b"evil") if (tamper and n == "bbb") else b) for n, b in good.items()}
    if tamper:          # PyPI now serves different bytes than our pin: locate fails (no file with the pinned hash)
        served["bbb"] = ("bbb-1.0-py3-none-any.whl", good["bbb"])
    return manifest, FakePyPI(served), good


def test_good_install_unpacks_and_is_idempotent(tmp_path):
    manifest, net, _ = setup(tmp_path)
    r = addons.install(tmp_path, downloader=net, manifest=manifest)
    assert r["ok"] and r["code"] == "INSTALLED", r
    pk = addons.addon_path(tmp_path)
    assert (pk / "aaa" / "__init__.py").read_text() == "X = 1\n" and (pk / "bbb.py").exists()
    assert addons.status(tmp_path, manifest)["complete"] is True
    calls = len(net.calls)
    r2 = addons.install(tmp_path, downloader=net, manifest=manifest)
    assert r2["ok"] and r2["code"] == "ALREADY_INSTALLED" and len(net.calls) == calls      # no second download


def test_bad_hash_rejected_and_nothing_unpacked(tmp_path):
    manifest, net, _ = setup(tmp_path)
    real = net.__call__

    def lying(url):                                  # locates fine, but delivers other bytes than pinned
        data = real(url)
        return wheel({"evil.py": b"boom"}) if url.endswith("aaa-1.0-py3-none-any.whl") else data

    r = addons.install(tmp_path, downloader=lying, manifest=manifest)
    assert not r["ok"] and r["code"] == "HASH_MISMATCH" and r["artifact"] == "aaa"
    assert not (addons.addon_path(tmp_path) / "evil.py").exists()
    assert addons.status(tmp_path, manifest)["complete"] is False


def test_pypi_file_with_other_hash_is_not_located(tmp_path):
    manifest, net, good = setup(tmp_path)
    net.blobs["aaa"] = ("aaa-1.0-py3-none-any.whl", good["aaa"] + b"x")      # PyPI hash differs from our pin
    r = addons.install(tmp_path, downloader=net, manifest=manifest)
    assert not r["ok"] and r["code"] == "WHEEL_NOT_FOUND"


def test_unhashed_artifact_refused_before_any_download(tmp_path):
    seen: list[str] = []
    r = addons.install(tmp_path, downloader=lambda u: seen.append(u) or b"", manifest=addons.MANIFEST)
    assert not r["ok"] and r["code"] == addons.UNPINNED and "telethon" in r["unpinned"] and seen == []
    assert not addons.addon_path(tmp_path).exists()


def test_shipped_manifest_pins_the_verified_hashes_and_marks_the_rest():
    by = {a.name: a for a in addons.MANIFEST}
    assert by["py-tgcalls"].sha256 == "c736066f3f79804f6f128231f4adad228e6aefb3418a473e0a52175c0a8baf49"
    assert by["ntgcalls"].sha256 == "545cfe0069911cbc22f98b1ac8ba4be42065903e88467b590037aa5514c9beba"
    assert by["py-tgcalls"].pinned and by["ntgcalls"].pinned
    assert by["telethon"].sha256 == addons.UNPINNED and not by["telethon"].pinned


@pytest.mark.parametrize("bad", ["../evil.py", "/abs.py", "a/../../evil.py", "C:/evil.py", "a\\..\\..\\evil.py"])
def test_path_traversal_rejected(tmp_path, bad):
    with pytest.raises(AddonError) as ei:
        addons.safe_unzip(wheel({"ok.py": b"1", bad: b"x"}), tmp_path / "out")
    assert ei.value.code == "UNSAFE_ARCHIVE"
    assert not (tmp_path / "evil.py").exists() and not (tmp_path / "out" / "ok.py").exists()   # validated before writing


def test_legit_nested_paths_and_symlink_rejected(tmp_path):
    tops = addons.safe_unzip(wheel({"pkg/sub/mod.py": b"1", "pkg-1.dist-info/RECORD": b""}), tmp_path / "out")
    assert tops == ["pkg", "pkg-1.dist-info"] and (tmp_path / "out" / "pkg" / "sub" / "mod.py").exists()
    with pytest.raises(AddonError):
        addons.safe_unzip(wheel({"a.py": b"1"}, symlink="lnk"), tmp_path / "out2")
    with pytest.raises(AddonError):
        addons.safe_unzip(b"not a zip", tmp_path / "out3")


def test_data_dir_purelib_flattened_scripts_dropped(tmp_path):
    blob = wheel({"x-1.data/purelib/xmod.py": b"1", "x-1.data/scripts/run.exe": b"MZ", "x-1.dist-info/M": b"m"})
    st = tmp_path / "st"
    addons.safe_unzip(blob, st)
    addons._promote(st, tmp_path / "pk")
    assert (tmp_path / "pk" / "xmod.py").exists() and not (tmp_path / "pk" / "run.exe").exists()


@pytest.mark.parametrize("url", ["http://pypi.org/x", "https://evil.example/x", "https://pypi.org.evil.com/x",
                                 "https://user:pw@pypi.org/x", "file:///etc/passwd"])
def test_only_https_pypi_hosts(url):
    with pytest.raises(AddonError) as ei:
        addons._check_url(url)
    assert ei.value.code == "HOST_NOT_ALLOWED"


def test_allowed_hosts_pass():
    addons._check_url("https://pypi.org/pypi/telethon/1.45.0/json")
    addons._check_url("https://files.pythonhosted.org/packages/ab/cd/x.whl")


def test_pypi_pointing_to_foreign_host_rejected(tmp_path):
    manifest, net, good = setup(tmp_path)

    def evil(url):
        if url.endswith("/json"):
            return json.dumps({"urls": [{"url": "https://evil.example/aaa.whl",
                                         "digests": {"sha256": manifest[0].sha256}}]}).encode()
        raise AssertionError("must not download from a foreign host")

    r = addons.install(tmp_path, downloader=evil, manifest=manifest)
    assert not r["ok"] and r["code"] == "HOST_NOT_ALLOWED"


def test_activate_adds_path_once_and_is_noop_without_install(tmp_path):
    assert addons.activate(tmp_path) is False
    p = addons.addon_path(tmp_path)
    p.mkdir(parents=True)
    try:
        assert addons.activate(tmp_path) and addons.activate(tmp_path)
        assert sys.path.count(str(p)) == 1
    finally:
        while str(p) in sys.path:
            sys.path.remove(str(p))


def test_installed_module_becomes_importable(tmp_path):
    manifest, net, _ = setup(tmp_path)
    addons.install(tmp_path, downloader=net, manifest=manifest)
    p = str(addons.addon_path(tmp_path))
    try:
        addons.activate(tmp_path)
        import importlib
        mod = importlib.import_module("aaa")
        assert mod.X == 1
    finally:
        sys.modules.pop("aaa", None)
        while p in sys.path:
            sys.path.remove(p)
