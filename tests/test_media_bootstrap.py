"""tools/media_bootstrap.py: manifest validation, download planning, opt-in download, manifest writing.

All files here are tiny temp files; no network, no model weights, no engine.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("media_bootstrap", ROOT / "tools/media_bootstrap.py")
boot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boot)


def _manifest(models: Path, *, urls=True) -> dict:
    engines = {}
    for engine, roles in (("wan2.2-ti2v-5b", ("diffusion", "vae", "text_encoder")), ("z-image-turbo", ("diffusion", "vae"))):
        files = {}
        for role in roles:
            data = f"{engine}/{role}".encode() * 32
            p = models / engine / f"{role}.gguf"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            files[role] = {"path": f"{engine}/{role}.gguf", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                           "revision": "abc123"}
            if urls:
                files[role]["url"] = f"https://example.invalid/{engine}/{role}.gguf"
        engines[engine] = {"files": files}
    manifest = {"schema_version": 1, "engine": {"release": "master-test"}, "engines": engines}
    (models / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


@pytest.fixture
def models(tmp_path):
    m = tmp_path / "models"
    _manifest(m)
    return m


def _status(rows, engine, role):
    return next(r["status"] for r in rows if r["engine"] == engine and r["role"] == role)


def test_validate_reports_each_state(models):
    manifest = boot.load_manifest(models)
    (models / "wan2.2-ti2v-5b" / "vae.gguf").unlink()
    p = models / "wan2.2-ti2v-5b" / "text_encoder.gguf"
    p.write_bytes(p.read_bytes() + b"x")
    q = models / "z-image-turbo" / "vae.gguf"
    data = bytearray(q.read_bytes()); data[3] ^= 0xFF; q.write_bytes(bytes(data))  # same size
    rows = boot.validate_files(models, manifest)
    assert _status(rows, "wan2.2-ti2v-5b", "diffusion") == "PRESENT_OK"
    assert _status(rows, "wan2.2-ti2v-5b", "vae") == "MISSING"
    assert _status(rows, "wan2.2-ti2v-5b", "text_encoder") == "SIZE_MISMATCH"
    assert _status(rows, "z-image-turbo", "vae") == "PRESENT_BAD_HASH"
    assert _status(rows, "z-image-turbo", "diffusion") == "PRESENT_OK"
    bad = boot.needs_download(rows)
    assert {(r["engine"], r["role"]) for r in bad} == {("wan2.2-ti2v-5b", "vae"), ("wan2.2-ti2v-5b", "text_encoder"),
                                                       ("z-image-turbo", "vae")}
    for r in rows:  # expected and observed are separate fields, never copied
        if r["status"] == "PRESENT_BAD_HASH":
            assert r["sha256_observed"] != r["sha256_expected"]


def test_validate_cli_exit_codes_and_report_file(models, capsys, tmp_path):
    report = tmp_path / "out" / "media-manifest.json"
    assert boot.main(["validate", str(models), "--json", "--out", str(report)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "OK" and out["binary"]["status"] == "BINARY_NOT_GIVEN"
    written = json.loads(report.read_text(encoding="utf-8"))
    assert written["verdict"] == "OK" and len(written["files"]) == 5
    assert all(f["status"] == "PRESENT_OK" and f["path"] for f in written["files"])  # owner_run_tomorrow contract
    (models / "z-image-turbo" / "vae.gguf").unlink()
    assert boot.main(["validate", str(models), "--json", "--out", str(report)]) == 1
    assert json.loads(capsys.readouterr().out)["verdict"] == "INVALID"
    written = json.loads(report.read_text(encoding="utf-8"))
    assert written["verdict"] == "INVALID"
    assert {f["status"] for f in written["files"]} == {"PRESENT_OK", "MISSING"}
    (models / "MANIFEST.json").write_text("{}", encoding="utf-8")
    assert boot.main(["validate", str(models), "--json", "--out", str(report)]) == 2
    printed = json.loads(capsys.readouterr().out)
    assert printed["verdict"] == "INVALID" and printed["reason"] == "MANIFEST_INVALID"
    assert json.loads(report.read_text(encoding="utf-8"))["verdict"] == "INVALID"
    assert not list(report.parent.glob("*.tmp"))


def test_models_dir_from_env_not_hardcoded(models, monkeypatch, capsys):
    source = (ROOT / "tools/media_bootstrap.py").read_text(encoding="utf-8")
    assert "Users\\\\asd" not in source and "Users/asd" not in source and "C:\\\\" not in source
    monkeypatch.setenv("BOSSMAN_MEDIA_MODELS", str(models))
    assert boot.main(["validate", "--json"]) == 0
    monkeypatch.delenv("BOSSMAN_MEDIA_MODELS")
    with pytest.raises(SystemExit):
        boot.main(["validate", "--json"])


def test_binary_pin_checked(models, tmp_path):
    exe = tmp_path / "sd-cli"
    exe.write_bytes(b"fake engine binary")
    manifest = boot.load_manifest(models)
    assert boot.validate_binary(manifest, str(exe))["status"] == "BINARY_UNPINNED"
    manifest["engine"]["binary_sha256"] = hashlib.sha256(b"fake engine binary").hexdigest()
    assert boot.validate_binary(manifest, str(exe))["status"] == "BINARY_OK"
    manifest["engine"]["binary_sha256"] = "0" * 64
    assert boot.validate_binary(manifest, str(exe))["status"] == "BINARY_BAD_HASH"
    assert boot.validate_binary(manifest, str(tmp_path / "gone"))["status"] == "BINARY_MISSING"


@pytest.mark.parametrize("path", ["../x.gguf", "/abs.gguf", "C:\\x.gguf", "a/../b.gguf", "a\\b.gguf", ""])
def test_manifest_path_escapes_rejected(models, path):
    m = json.loads((models / "MANIFEST.json").read_text(encoding="utf-8"))
    m["engines"]["z-image-turbo"]["files"]["vae"]["path"] = path
    (models / "MANIFEST.json").write_text(json.dumps(m), encoding="utf-8")
    with pytest.raises(ValueError, match="path"):
        boot.load_manifest(models)


@pytest.mark.parametrize("mutate", [
    lambda m: m.pop("schema_version"),
    lambda m: m.__setitem__("engine", {}),
    lambda m: m.__setitem__("engines", {}),
    lambda m: m["engines"]["z-image-turbo"]["files"]["vae"].__setitem__("sha256", "ABC"),
    lambda m: m["engines"]["z-image-turbo"]["files"]["vae"].__setitem__("bytes", "12"),
    lambda m: m["engine"].__setitem__("binary_sha256", "zz"),
])
def test_manifest_schema_rejections(models, mutate):
    m = json.loads((models / "MANIFEST.json").read_text(encoding="utf-8"))
    mutate(m)
    with pytest.raises(ValueError):
        boot.validate_manifest(m)


def test_symlink_inside_models_dir_rejected(models, tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("no symlinks")
    target = models / "z-image-turbo" / "vae.gguf"
    outside = tmp_path / "outside.gguf"
    outside.write_bytes(target.read_bytes())
    target.unlink()
    try:
        os.symlink(outside, target)
    except OSError:
        pytest.skip("symlink not permitted")
    rows = boot.validate_files(models, boot.load_manifest(models))
    assert _status(rows, "z-image-turbo", "vae") == "MISSING"


def test_plan_download_lists_only_missing_or_bad_without_network(models, capsys, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("plan-download must not fetch")

    monkeypatch.setattr(boot, "fetch_url", no_network)
    monkeypatch.setattr(boot.urllib.request, "urlopen", no_network)
    (models / "wan2.2-ti2v-5b" / "vae.gguf").unlink()
    q = models / "z-image-turbo" / "diffusion.gguf"
    data = bytearray(q.read_bytes()); data[0] ^= 1; q.write_bytes(bytes(data))
    assert boot.main(["plan-download", str(models)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["downloads_performed"] == 0
    assert {(p["engine"], p["role"], p["status"]) for p in out["plan"]} == {
        ("wan2.2-ti2v-5b", "vae", "MISSING"), ("z-image-turbo", "diffusion", "PRESENT_BAD_HASH")}
    assert all(p["url"].startswith("https://") for p in out["plan"])
    assert out["total_bytes"] == sum(p["bytes"] for p in out["plan"])
    # file without url is listed as NO_URL, still never fetched
    m = json.loads((models / "MANIFEST.json").read_text(encoding="utf-8"))
    del m["engines"]["wan2.2-ti2v-5b"]["files"]["vae"]["url"]
    (models / "MANIFEST.json").write_text(json.dumps(m), encoding="utf-8")
    boot.main(["plan-download", str(models)])
    out = json.loads(capsys.readouterr().out)
    assert next(p for p in out["plan"] if p["role"] == "vae")["url"] == "NO_URL"


def test_download_refused_without_opt_in(models, capsys, monkeypatch):
    monkeypatch.setattr(boot, "fetch_url", lambda *a, **k: pytest.fail("fetched without opt-in"))
    (models / "wan2.2-ti2v-5b" / "vae.gguf").unlink()
    assert boot.main(["download", str(models)]) == 3
    assert json.loads(capsys.readouterr().out)["verdict"] == "REFUSED"
    assert not (models / "wan2.2-ti2v-5b" / "vae.gguf").exists()


def test_download_verifies_sha256_via_part_then_rename(models, monkeypatch):
    manifest = boot.load_manifest(models)
    good = manifest["engines"]["wan2.2-ti2v-5b"]["files"]["vae"]
    good_bytes = b"wan2.2-ti2v-5b/vae" * 32
    assert hashlib.sha256(good_bytes).hexdigest() == good["sha256"]
    (models / "wan2.2-ti2v-5b" / "vae.gguf").unlink()
    te = models / "wan2.2-ti2v-5b" / "text_encoder.gguf"
    te.write_bytes(te.read_bytes() + b"x")
    te_spec = manifest["engines"]["wan2.2-ti2v-5b"]["files"]["text_encoder"]
    served = {good["url"]: good_bytes,
              te_spec["url"]: b"w" * te_spec["bytes"]}  # right size, wrong bytes: only the hash can catch it
    seen_parts = []

    def fake_fetch(url, part, *, expected_bytes, log=None, timeout=60):
        assert part.name.endswith(".part")
        seen_parts.append(part)
        part.write_bytes(served[url][:expected_bytes])
        return min(len(served[url]), expected_bytes)

    rows = boot.validate_files(models, manifest)
    report = boot.download_missing(models, manifest, rows, allow=True, fetch=fake_fetch)
    by_role = {r["role"]: r for r in report}
    assert by_role["vae"]["download"] == "VERIFIED" and (models / "wan2.2-ti2v-5b" / "vae.gguf").read_bytes() == good_bytes
    assert by_role["text_encoder"]["download"] == "FAILED" and "sha256" in by_role["text_encoder"]["detail"]
    assert not list(models.rglob("*.part")), "failed download must not leave a .part"
    assert len(seen_parts) == 2
    # untouched good files were not re-downloaded
    assert set(by_role) == {"vae", "text_encoder"}
    after = boot.validate_files(models, manifest)
    assert _status(after, "wan2.2-ti2v-5b", "vae") == "PRESENT_OK"
    assert _status(after, "wan2.2-ti2v-5b", "text_encoder") == "SIZE_MISMATCH"


def test_download_rejects_non_http_url(models, tmp_path):
    manifest = boot.load_manifest(models)
    manifest["engines"]["z-image-turbo"]["files"]["vae"]["url"] = f"file://{tmp_path}/x"
    (models / "z-image-turbo" / "vae.gguf").unlink()
    rows = boot.validate_files(models, manifest)
    report = boot.download_missing(models, manifest, rows, allow=True)
    assert report[0]["download"] == "FAILED" and "scheme" in report[0]["detail"]


def test_write_manifest_hashes_real_files_and_roundtrips(tmp_path, capsys):
    models = tmp_path / "owner-models"
    for rel, data in (("wan2.2-ti2v-5b/wan2.2_ti2v_5B_Q8_0.gguf", b"D" * 100), ("wan2.2-ti2v-5b/wan2.2_vae.safetensors", b"V" * 50),
                      ("wan2.2-ti2v-5b/umt5-xxl-encoder-Q8_0.gguf", b"T" * 70), ("z-image-turbo/z_image_turbo-Q8_0.gguf", b"Z" * 10),
                      ("z-image-turbo/ae.safetensors", b"a" * 5), ("z-image-turbo/Qwen3-4B-Q8_0.gguf", b"q" * 8)):
        p = models / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    exe = tmp_path / "sd-cli.exe"
    exe.write_bytes(b"engine")
    assert boot.main(["write-manifest", str(models), "--release", "master-890-74988b2", "--bin", str(exe),
                      "--file", "z-image-turbo:vae=z-image-turbo/ae.safetensors", "--auto"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "WRITTEN" and out["files"] == 6 and out["review_needed"]
    manifest = boot.load_manifest(models)
    assert manifest["engine"]["binary_sha256"] == hashlib.sha256(b"engine").hexdigest()
    wan = manifest["engines"]["wan2.2-ti2v-5b"]["files"]
    assert set(wan) == {"diffusion", "vae", "text_encoder"}
    assert wan["diffusion"]["bytes"] == 100 and wan["diffusion"]["sha256"] == hashlib.sha256(b"D" * 100).hexdigest()
    assert manifest["engines"]["z-image-turbo"]["files"]["vae"]["path"] == "z-image-turbo/ae.safetensors"
    rows = boot.validate_files(models, manifest)
    assert all(r["status"] == "PRESENT_OK" for r in rows)
    # refuses to overwrite without --force; --merge keeps url/revision
    assert boot.main(["write-manifest", str(models), "--release", "x", "--auto"]) == 3
    m = json.loads((models / "MANIFEST.json").read_text(encoding="utf-8"))
    m["engines"]["wan2.2-ti2v-5b"]["files"]["vae"]["url"] = "https://example.invalid/vae"
    m["engines"]["wan2.2-ti2v-5b"]["files"]["vae"]["revision"] = "57437632"
    (models / "MANIFEST.json").write_text(json.dumps(m), encoding="utf-8")
    assert boot.main(["write-manifest", str(models), "--release", "x", "--auto", "--merge", "--force"]) == 0
    capsys.readouterr()
    merged = boot.load_manifest(models)
    assert merged["engines"]["wan2.2-ti2v-5b"]["files"]["vae"]["url"] == "https://example.invalid/vae"
    assert merged["engines"]["wan2.2-ti2v-5b"]["files"]["vae"]["revision"] == "57437632"


def test_guess_role_covers_owner_file_names():
    assert boot.guess_role("wan2.2_vae.safetensors") == "vae"
    assert boot.guess_role("ae.safetensors") == "vae"
    assert boot.guess_role("umt5-xxl-encoder-Q8_0.gguf") == "text_encoder"
    assert boot.guess_role("Qwen3-4B-Q8_0.gguf") == "text_encoder"
    assert boot.guess_role("wan2.2_ti2v_5B_Q8_0.gguf") == "diffusion"
    assert boot.guess_role("z_image_turbo-Q8_0.gguf") == "diffusion"


def test_write_manifest_rejects_duplicate_role_and_missing_file(tmp_path):
    models = tmp_path / "m"
    (models / "e").mkdir(parents=True)
    (models / "e" / "a.gguf").write_bytes(b"a")
    with pytest.raises(FileNotFoundError):
        boot.hash_tree(models, release="r", files=["e:diffusion=e/none.gguf"])
    with pytest.raises(ValueError, match="twice"):
        boot.hash_tree(models, release="r", files=["e:diffusion=e/a.gguf", "e:diffusion=e/a.gguf"])
    with pytest.raises(ValueError, match="ENGINE:ROLE"):
        boot.hash_tree(models, release="r", files=["garbage"])
    with pytest.raises(ValueError, match="nothing to hash"):
        boot.hash_tree(models, release="r")


def test_manifest_schema_agrees_with_provider(models):
    """The provider's validator and the bootstrap validator accept/reject the same manifests."""
    cc = ROOT / "command-center"
    if str(cc) not in sys.path:
        sys.path.insert(0, str(cc))
    sdcpp = pytest.importorskip("bcc.studio.providers.sdcpp")
    manifest = boot.load_manifest(models)
    assert sdcpp.validate_manifest(json.loads(json.dumps(manifest)))
    bad = json.loads(json.dumps(manifest))
    bad["engines"]["z-image-turbo"]["files"]["vae"]["path"] = "../x"
    with pytest.raises(ValueError):
        sdcpp.validate_manifest(bad)
    with pytest.raises(ValueError):
        boot.validate_manifest(bad)


def test_configure_writes_media_config_after_validation(models, tmp_path, capsys):
    exe = tmp_path / "sd-cli"
    exe.write_bytes(b"engine")
    data_dir = tmp_path / "data"
    assert boot.main(["configure", "--sdcpp-bin", str(tmp_path / "gone"), "--models-dir", str(models), "--data-dir", str(data_dir)]) == 1
    capsys.readouterr()
    assert not (data_dir / "media" / "config.json").exists()
    assert boot.main(["configure", "--sdcpp-bin", str(exe), "--models-dir", str(models), "--data-dir", str(data_dir)]) == 0
    out = json.loads(capsys.readouterr().out)
    cfg = json.loads((data_dir / "media" / "config.json").read_text(encoding="utf-8"))
    assert out["verdict"] == "WRITTEN" and Path(cfg["sdcpp_bin"]) == exe.resolve() and Path(cfg["models_dir"]) == models.resolve()
    assert cfg["written_at"] and not list((data_dir / "media").glob("*.tmp"))
    # the sdcpp provider reads exactly this file when env vars are absent
    cc = ROOT / "command-center"
    if str(cc) not in sys.path:
        sys.path.insert(0, str(cc))
    sdcpp = pytest.importorskip("bcc.studio.providers.sdcpp")
    assert Path(sdcpp.media_config_path(data_dir)) == data_dir / "media" / "config.json"


def test_tools_put_console_into_utf8_before_printing():
    for name in ("media_bootstrap.py", "media_ab_preset.py"):
        source = (ROOT / "tools" / name).read_text(encoding="utf-8")
        assert 'reconfigure(encoding="utf-8", errors="replace")' in source, name
        body = source[source.index("def main("):]
        assert body.splitlines()[1].strip() == "utf8_console()", name
