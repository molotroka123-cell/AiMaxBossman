"""Red-team tests for the stable-diffusion.cpp Studio provider.

Every engine run here is a MOCK_ENGINE: a Python child (``FAKE_ENGINE``) that
writes a tiny testsrc clip/PNG with ffmpeg or misbehaves on purpose (hangs,
prints megabytes without a newline, exits non-zero, writes a corrupt container).
``SdCppProvider.fake = True`` keeps dispatch from writing the "verified" probe.
Nothing in this file is evidence of real generation.

Each strictness change carries a negative control: the legitimate case passes
and the genuinely bad case is rejected.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from bcc.studio import catalog
from bcc.studio.provider import GenerationPlane, ProviderFailure, ProviderOutput
from bcc.studio.providers import sdcpp

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")

MOCK_ENGINE = r'''
# MOCK_ENGINE — never a real generation. Modes are the first argv element.
import os, subprocess, sys, time, shutil
mode = sys.argv[1]
out = sys.argv[sys.argv.index("-o") + 1]
w = sys.argv[sys.argv.index("-W") + 1]; h = sys.argv[sys.argv.index("-H") + 1]
ffmpeg = shutil.which("ffmpeg")
def render():
    if out.endswith(".webm"):
        # the frames that were asked for, like the real engine (a wrong length is refused)
        fps = sys.argv[sys.argv.index("--fps") + 1] if "--fps" in sys.argv else "16"
        frames = sys.argv[sys.argv.index("--video-frames") + 1] if "--video-frames" in sys.argv else "16"
        subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={w}x{h}:rate={fps}",
                        "-frames:v", frames, "-c:v", "libvpx", out], check=True)
    else:
        subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={w}x{h}",
                        "-frames:v", "1", out], check=True)
if mode == "slow":
    print("MOCK_ENGINE sleeping", flush=True); time.sleep(120)
elif mode == "slow-with-child":
    print("MOCK_ENGINE child", flush=True)
    subprocess.run([sys.executable, "-c", "import time; time.sleep(120)"])
elif mode == "fail":
    print("MOCK_ENGINE: model load failed"); sys.exit(3)
elif mode == "longline":
    sys.stdout.write("x" * (6 * 1024 * 1024)); sys.stdout.flush(); render(); print("done")
elif mode == "corrupt":
    with open(out, "wb") as fh:
        fh.write(b"\x1aE\xdf\xa3" + os.urandom(4096) if out.endswith(".webm") else b"\x89PNG\r\n\x1a\n" + os.urandom(64))
    print("MOCK_ENGINE wrote corrupt container, exit 0")
elif mode == "empty":
    open(out, "wb").close(); print("MOCK_ENGINE wrote empty file")
elif mode == "vulkan-log":
    print("ggml_vulkan: Found 1 Vulkan devices:"); print("ggml_vulkan: 0 = MOCK_ENGINE device (no real GPU)"); render()
else:
    render(); print("MOCK_ENGINE: done")
'''

ROLES = ("diffusion", "vae", "text_encoder")


def _model(model_id):
    return next(m for m in catalog.load()["models"] if m["id"] == model_id)


def write_models(models: Path, *, contents=None) -> dict:
    """Manifest + files for both engines; returns the manifest dict."""
    contents = contents or {}
    engines = {}
    for name in ("wan2.2-ti2v-5b", "z-image-turbo"):
        files = {}
        for role in ROLES:
            data = contents.get((name, role), f"{name}:{role}".encode() * 64)
            p = models / name / f"{role}.gguf"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            files[role] = {"path": f"{name}/{role}.gguf", "bytes": len(data),
                           "sha256": hashlib.sha256(data).hexdigest(), "revision": "deadbeef"}
        engines[name] = {"files": files}
    manifest = {"schema_version": 1, "engine": {"release": "MOCK_ENGINE"}, "engines": engines}
    (models / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


class Harness:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp = tmp_path
        self.models = tmp_path / "media"
        self.manifest = write_models(self.models)
        self.script = tmp_path / "mock_engine.py"
        self.script.write_text(MOCK_ENGINE, encoding="utf-8")
        self.storage = tmp_path / "studio"
        self.cache_dir = tmp_path / "data" / "studio" / "engine-work"
        monkeypatch.setenv(sdcpp.BIN_ENV, sys.executable)
        monkeypatch.setenv(sdcpp.MODELS_ENV, str(self.models))
        monkeypatch.setenv("BCC_DATA_DIR", str(tmp_path / "data"))
        self.mode = "ok"
        real_argv = sdcpp._argv

        def fake_argv(cfg, model_id, plane, settings, files_, out, init):
            argv = real_argv(cfg, model_id, plane, settings, files_, out, init)
            if self.mode == "nobinary":
                return [str(tmp_path / "missing" / "sd-cli"), *argv[1:]]
            return [sys.executable, str(self.script), self.mode, *argv[1:]]

        monkeypatch.setattr(sdcpp, "_argv", fake_argv)
        monkeypatch.setattr(sdcpp.SdCppProvider, "fake", True, raising=False)

    def cfg(self):
        return sdcpp.configuration()

    def provider(self, model_id="sdcpp:z-image-turbo", cls=None):
        cls = cls or sdcpp.SdCppProvider
        return cls(self.cfg(), self.storage, _model(model_id))

    def work(self):
        return self.storage.resolve() / "engine-work"


@pytest.fixture
def harness(tmp_path, monkeypatch):
    return Harness(tmp_path, monkeypatch)


def plane(model_id="sdcpp:z-image-turbo", media=(), **settings):
    base = {"width": 256, "height": 256, "steps": 4, "seed": 1} if model_id.endswith("turbo") else \
        {"width": 640, "height": 352, "frames": 17, "fps": 16, "steps": 16, "seed": 1}
    return GenerationPlane(model_id, "MOCK_ENGINE prompt", {**base, **settings}, tuple(media))


async def wait_done(provider, rid, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = await provider.status(rid)
        if st.state != "running":
            return st
        await asyncio.sleep(0.1)
    raise AssertionError("provider never left running")


def png_bytes(w=32, h=32):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 200, 30)).save(buf, format="PNG")
    return buf.getvalue()


def data_uri(data: bytes, mime="image/png"):
    return f"data:{mime};base64," + base64.b64encode(data).decode()


# --------------------------------------------------------------------------- MEDIA-HASH

def test_manifest_requires_schema_fields(harness):
    bad = dict(harness.manifest)
    bad.pop("schema_version")
    (harness.models / "MANIFEST.json").write_text(json.dumps(bad), encoding="utf-8")
    assert sdcpp.configuration() is None  # never raises on the product path
    with pytest.raises(ValueError, match="schema_version"):
        sdcpp.configuration(strict=True)


@pytest.mark.parametrize("path", ["../outside.gguf", "/abs/file.gguf", "C:\\abs\\x.gguf", "a/../../b.gguf", ""])
def test_manifest_path_escape_rejected(harness, path):
    bad = json.loads(json.dumps(harness.manifest))
    bad["engines"]["z-image-turbo"]["files"]["vae"]["path"] = path
    (harness.models / "MANIFEST.json").write_text(json.dumps(bad), encoding="utf-8")
    assert sdcpp.configuration() is None
    with pytest.raises(ValueError, match="path"):
        sdcpp.configuration(strict=True)


def test_manifest_legit_passes_and_bad_sha_format_rejected(harness):
    assert sdcpp.configuration() is not None  # negative control: the legit manifest is accepted
    bad = json.loads(json.dumps(harness.manifest))
    bad["engines"]["z-image-turbo"]["files"]["vae"]["sha256"] = "not-a-hash"
    (harness.models / "MANIFEST.json").write_text(json.dumps(bad), encoding="utf-8")
    assert sdcpp.configuration() is None
    with pytest.raises(ValueError, match="sha256"):
        sdcpp.configuration(strict=True)


def test_same_size_corruption_is_detected_by_real_hashing(harness):
    cfg = harness.cfg()
    report = sdcpp.verify_engine_files(cfg, "sdcpp:z-image-turbo", mode="force")
    for role in ROLES:
        assert report[role]["sha256_expected"] == report[role]["sha256_observed"]
        assert report[role]["method"] == "full"
    target = harness.models / "z-image-turbo" / "vae.gguf"
    st = target.stat()
    data = bytearray(target.read_bytes())
    data[len(data) // 2] ^= 0xFF  # same size
    target.write_bytes(bytes(data))
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))  # restore mtime
    assert target.stat().st_size == st.st_size and target.stat().st_mtime_ns == st.st_mtime_ns
    with pytest.raises(ValueError, match="sha256"):
        sdcpp.verify_engine_files(cfg, "sdcpp:z-image-turbo", mode="force")
    with pytest.raises(ValueError, match="sha256"):  # submit path rehashes small files fully
        sdcpp.verify_engine_files(cfg, "sdcpp:z-image-turbo", mode="submit")
    with pytest.raises((ValueError, OSError)):
        sdcpp.engine_files(sdcpp.configuration(cache_dir=harness.tmp / "fresh-cache"), "sdcpp:z-image-turbo")


def test_first_observation_always_hashes_and_health_does_not_rehash(harness, monkeypatch):
    calls = []
    real = sdcpp._sha256

    def counted(path):
        calls.append(Path(path).name)
        return real(path)

    monkeypatch.setattr(sdcpp, "_sha256", counted)
    cfg = harness.cfg()
    # a cache entry written by hand with correct stat keys must not be trusted: first observation hashes
    assert sdcpp.engine_files(cfg, "sdcpp:z-image-turbo")
    first = len(calls)
    assert first >= len(ROLES), calls
    for _ in range(5):
        assert sdcpp.engine_files(cfg, "sdcpp:z-image-turbo")
    assert len(calls) == first, "health call rehashed the model files"
    # a new process (fresh module state) but same persisted cache: still no rehash
    assert sdcpp.engine_files(sdcpp.configuration(), "sdcpp:z-image-turbo")
    assert len(calls) == first
    # expected sha in the manifest changes -> cache entry is not reused
    changed = json.loads(json.dumps(harness.manifest))
    changed["engines"]["z-image-turbo"]["files"]["vae"]["sha256"] = "0" * 64
    (harness.models / "MANIFEST.json").write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="sha256"):
        sdcpp.engine_files(sdcpp.configuration(), "sdcpp:z-image-turbo")
    assert len(calls) == first + 1


def test_cache_entry_never_trusts_stat_alone_for_first_verification(harness):
    cfg = harness.cfg()
    target = harness.models / "z-image-turbo" / "vae.gguf"
    st = target.stat()
    # forge a cache file claiming the file was verified, with matching stat keys but wrong bytes on disk
    data = bytearray(target.read_bytes()); data[0] ^= 1; target.write_bytes(bytes(data))
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))
    forged = {"version": 1, "entries": {str(target.resolve()): {
        "size": st.st_size, "mtime_ns": st.st_mtime_ns, "ctime_ns": target.stat().st_ctime_ns,
        "inode": st.st_ino, "sha256_expected": harness.manifest["engines"]["z-image-turbo"]["files"]["vae"]["sha256"],
        "sha256_observed": harness.manifest["engines"]["z-image-turbo"]["files"]["vae"]["sha256"],
        "sample": "", "verified_at": time.time()}}}
    cache_path = Path(cfg["cache_dir"]) / sdcpp.HASH_CACHE_NAME
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(forged), encoding="utf-8")
    # the cache carries no proof this process ever hashed the file: the first observation hashes anyway
    with pytest.raises(ValueError, match="sha256"):
        sdcpp.engine_files(cfg, "sdcpp:z-image-turbo")


def test_large_file_submit_policy_sampled_and_force_full(harness, monkeypatch):
    """Honest policy: big files on submit = stat keys + sampled windows; force = full hash.

    On Linux a rewrite bumps st_ctime_ns (not forgeable), so the stat key alone already
    misses the cache and the full hash catches the change. The sampled limit only shows
    on filesystems whose ctime is a creation time (Windows): simulated by a stat key
    without ctime/inode.
    """
    monkeypatch.setattr(sdcpp, "SMALL_FILE_FULL_HASH_BYTES", 16)
    monkeypatch.setattr(sdcpp, "SAMPLE_WINDOW_BYTES", 8)
    real_key = sdcpp._stat_key
    monkeypatch.setattr(sdcpp, "_stat_key", lambda path: {**real_key(path), "ctime_ns": 0, "inode": 0})
    cfg = harness.cfg()
    target = harness.models / "z-image-turbo" / "diffusion.gguf"
    size = target.stat().st_size
    assert size > 3 * 8
    base = sdcpp.verify_engine_files(cfg, "sdcpp:z-image-turbo", mode="force")
    assert base["diffusion"]["method"] == "full"
    ok = sdcpp.verify_engine_files(cfg, "sdcpp:z-image-turbo", mode="submit")
    assert ok["diffusion"]["method"] == "sampled" and ok["vae"]["method"] == "sampled"

    def corrupt(offset):
        st = target.stat()
        data = bytearray(target.read_bytes()); data[offset] ^= 0xFF; target.write_bytes(bytes(data))
        os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))

    corrupt(size // 2)  # inside the middle sample window
    with pytest.raises(ValueError, match="sha256"):
        sdcpp.verify_engine_files(cfg, "sdcpp:z-image-turbo", mode="submit")
    corrupt(size // 2)  # restore
    corrupt(12)  # between head window [0,8) and middle window: NOT covered by sampling
    assert sdcpp.verify_engine_files(cfg, "sdcpp:z-image-turbo", mode="submit")["diffusion"]["method"] == "sampled"
    with pytest.raises(ValueError, match="sha256"):  # only the full policy catches it
        sdcpp.verify_engine_files(cfg, "sdcpp:z-image-turbo", mode="force")
    # negative control for the stat key itself: with the real key (ctime bumped by the rewrite)
    # the cache misses and even the submit path falls back to a full hash that catches it
    monkeypatch.setattr(sdcpp, "_stat_key", real_key)
    with pytest.raises(ValueError, match="sha256"):
        sdcpp.verify_engine_files(cfg, "sdcpp:z-image-turbo", mode="submit")


# --------------------------------------------------------------------------- configuration fallback

def _write_media_config(data_dir: Path, **fields):
    path = data_dir / "media" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fields) if fields else "{not json", encoding="utf-8")
    return path


def test_configuration_file_fallback_when_env_unset(harness, monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    monkeypatch.delenv(sdcpp.BIN_ENV); monkeypatch.delenv(sdcpp.MODELS_ENV)
    assert sdcpp.configuration() is None
    written = sdcpp.write_configuration(sys.executable, harness.models, data_dir)
    assert written == data_dir / "media" / "config.json"
    cfg = sdcpp.configuration()
    assert cfg is not None and cfg["root"] == harness.models.resolve() and cfg["bin"] == Path(sys.executable).resolve()
    rep = sdcpp.describe_configuration()
    assert rep["source"] == {"bin": "file", "models": "file"} and rep["error"] is None


def test_configuration_env_wins_over_file(harness, monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    other = tmp_path / "other-models"
    write_models(other)
    sdcpp.write_configuration(sys.executable, other, data_dir)
    cfg = sdcpp.configuration()  # env still points at harness.models
    assert cfg["root"] == harness.models
    assert sdcpp.describe_configuration()["source"] == {"bin": "env", "models": "env"}
    monkeypatch.delenv(sdcpp.MODELS_ENV)  # per-variable fallback: bin from env, models from file
    cfg = sdcpp.configuration()
    assert cfg["root"] == other.resolve() and sdcpp.describe_configuration()["source"] == {"bin": "env", "models": "file"}


@pytest.mark.parametrize("case", ["garbage", "missing_key", "wrong_type", "bin_missing", "manifest_invalid", "absent"])
def test_invalid_media_config_never_raises(harness, monkeypatch, tmp_path, case):
    data_dir = tmp_path / "data"
    monkeypatch.delenv(sdcpp.BIN_ENV); monkeypatch.delenv(sdcpp.MODELS_ENV)
    if case == "garbage":
        _write_media_config(data_dir)
    elif case == "missing_key":
        _write_media_config(data_dir, sdcpp_bin=sys.executable)
    elif case == "wrong_type":
        _write_media_config(data_dir, sdcpp_bin=["x"], models_dir=str(harness.models))
    elif case == "bin_missing":
        _write_media_config(data_dir, sdcpp_bin=str(tmp_path / "gone.exe"), models_dir=str(harness.models))
    elif case == "manifest_invalid":
        (harness.models / "MANIFEST.json").write_text("{}", encoding="utf-8")
        _write_media_config(data_dir, sdcpp_bin=sys.executable, models_dir=str(harness.models))
    assert sdcpp.configuration() is None
    assert sdcpp.describe_configuration()["error"]


def test_write_configuration_validates_before_writing(harness, tmp_path):
    data_dir = tmp_path / "data"
    with pytest.raises(FileNotFoundError):
        sdcpp.write_configuration(tmp_path / "gone.exe", harness.models, data_dir)
    (harness.models / "MANIFEST.json").write_text(json.dumps({"schema_version": 2}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        sdcpp.write_configuration(sys.executable, harness.models, data_dir)
    assert not (data_dir / "media" / "config.json").exists()
    assert not list((data_dir / "media").glob("*.tmp")) if (data_dir / "media").exists() else True


def test_bcc_data_dir_redirects_media_config(harness, monkeypatch, tmp_path):
    monkeypatch.delenv(sdcpp.BIN_ENV); monkeypatch.delenv(sdcpp.MODELS_ENV)
    redirected = tmp_path / "redirected-data"
    sdcpp.write_configuration(sys.executable, harness.models, redirected)
    assert sdcpp.configuration() is None  # BCC_DATA_DIR still points at tmp/data
    monkeypatch.setenv("BCC_DATA_DIR", str(redirected))
    assert sdcpp.configuration() is not None
    assert sdcpp.media_config_path() == redirected / "media" / "config.json"


async def test_models_endpoint_configured_from_file_only(env, engine_file_only):
    models = {m["id"]: m for m in (await env.client.get("/api/studio/models")).json()["items"]}
    assert models["sdcpp:z-image-turbo"]["configured"] is True
    assert models["sdcpp:z-image-turbo"]["verified"] is False


@pytest.fixture
def engine_file_only(env, harness, monkeypatch):
    monkeypatch.delenv(sdcpp.BIN_ENV); monkeypatch.delenv(sdcpp.MODELS_ENV)
    sdcpp.write_configuration(sys.executable, harness.models, Path(env.settings.data_dir))
    monkeypatch.setenv("BCC_DATA_DIR", str(env.settings.data_dir))
    return harness


def test_binary_hash_checked_against_manifest(harness):
    m = json.loads(json.dumps(harness.manifest))
    m["engine"]["binary_sha256"] = "1" * 64
    (harness.models / "MANIFEST.json").write_text(json.dumps(m), encoding="utf-8")
    with pytest.raises(ValueError, match="binary"):
        sdcpp.engine_files(sdcpp.configuration(), "sdcpp:z-image-turbo")
    m["engine"]["binary_sha256"] = hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
    (harness.models / "MANIFEST.json").write_text(json.dumps(m), encoding="utf-8")
    assert sdcpp.engine_files(sdcpp.configuration(), "sdcpp:z-image-turbo")
    report = sdcpp.verify_engine_binary(sdcpp.configuration())
    assert report["match"] is True and report["sha256_expected"] == report["sha256_observed"]


def test_symlink_in_models_dir_rejected_regular_file_accepted(harness, tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("no symlinks")
    cfg = harness.cfg()
    assert sdcpp.engine_files(cfg, "sdcpp:z-image-turbo")  # negative control
    target = harness.models / "z-image-turbo" / "vae.gguf"
    outside = tmp_path / "outside.gguf"
    outside.write_bytes(target.read_bytes())
    target.unlink()
    try:
        os.symlink(outside, target)
    except OSError:
        pytest.skip("symlink not permitted")
    with pytest.raises(PermissionError, match="symlink"):
        sdcpp.engine_files(cfg, "sdcpp:z-image-turbo")


def test_symlinked_directory_rejected(harness, tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("no symlinks")
    cfg = harness.cfg()
    real_dir = harness.models / "z-image-turbo"
    moved = tmp_path / "moved-engine"
    shutil.move(str(real_dir), str(moved))
    try:
        os.symlink(moved, real_dir, target_is_directory=True)
    except OSError:
        pytest.skip("symlink not permitted")
    with pytest.raises(PermissionError):
        sdcpp.engine_files(cfg, "sdcpp:z-image-turbo")


async def test_provenance_separates_expected_and_observed_hashes(harness):
    p = harness.provider()
    sub = await p.submit(plane())
    st = await wait_done(p, sub.request_id)
    assert st.state == "completed"
    dest = harness.storage / "out.png"
    await p.fetch(st.outputs[0], dest)
    trace = p.traces[sub.request_id]
    files = trace["generation"]["model_files"]
    for role in ROLES:
        assert files[role]["sha256_expected"] == files[role]["sha256_observed"]
        assert files[role]["revision_declared"] == "deadbeef"
        assert files[role]["verification"]["method"] in ("full", "sampled", "cache")
    assert trace["generation"]["engine_binary"]["sha256_observed"] and \
        trace["generation"]["engine_binary"]["sha256_expected"] is None


# --------------------------------------------------------------------------- MEDIA-CANCEL

class SlowSpawn(sdcpp.SdCppProvider):
    async def _spawn(self, job):
        await asyncio.sleep(0.6)
        return await super()._spawn(job)


@pytest.fixture
def spawn_counter(monkeypatch):
    calls = []
    real = sdcpp._create_subprocess

    async def counted(*argv, **kw):
        calls.append(argv)
        return await real(*argv, **kw)

    monkeypatch.setattr(sdcpp, "_create_subprocess", counted)
    return calls


async def test_cancel_before_task_starts_never_spawns(harness, spawn_counter):
    p = harness.provider()
    sub = await p.submit(plane())
    await p.cancel(sub.request_id)  # the _run task has not run a single step yet
    st = await p.status(sub.request_id)
    assert st.state == "canceled"
    assert spawn_counter == []
    await asyncio.sleep(0.3)
    assert spawn_counter == [], "engine spawned after cancel"
    assert not list(harness.work().glob(f"{sub.request_id}*"))


async def test_cancel_while_spawning_kills_immediately(harness, spawn_counter):
    p = harness.provider(cls=SlowSpawn)
    harness.mode = "slow"
    sub = await p.submit(plane())
    await asyncio.sleep(0.2)  # inside SlowSpawn._spawn, before the real spawn
    t0 = time.monotonic()
    await p.cancel(sub.request_id)
    assert time.monotonic() - t0 < 15
    st = await p.status(sub.request_id)
    assert st.state == "canceled"
    assert spawn_counter == [], "MOCK_ENGINE spawned after cancel was requested"
    assert not any(child.is_running() for child in psutil.Process().children(recursive=True)
                   if "mock_engine.py" in " ".join(child.cmdline()))


async def test_cancel_during_run_kills_process_tree(harness):
    harness.mode = "slow-with-child"
    p = harness.provider()
    sub = await p.submit(plane())
    for _ in range(50):
        await asyncio.sleep(0.1)
        job = p.job_record(sub.request_id)
        if job.get("pid") and psutil.Process(job["pid"]).children():
            break
    pid = job["pid"]
    grandchildren = psutil.Process(pid).children(recursive=True)
    assert grandchildren
    await asyncio.wait_for(p.cancel(sub.request_id), 30)
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    for g in grandchildren:
        assert not g.is_running() or g.status() == psutil.STATUS_ZOMBIE
    assert (await p.status(sub.request_id)).state == "canceled"
    assert not list(harness.work().glob(f"{sub.request_id}*"))


async def test_cancel_during_fetch_transcode_leaves_no_partial(harness):
    harness.mode = "ok"
    p = harness.provider("sdcpp:wan2.2-ti2v-5b")
    sub = await p.submit(plane("sdcpp:wan2.2-ti2v-5b"))
    st = await wait_done(p, sub.request_id)
    assert st.state == "completed"
    dest = harness.storage / "clip.mp4"
    fetch = asyncio.create_task(p.fetch(st.outputs[0], dest))
    await asyncio.sleep(0.05)
    fetch.cancel()
    with pytest.raises(asyncio.CancelledError):
        await fetch
    await p.cancel(sub.request_id)
    assert not dest.exists()
    assert not list(harness.storage.glob("*.part")) and not list(harness.work().glob("*.part"))
    assert not list(harness.work().glob(f"{sub.request_id}*"))


async def test_cancel_after_fetch_is_idempotent_and_repeated_fetch_impossible(harness):
    p = harness.provider()
    sub = await p.submit(plane())
    st = await wait_done(p, sub.request_id)
    dest = harness.storage / "one.png"
    await p.fetch(st.outputs[0], dest)
    assert dest.is_file()
    with pytest.raises(ValueError, match="fetched"):
        await p.fetch(st.outputs[0], harness.storage / "two.png")
    await p.cancel(sub.request_id)
    await p.cancel(sub.request_id)
    assert dest.is_file(), "cancel after import must not delete the imported file"


async def test_cancel_unknown_rid_is_noop(harness):
    p = harness.provider()
    await p.cancel("nope")


async def test_spawn_failure_is_provider_down_not_exception(harness):
    harness.mode = "nobinary"
    p = harness.provider()
    sub = await p.submit(plane())
    st = await wait_done(p, sub.request_id)
    assert st.state == "failed" and st.reason == "provider_down"
    assert "FileNotFoundError" in p.failure_detail(sub.request_id)
    assert not list(harness.work().glob(f"{sub.request_id}*.png"))


@pytest.mark.timeout(90)
async def test_very_long_stdout_line_does_not_hang_or_crash(harness):
    """Reproduced on the pre-fix provider: readline() on a 6 MiB line hung the worker and left the
    MOCK_ENGINE child alive past the pytest timeout. Bounded here by the chunked pump + hard timeouts."""
    harness.mode = "longline"
    p = harness.provider()
    sub = await p.submit(plane())
    try:
        st = await asyncio.wait_for(wait_done(p, sub.request_id), 60)
    finally:
        await asyncio.wait_for(p.cancel(sub.request_id), 30)  # never leaves a live child behind
        pid = p.job_record(sub.request_id).get("pid")
        assert pid is None or not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    assert st.state == "completed", p.failure_detail(sub.request_id)
    job = p.job_record(sub.request_id)
    assert all(len(line) <= sdcpp.MAX_LOG_LINE for line in job["log"])
    assert len(job["log"]) <= sdcpp.MAX_LOG_LINES


async def test_nonzero_exit_failed_and_zero_exit_corrupt_container_failed(harness):
    harness.mode = "fail"
    p = harness.provider()
    sub = await p.submit(plane())
    assert (await wait_done(p, sub.request_id)).reason == "malformed"
    harness.mode = "corrupt"
    for model_id in ("sdcpp:z-image-turbo", "sdcpp:wan2.2-ti2v-5b"):
        p = harness.provider(model_id)
        sub = await p.submit(plane(model_id))
        st = await wait_done(p, sub.request_id)
        assert st.state == "failed" and st.reason == "malformed", model_id
        assert p.job_record(sub.request_id)["returncode"] == 0
    harness.mode = "empty"
    p = harness.provider()
    sub = await p.submit(plane())
    assert (await wait_done(p, sub.request_id)).reason == "malformed"


async def test_hard_timeout_kills_engine(harness, monkeypatch):
    harness.mode = "slow"
    p = harness.provider()
    monkeypatch.setattr(p, "hard_timeout_s", 2)
    sub = await p.submit(plane())
    st = await asyncio.wait_for(wait_done(p, sub.request_id, 30), 40)
    assert st.state == "failed" and st.reason == "timeout"
    pid = p.job_record(sub.request_id)["pid"]
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


# --------------------------------------------------------------------------- MEDIA-RESTART

def _spawn_sleeper():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc, psutil.Process(proc.pid)


def _dead_owner():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    ct = psutil.Process(proc.pid).create_time()
    proc.wait()
    return proc.pid, ct


def _sidecar(work, rid, **fields):
    work.mkdir(parents=True, exist_ok=True)
    base = {"version": 1, "rid": rid, "pid": None, "create_time": None, "argv": [], "exe": None,
            "raw": str(work / f"{rid}.png"), "init": None, "started": time.time() - 3600,
            "studio_job_id": None, "owner_pid": None, "owner_create_time": None}
    (work / f"{rid}.job.json").write_text(json.dumps({**base, **fields}), encoding="utf-8")
    Path(base["raw"]).write_bytes(b"stale")


def test_reconcile_does_not_kill_reused_pid(tmp_path):
    work = tmp_path / "engine-work"
    proc, ps = _spawn_sleeper()
    try:
        dead_pid, dead_ct = _dead_owner()
        _sidecar(work, "reused", pid=proc.pid, create_time=ps.create_time() - 1000, argv=["sd-cli.exe", "-M", "vid_gen"],
                 exe="sd-cli.exe", owner_pid=dead_pid, owner_create_time=dead_ct)
        report = sdcpp.reconcile_orphans(work)
        assert report["pid_reused"] == ["reused"] and report["killed"] == []
        assert ps.is_running() and proc.poll() is None
        assert not (work / "reused.job.json").exists() and not (work / "reused.png").exists()
    finally:
        proc.kill(); proc.wait()


def test_reconcile_kills_verified_orphan(tmp_path):
    work = tmp_path / "engine-work"
    proc, ps = _spawn_sleeper()
    try:
        dead_pid, dead_ct = _dead_owner()
        _sidecar(work, "orphan", pid=proc.pid, create_time=ps.create_time(), argv=ps.cmdline(),
                 exe=ps.exe(), owner_pid=dead_pid, owner_create_time=dead_ct)
        report = sdcpp.reconcile_orphans(work)
        assert report["killed"] == ["orphan"], report
        proc.wait(timeout=10)
        assert not (work / "orphan.job.json").exists() and not (work / "orphan.png").exists()
    finally:
        if proc.poll() is None:
            proc.kill(); proc.wait()


def test_reconcile_skips_live_owner_and_reports_only(tmp_path):
    work = tmp_path / "engine-work"
    proc, ps = _spawn_sleeper()
    try:
        me = psutil.Process()
        _sidecar(work, "live", pid=proc.pid, create_time=ps.create_time(), argv=ps.cmdline(), exe=ps.exe(),
                 owner_pid=me.pid, owner_create_time=me.create_time())
        report = sdcpp.reconcile_orphans(work)
        assert report["skipped_live_owner"] == ["live"] and report["killed"] == []
        assert proc.poll() is None and (work / "live.job.json").exists()
        # report-only mode never kills, even a verified orphan
        dead_pid, dead_ct = _dead_owner()
        _sidecar(work, "orph", pid=proc.pid, create_time=ps.create_time(), argv=ps.cmdline(), exe=ps.exe(),
                 owner_pid=dead_pid, owner_create_time=dead_ct)
        dry = sdcpp.reconcile_orphans(work, kill=False)
        assert dry["would_kill"] == ["orph"] and proc.poll() is None and (work / "orph.job.json").exists()
    finally:
        proc.kill(); proc.wait()


def test_reconcile_tolerates_garbage_sidecar_and_missing_dir(tmp_path):
    work = tmp_path / "engine-work"
    assert sdcpp.reconcile_orphans(work)["scanned"] == 0
    work.mkdir()
    (work / "junk.job.json").write_text("{not json", encoding="utf-8")
    (work / "stale.png").write_bytes(b"x")
    report = sdcpp.reconcile_orphans(work)
    assert report["errors"] and not (work / "junk.job.json").exists()


async def test_sidecar_written_with_identity_and_removed_after_import(harness):
    harness.mode = "slow"
    p = harness.provider()
    sub = await p.submit(plane())
    for _ in range(50):
        await asyncio.sleep(0.1)
        if p.job_record(sub.request_id).get("pid"):
            break
    side = json.loads((harness.work() / f"{sub.request_id}.job.json").read_text(encoding="utf-8"))
    ps = psutil.Process(side["pid"])
    assert abs(side["create_time"] - ps.create_time()) < 1.0
    assert side["argv"][1:3] == [str(harness.script), "slow"] and side["owner_pid"] == os.getpid()
    assert side["raw"].endswith(".png") and side["started"] > 0
    await p.cancel(sub.request_id)
    assert not (harness.work() / f"{sub.request_id}.job.json").exists()
    harness.mode = "ok"
    p = harness.provider()
    sub = await p.submit(plane())
    st = await wait_done(p, sub.request_id)
    assert (harness.work() / f"{sub.request_id}.job.json").exists()
    await p.fetch(st.outputs[0], harness.storage / "a.png")
    assert not (harness.work() / f"{sub.request_id}.job.json").exists()


async def test_repeated_submit_of_same_rid_impossible(harness, monkeypatch):
    p = harness.provider()
    monkeypatch.setattr(sdcpp, "_new_rid", lambda: "fixedrid00000001")
    harness.mode = "slow"
    sub = await p.submit(plane())
    with pytest.raises(ValueError, match="rid"):
        await p.submit(plane())
    q = harness.provider()  # a second provider instance sharing the work dir: the sidecar is the lock
    with pytest.raises(ValueError, match="rid"):
        await q.submit(plane())
    await p.cancel(sub.request_id)


async def test_two_workers_do_not_reconcile_each_other(harness):
    harness.mode = "slow"
    a = harness.provider()
    sub_a = await a.submit(plane())
    for _ in range(50):
        await asyncio.sleep(0.1)
        if a.job_record(sub_a.request_id).get("pid"):
            break
    pid_a = a.job_record(sub_a.request_id)["pid"]
    harness.mode = "ok"
    b = harness.provider()  # construction reconciles the shared work dir
    assert b.reconcile_report["killed"] == []
    assert psutil.pid_exists(pid_a) and psutil.Process(pid_a).status() != psutil.STATUS_ZOMBIE
    sub_b = await b.submit(plane())
    assert (await wait_done(b, sub_b.request_id)).state == "completed"
    assert (await a.status(sub_a.request_id)).state == "running"
    await a.cancel(sub_a.request_id)


async def test_runtime_setup_calls_reconcile(env, monkeypatch):
    from bcc.studio import runtime
    seen = []
    monkeypatch.setattr(sdcpp, "reconcile_orphans", lambda work, **kw: seen.append(Path(work)) or {"killed": []})
    await runtime.setup(env.svc)
    assert seen and seen[0] == Path(env.settings.data_dir) / "studio" / "engine-work"

    def boom(work, **kw):
        raise RuntimeError("psutil exploded")

    monkeypatch.setattr(sdcpp, "reconcile_orphans", boom)
    await runtime.setup(env.svc)  # guarded: setup survives


# --------------------------------------------------------------------------- MEDIA-INPUT/OUTPUT

async def test_start_role_real_png_accepted(harness):
    p = harness.provider("sdcpp:wan2.2-ti2v-5b")
    media = [{"role": "start", "run_id": "r", "sha256": "x", "data_uri": data_uri(png_bytes())}]
    sub = await p.submit(plane("sdcpp:wan2.2-ti2v-5b", media=media))
    job = p.job_record(sub.request_id)
    assert job["init"] is not None and Path(job["init"]).is_file()
    assert "-i" in job["argv"]
    st = await wait_done(p, sub.request_id)
    assert st.state == "completed"
    await p.cancel(sub.request_id)


@pytest.mark.parametrize("case", ["role", "junk", "png_header_junk", "oversize", "svg", "bad_uri", "two"])
async def test_bad_start_media_rejected(harness, case):
    p = harness.provider("sdcpp:wan2.2-ti2v-5b")
    item = {"role": "start", "run_id": "r", "sha256": "x", "data_uri": data_uri(png_bytes())}
    if case == "role":
        item["role"] = "reference"
    elif case == "junk":
        item["data_uri"] = data_uri(b"definitely not an image")
    elif case == "png_header_junk":
        item["data_uri"] = data_uri(b"\x89PNG\r\n\x1a\n" + os.urandom(512))
    elif case == "oversize":
        item["data_uri"] = "data:image/png;base64," + "A" * (4 * (sdcpp.MAX_INPUT_BYTES // 3) + 64)
    elif case == "svg":
        item["data_uri"] = data_uri(b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml")
    elif case == "bad_uri":
        item["data_uri"] = "http://evil/x.png"
    media = [item, item] if case == "two" else [item]
    with pytest.raises(ValueError):
        await p.submit(plane("sdcpp:wan2.2-ti2v-5b", media=media))
    assert not list(harness.work().glob("*-start*")), "rejected input left on disk"
    assert not list(harness.work().glob("*.job.json"))


async def test_image_model_rejects_media(harness):
    p = harness.provider()
    media = [{"role": "start", "run_id": "r", "sha256": "x", "data_uri": data_uri(png_bytes())}]
    with pytest.raises(ValueError):
        await p.submit(plane(media=media))


async def test_disk_space_check_before_generation(harness, monkeypatch, spawn_counter):
    p = harness.provider()
    monkeypatch.setattr(p, "min_free_bytes", 1 << 60)
    with pytest.raises(ProviderFailure) as exc:
        await p.submit(plane())
    assert exc.value.status.reason == "provider_down" and "disk" in str(exc.value)
    assert spawn_counter == []
    monkeypatch.setattr(p, "min_free_bytes", 0)
    sub = await p.submit(plane())  # negative control
    assert (await wait_done(p, sub.request_id)).state == "completed"


async def test_fetch_dest_must_be_under_root_and_output_is_atomic(harness, tmp_path):
    p = harness.provider()
    sub = await p.submit(plane())
    st = await wait_done(p, sub.request_id)
    with pytest.raises(PermissionError):
        await p.fetch(st.outputs[0], tmp_path / "escape.png")
    replaced = []
    real_replace = os.replace

    def spy(src, dst):
        replaced.append((Path(src).name, Path(dst).name)); real_replace(src, dst)

    import bcc.studio.providers.sdcpp as mod
    mod_os_replace = mod.os.replace
    mod.os.replace = spy
    try:
        dest = harness.storage / "final.png"
        fetched = await p.fetch(st.outputs[0], dest)
    finally:
        mod.os.replace = mod_os_replace
    assert fetched.path == dest.resolve() and dest.is_file()
    assert any(dst == "final.png" and src != "final.png" for src, dst in replaced), replaced
    assert not list(harness.storage.glob("*.part"))
    assert fetched.sha256 == hashlib.sha256(dest.read_bytes()).hexdigest()


class BrokenTranscode(sdcpp.SdCppProvider):
    async def _transcode(self, raw, tmp, job):
        tmp.write_bytes(b"half written")
        raise ValueError("MOCK_ENGINE ffmpeg encoder missing")


async def test_transcode_failure_leaves_no_partial_output(harness):
    p = harness.provider("sdcpp:wan2.2-ti2v-5b", cls=BrokenTranscode)
    sub = await p.submit(plane("sdcpp:wan2.2-ti2v-5b"))
    st = await wait_done(p, sub.request_id)
    dest = harness.storage / "clip.mp4"
    with pytest.raises(ProviderFailure) as exc:
        await p.fetch(st.outputs[0], dest)
    assert exc.value.status.reason == "provider_down"
    assert not dest.exists() and not list(harness.storage.glob("*.part"))


async def test_missing_ffmpeg_is_named_failure(harness, monkeypatch):
    p = harness.provider("sdcpp:wan2.2-ti2v-5b")
    sub = await p.submit(plane("sdcpp:wan2.2-ti2v-5b"))
    st = await wait_done(p, sub.request_id)
    assert st.state == "completed"
    from bcc.video_studio import media
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    with pytest.raises(ProviderFailure) as exc:
        await p.fetch(st.outputs[0], harness.storage / "clip.mp4")
    assert exc.value.status.reason == "provider_down" and "ffmpeg" in str(exc.value)
    assert not list(harness.storage.glob("*.part"))


async def test_provenance_separates_generation_transcode_import(harness):
    p = harness.provider("sdcpp:wan2.2-ti2v-5b")
    sub = await p.submit(plane("sdcpp:wan2.2-ti2v-5b", frames=49, fps=16))
    st = await wait_done(p, sub.request_id)
    dest = harness.storage / "clip.mp4"
    fetched = await p.fetch(st.outputs[0], dest)
    t = p.traces[sub.request_id]
    gen, tr, imp = t["generation"], t["transcode"], t["import"]
    assert len(gen["raw_output"]["sha256"]) == 64 and gen["raw_output"]["container"] == "matroska"
    assert tr["tool"] == "ffmpeg" and tr["input_sha256"] == gen["raw_output"]["sha256"]
    # argv[0] is the transcoder, quoted by name and never by absolute path. The executable
    # suffix is the host's business ("ffmpeg" on POSIX, "ffmpeg.exe" on Windows); the tool
    # identity and its exact case are not. `.EXE` here would mean the name came from PATHEXT
    # rather than from the file, and the same binary would be recorded differently per host.
    assert not os.path.isabs(tr["argv"][0]) and Path(tr["argv"][0]).stem == "ffmpeg", tr["argv"][0]
    assert Path(tr["argv"][0]).suffix in ("", ".exe"), tr["argv"][0]
    assert "libx264" in tr["argv"] and len(tr["output_sha256"]) == 64
    assert tr["output_sha256"] != gen["raw_output"]["sha256"]
    assert imp["sha256"] == fetched.sha256 == tr["output_sha256"] and imp["atomic"] is True
    assert gen["duration_s_declared"] == 49 / 16 and gen["frames"] == 49 and gen["fps"] == 16
    assert gen["duration_s_observed"] is not None  # ffprobe of the raw engine output
    assert t["backend"] == {"declared": "vulkan", "observed": None, "evidence": "none", "status": "UNVERIFIED"}


async def test_backend_observed_only_from_engine_log(harness):
    harness.mode = "vulkan-log"
    p = harness.provider()
    sub = await p.submit(plane())
    st = await wait_done(p, sub.request_id)
    await p.fetch(st.outputs[0], harness.storage / "x.png")
    b = p.traces[sub.request_id]["backend"]
    assert b["status"] == "OBSERVED" and b["evidence"] == "engine_log" and "ggml_vulkan" in b["observed"]
    assert p.traces[sub.request_id]["transcode"]["tool"] == "copy"


@pytest.mark.parametrize("lines,expect", [
    (["ggml_vulkan: Found 1 Vulkan devices:", "ggml_vulkan: 0 = AMD Radeon 8060S Graphics"], "OBSERVED"),
    (["[INFO ] stable-diffusion.cpp:123 - loading model"], "UNVERIFIED"),
    ([], "UNVERIFIED"),
    (["warning: vulkan backend not compiled in, using CPU"], "UNVERIFIED"),
])
def test_observe_backend_pure(lines, expect):
    out = sdcpp.observe_backend(lines)
    assert out["status"] == expect and out["declared"] == "vulkan"
    assert (out["observed"] is None) == (expect == "UNVERIFIED")


# --------------------------------------------------------------------------- MEDIA-PRESET

def test_catalog_default_video_duration_at_least_3s_and_argv_matches():
    model = _model("sdcpp:wan2.2-ti2v-5b")
    settings = catalog.validate_settings(model, {"seed": 1})
    assert settings["frames"] / settings["fps"] >= 3.0, "catalog default gives a <3 s clip"
    assert 17 / 16 < 3.0  # the owner handoff preset (17 frames @16fps) is ~1 s, not a >=3 s clip
    cfg = {"bin": Path("sd-cli.exe")}
    files = {r: Path(f"/m/{r}.gguf") for r in ROLES}
    argv = sdcpp._argv(cfg, model["id"], GenerationPlane(model["id"], "p", settings), settings, files,
                       Path("/w/out.webm"), None)
    assert argv[argv.index("--video-frames") + 1] == str(settings["frames"])
    assert argv[argv.index("--fps") + 1] == str(settings["fps"])
    assert "--vae-tiling" not in argv and "--diffusion-fa" not in argv
    assert sdcpp.declared_duration_s(settings) == settings["frames"] / settings["fps"]


# --------------------------------------------------------------------------- tools/media_ab_preset.py

@pytest.fixture
def ab_tool():
    import importlib.util
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("media_ab_preset", root / "tools" / "media_ab_preset.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ab_plan_changes_exactly_one_variable_per_pair(ab_tool, harness, tmp_path):
    plan = ab_tool.build_plan(seed=7, prompt="p", out_dir=tmp_path / "out", cfg=harness.cfg())
    by_id = {v["id"]: v for v in plan["variants"]}
    base = by_id["baseline"]
    assert base["extra_flags"] == [] and base["catalog_valid"] is True and base["settings"]["seed"] == 7
    assert len(plan["pairs"]) == 4 and all(p["a"] == "baseline" for p in plan["pairs"])
    for pair in plan["pairs"]:
        v = by_id[pair["b"]]
        assert v["settings"]["seed"] == 7
        changed = {k for k in v["settings"] if v["settings"][k] != base["settings"][k]}
        flag_changed = set(v["extra_flags"]) ^ set(base["extra_flags"])
        assert (changed, flag_changed) in [({"width", "height"}, set()), ({"steps"}, set()),
                                           (set(), {"--vae-tiling"}), (set(), {"--diffusion-fa"})], (changed, flag_changed)
        assert "-s" in v["argv"] and v["argv"][v["argv"].index("-s") + 1] == "7"
        assert v["expected_verdict"].startswith("UNKNOWN")
    # both "noise" presets from the owner handoff are outside the Studio catalog (width>=640, steps>=16):
    # Studio itself cannot submit them; the plan says so instead of hiding it
    assert by_id["only-resolution"]["catalog_valid"] is False
    assert by_id["only-steps"]["catalog_valid"] is False
    assert by_id["only-vae_tiling"]["catalog_valid"] is True and by_id["only-diffusion_fa"]["catalog_valid"] is True
    assert "--vae-tiling" in by_id["only-vae_tiling"]["argv"] and "--vae-tiling" not in base["argv"]
    assert "--diffusion-fa" in by_id["only-diffusion_fa"]["argv"] and "--diffusion-fa" not in base["argv"]
    assert by_id["only-steps"]["argv"][by_id["only-steps"]["argv"].index("--steps") + 1] == "10"
    assert all(v["duration_s_declared"] == 17 / 16 for v in plan["variants"])
    assert "not a declared working preset" in plan["baseline_note"]


def test_ab_run_refuses_without_engine_and_records_only_measurements(ab_tool, harness, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(sdcpp.BIN_ENV); monkeypatch.delenv(sdcpp.MODELS_ENV)
    assert ab_tool.main(["plan", "--out", str(tmp_path / "plan.json"), "--run"]) == 2
    assert (tmp_path / "plan.json").is_file() and not (tmp_path / "plan-results.json").exists()
    plan = json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))
    assert plan["paths_source"].startswith("placeholders")
    calls = []

    class Done:
        returncode = 0
        stdout = b"ggml_vulkan: Found 1 Vulkan devices:\nMOCK_ENGINE\n"

    def fake_runner(argv, **kw):
        calls.append(argv)
        Path(argv[argv.index("-o") + 1]).write_bytes(b"\x1aE\xdf\xa3 MOCK_ENGINE not a real clip")
        return Done()

    results = ab_tool.run_plan(plan, timeout_s=5, log=lambda *_: None, runner=fake_runner)
    assert len(calls) == 5 and all(r["returncode"] == 0 and len(r["output_sha256"]) == 64 for r in results["results"])
    assert all(r["quality"].startswith("NOT_ASSESSED") for r in results["results"])
    assert all(r["backend"]["status"] == "OBSERVED" for r in results["results"])
    assert "no quality claim" in results["verdict"]


def test_ffmpeg_is_named_by_the_filesystem_not_by_pathext():
    """Provenance quotes argv, so the tool's name must be a fact about the file.

    shutil.which() composes the extension from PATHEXT, a registry value spelled ".EXE" on a
    stock Windows install, while the bundled binary on disk is "ffmpeg.exe". Without this the
    identical binary is recorded under two different names depending on the host's registry,
    and every provenance comparison across machines turns into noise.
    """
    from bcc.video_studio import media

    found = Path(media.binary("ffmpeg"))
    on_disk = [e.name for e in found.parent.iterdir() if e.name.casefold() == found.name.casefold()]
    assert on_disk, f"{found} is not a directory entry of {found.parent}"
    assert found.name == on_disk[0], f"named {found.name!r}, the filesystem says {on_disk[0]!r}"
