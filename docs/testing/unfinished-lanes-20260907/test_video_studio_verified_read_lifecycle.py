"""A8-01 / CC-VIDEO-READVERIFICATION-WINFILE.

FINDING (owner, on Windows): WinError 32 -- ERROR_SHARING_VIOLATION -- around
unlink/replace while a VerifiedRead descriptor is alive.

WHY IT HAPPENS. A verified descriptor is deliberately long-lived: it is detached
into the HTTP response and stays open for the whole body, so the pathname can no
longer decide which bytes leave. CPython's ``os.open`` on Windows goes through
``_wopen``/``_sopen_s`` with ``_SH_DENYNO``, which grants FILE_SHARE_READ and
FILE_SHARE_WRITE but NOT FILE_SHARE_DELETE. For as long as one download,
thumbnail or export read is in flight, ``os.replace()`` and ``unlink()`` of that
path therefore fail on Windows. POSIX never shows this: unlink always succeeds
and the open descriptor keeps the inode alive.

WHAT THIS MODULE CAN AND CANNOT PROVE ON LINUX
----------------------------------------------
PROVABLE HERE, and asserted below:
  * the descriptor window itself -- when a verified descriptor is open, on which
    path, and that it is closed on every exit path (no leak, no double close);
  * that a production mutation really does run while that descriptor is alive
    (``MediaLibrary.import_file``'s content-addressed repair), which is the exact
    shape that raises WinError 32 on Windows;
  * that the replace/delete lifecycle still serves the VERIFIED bytes and never
    stale or substituted ones (no descriptor TOCTOU regression);
  * that the Windows opener is wired in, is selected only on win32, requests
    FILE_SHARE_DELETE, and does not skip a single verification step.

NOT PROVABLE HERE: that Windows actually stops raising WinError 32. That needs a
Windows runner; the one assertion that requires it is marked skipif and carries a
reason, so tools/skips_registry.py records it rather than it hiding a FAIL.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bcc.video_studio import read_verification
from bcc.video_studio.descriptor_stream import stream_verified
from bcc.video_studio.media import MediaLibrary
from bcc.video_studio.read_verification import open_descriptor, open_verified
from bcc.video_studio.service import VideoService

needs_ffmpeg = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="real FFmpeg binaries required to exercise MediaLibrary.import_file")
needs_proc_fd = pytest.mark.skipif(
    not os.path.isdir("/proc/self/fd"),
    reason="descriptor-per-path introspection needs /proc/self/fd (Linux)")


# --------------------------------------------------------------------------
# fixtures: real files on disk, no FFmpeg, no DB
# --------------------------------------------------------------------------

def wav(payload: bytes) -> bytes:
    """A real, magic-recognised RIFF/WAVE container; file_kind() accepts it."""
    return b"RIFF" + (len(payload) + 36).to_bytes(4, "little") + b"WAVE" + payload


def owned_media(root: Path, payload: bytes = b"verified-original-bytes" * 16):
    body = wav(payload)
    digest = hashlib.sha256(body).hexdigest()
    directory = Path(root) / "media"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (digest + ".wav")
    path.write_bytes(body)
    return path, body, {"id": "m_" + digest[:24], "name": "owned.wav", "folder": "", "tags": [],
                        "relative_path": "media/" + digest + ".wav", "sha256": digest,
                        "bytes": len(body), "duration_ticks": 1_000_000, "width": 0, "height": 0,
                        "fps": {"num": 0, "den": 1}, "has_video": False, "has_audio": True,
                        "sample_rate": 48000, "channels": 2, "metadata": {}}


def studio(root: Path, item: dict) -> SimpleNamespace:
    service = SimpleNamespace(root=Path(root).resolve(), media=MediaLibrary(root),
                              store=SimpleNamespace(get=AsyncMock(return_value={"media": {item["id"]: item}})))
    service.media_file = lambda pid, mid: VideoService.media_file(service, pid, mid)
    return service


def derivative(root: Path, item: dict, kind: str = "thumbnail",
               payload: bytes = b"\xff\xd8\xff" + b"derived-thumbnail-bytes" * 8):
    """A prepared cache artifact plus the manifest digest prepare() would record."""
    name = {"thumbnail": "thumb.jpg", "proxy": "proxy.mp4", "waveform": "wave.png"}[kind]
    cache = Path(root) / "cache" / item["sha256"] / "v1"
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / name
    target.write_bytes(payload)
    MediaLibrary(root).record_derived(item["sha256"], {kind: {
        "name": name, "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}})
    return target, payload


async def drive(response, *, headers=None):
    """Run one ASGI response to completion and collect status/headers/body."""
    scope = {"type": "http", "method": "GET", "path": "/", "query_string": b"",
             "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()]}
    sent = []

    async def receive():
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    await response(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], {k.decode().lower(): v.decode() for k, v in start["headers"]}, body


def descriptors_on(path) -> list[str]:
    """The fds THIS process holds on `path`.

    On Windows a non-empty result at the moment of unlink/replace is exactly the
    condition that raises WinError 32, so this is the Linux-observable proxy for
    the finding: it measures the hazard window, not the Windows error itself.
    """
    try:
        real = os.path.realpath(path)
    except OSError:  # pragma: no cover - defensive
        real = str(path)
    held = []
    for name in os.listdir("/proc/self/fd"):
        try:
            target = os.readlink("/proc/self/fd/" + name)
        except OSError:
            continue
        if target in (real, str(path)):
            held.append(name)
    return held


def open_count() -> int:
    return len(os.listdir("/proc/self/fd")) if os.path.isdir("/proc/self/fd") else -1


# --------------------------------------------------------------------------
# 1. The descriptor window: where it opens, how long it lives, that it closes
# --------------------------------------------------------------------------

@needs_proc_fd
async def test_media_descriptor_is_alive_for_the_whole_body_and_closed_afterwards(tmp_path):
    path, original, item = owned_media(tmp_path)
    baseline = open_count()

    handle, media = await VideoService.media_file(studio(tmp_path, item), "p", item["id"])
    assert len(descriptors_on(path)) == 1, "verification must hold exactly one descriptor"

    response = stream_verified(handle, filename=media["name"])
    assert len(descriptors_on(path)) == 1, "the descriptor must survive into the response"

    status, _, body = await drive(response)
    assert (status, body) == (200, original)
    assert descriptors_on(path) == [], "the verified descriptor outlived the body"
    assert open_count() == baseline


@needs_proc_fd
async def test_abandoned_verified_handle_is_closed_by_close_not_leaked(tmp_path):
    path, _, item = owned_media(tmp_path)
    baseline = open_count()
    handle, _ = await VideoService.media_file(studio(tmp_path, item), "p", item["id"])
    assert len(descriptors_on(path)) == 1
    handle.close()
    assert descriptors_on(path) == []
    handle.close()  # idempotent: must not close a recycled descriptor twice
    assert open_count() == baseline


@needs_proc_fd
async def test_prepared_derivative_never_holds_the_source_descriptor_at_the_same_time(tmp_path):
    """prepared_file authorises with the SOURCE and serves a DERIVATIVE.

    Holding both at once would double the WinError 32 window for no reason, and
    would keep a descriptor on owned media alive for the whole cache download.
    """
    source, _, item = owned_media(tmp_path)
    target, payload = derivative(tmp_path, item)
    baseline = open_count()

    handle = await VideoService.prepared_file(studio(tmp_path, item), "p", item["id"], "thumbnail")
    assert descriptors_on(source) == [], "the source descriptor spans the derivative download"
    assert len(descriptors_on(target)) == 1

    status, _, body = await drive(stream_verified(handle, media_type="image/jpeg"))
    assert (status, body) == (200, payload)
    assert descriptors_on(target) == []
    assert open_count() == baseline


@needs_proc_fd
async def test_refused_verification_leaks_no_descriptor(tmp_path):
    """Every refusal path must close first; a leaked fd is a permanent WinError 32."""
    path, _, item = owned_media(tmp_path)
    path.write_bytes(wav(b"tampered-in-place-bytes" * 16))
    baseline = open_count()
    with pytest.raises(ValueError, match="hash mismatch"):
        await VideoService.media_file(studio(tmp_path, item), "p", item["id"])
    assert descriptors_on(path) == []
    assert open_count() == baseline


# --------------------------------------------------------------------------
# 2. replace / delete lifecycle -- no stale or substituted bytes
# --------------------------------------------------------------------------

@pytest.mark.parametrize("mutation", ["unlink_recreate", "replace", "delete_only"])
async def test_replace_delete_lifecycle_still_serves_the_verified_bytes(tmp_path, mutation):
    path, original, item = owned_media(tmp_path)
    handle, media = await VideoService.media_file(studio(tmp_path, item), "p", item["id"])
    response = stream_verified(handle, filename=media["name"])

    other = wav(b"post-verification-substituted-bytes" * 9)
    assert other != original
    if mutation == "unlink_recreate":
        path.unlink()
        path.write_bytes(other)
    elif mutation == "replace":
        staged = tmp_path / "staged.wav"
        staged.write_bytes(other)
        os.replace(staged, path)
    else:
        path.unlink()

    status, headers, body = await drive(response)
    assert body == original, "stale or substituted bytes were served"
    assert not (status == 200 and body == other)
    assert (status, headers["content-length"]) == (200, str(len(original)))


async def test_export_download_survives_republish_of_the_same_pathname(tmp_path):
    """Export/cache cleanup shape: the artifact name is re-pointed mid-download."""
    directory = tmp_path / "exports" / "job1" / "1"
    directory.mkdir(parents=True)
    artifact = directory / "out.mp4"
    original = b"published-export-artifact" * 40
    artifact.write_bytes(original)
    digest = hashlib.sha256(original).hexdigest()

    handle = await open_verified(artifact, digest, "output changed after independent verification")
    response = stream_verified(handle, media_type="video/mp4")

    republished = b"republished-export-artifact" * 40
    staged = directory / "out.partial"
    staged.write_bytes(republished)
    os.replace(staged, artifact)          # WinError 32 site on Windows
    shutil.rmtree(directory, ignore_errors=True)   # cache/export cleanup

    status, _, body = await drive(response)
    assert (status, body) == (200, original)


@needs_proc_fd
async def test_cache_cleanup_after_derivative_download_leaves_no_descriptor(tmp_path):
    _, _, item = owned_media(tmp_path)
    target, payload = derivative(tmp_path, item)
    cache = target.parent
    baseline = open_count()

    handle = await VideoService.prepared_file(studio(tmp_path, item), "p", item["id"], "thumbnail")
    status, _, body = await drive(stream_verified(handle, media_type="image/jpeg"))
    assert (status, body) == (200, payload)

    shutil.rmtree(cache)
    assert not cache.exists()
    assert open_count() == baseline


# --------------------------------------------------------------------------
# 3. The reproduction: a production mutation runs while the descriptor is alive
# --------------------------------------------------------------------------

@needs_proc_fd
@needs_ffmpeg
async def test_content_addressed_repair_runs_while_a_download_holds_the_descriptor(tmp_path):
    """media.py MediaLibrary.import_file: ``os.replace(temporary, destination)``.

    The repair branch overwrites owned media that a verified download may be
    streaming from. That replace is issued with a live descriptor on the
    destination -- proven here on Linux -- which is precisely ERROR_SHARING_
    VIOLATION on Windows unless the descriptor was opened with FILE_SHARE_DELETE.
    """
    from bcc.video_studio.media import binary, process
    source = tmp_path / "source.wav"
    await process([binary("ffmpeg"), "-v", "error", "-nostdin", "-y", "-f", "lavfi",
                   "-i", "sine=frequency=440:duration=0.2", "-ac", "2", "-ar", "48000", str(source)])
    library = MediaLibrary(tmp_path)
    item = await library.import_file(source, name="source.wav")
    destination = tmp_path / item["relative_path"]
    original = destination.read_bytes()

    handle, media = await VideoService.media_file(studio(tmp_path, item), "p", item["id"])
    response = stream_verified(handle, filename=media["name"])
    assert len(descriptors_on(destination)) == 1

    # Bit rot on the destination sends the re-import down the repair branch. It
    # is an IN-PLACE rewrite of the pinned inode, which is exactly how bit rot
    # presents: same name, same inode, one flipped byte.
    damaged = bytearray(original)
    damaged[-1] ^= 1
    damaged = bytes(damaged)
    destination.write_bytes(damaged)

    replaced = []
    real_replace = os.replace

    def watched(src, dst, *args, **kwargs):
        replaced.append((str(dst), descriptors_on(dst)))
        return real_replace(src, dst, *args, **kwargs)

    os.replace = watched
    try:
        repaired = await library.import_file(source, name="source.wav")
    finally:
        os.replace = real_replace

    assert repaired["sha256"] == item["sha256"]
    held = [fds for dst, fds in replaced if dst == str(destination) and fds]
    assert held, ("the repair no longer replaces owned media under a live verified "
                  "descriptor -- if that is intentional this test must be rewritten, "
                  "not deleted, since it is the WinError 32 reproduction")

    # The repair put a NEW inode behind the name. The in-flight response must
    # keep reading the inode it verified and pinned, never follow the pathname to
    # the replacement. (An in-place rewrite of a pinned inode is out of scope of
    # the descriptor boundary by design -- see read_verification's docstring --
    # so the pinned bytes here are the damaged ones, and that is the point: they
    # are the inode's, not the replacement's.)
    assert destination.read_bytes() == original, "the repair did not restore the owned bytes"
    status, _, body = await drive(response)
    assert status == 200
    assert body == damaged, "the response stopped reading the descriptor it verified"
    assert body != destination.read_bytes(), "the replacement inode leaked into a verified read"


# --------------------------------------------------------------------------
# 4. The Windows fix: FILE_SHARE_DELETE, wired in, and not weakening anything
# --------------------------------------------------------------------------

def test_windows_share_mode_requests_file_share_delete():
    """Without FILE_SHARE_DELETE the Windows handle blocks every replace/unlink."""
    assert read_verification.FILE_SHARE_DELETE == 0x00000004
    assert read_verification.WINDOWS_SHARE_MODE & read_verification.FILE_SHARE_DELETE
    assert read_verification.WINDOWS_SHARE_MODE == 0x00000001 | 0x00000002 | 0x00000004


@pytest.mark.parametrize("platform,expected", [("win32", "_windows_open"),
                                               ("linux", "_posix_open"),
                                               ("darwin", "_posix_open")])
def test_shared_delete_opener_is_selected_only_on_win32(monkeypatch, platform, expected):
    monkeypatch.setattr(sys, "platform", platform)
    module = importlib.reload(read_verification)
    try:
        assert module.SHARED_DELETE_OPEN is (platform == "win32")
        assert module.platform_open is getattr(module, expected)
    finally:
        monkeypatch.undo()
        importlib.reload(read_verification)


def test_every_verified_descriptor_comes_from_the_single_shared_opener(tmp_path, monkeypatch):
    """One chokepoint, or a forgotten os.open reintroduces the bug on Windows."""
    path, body, _ = owned_media(tmp_path)
    seen = []
    real = read_verification.platform_open
    monkeypatch.setattr(read_verification, "platform_open",
                        lambda p: seen.append(str(p)) or real(p))
    fd, _ = open_descriptor(path)
    os.close(fd)
    assert seen == [str(path)]


def test_open_descriptor_still_refuses_a_non_regular_file_and_closes_the_fd(tmp_path, monkeypatch):
    """Negative control for the new opener: the checks after it are unchanged."""
    directory = tmp_path / "adirectory"
    directory.mkdir()
    monkeypatch.setattr(read_verification, "platform_open",
                        lambda p: os.open(p, os.O_RDONLY))
    before = open_count()
    with pytest.raises(ValueError, match="regular file"):
        open_descriptor(directory)
    if before >= 0:
        assert open_count() == before, "a refused open leaked its descriptor"


async def test_verification_is_not_weakened_by_the_shared_opener(tmp_path):
    """The three refusals that the fix must not trade away, all still refusals."""
    path, body, item = owned_media(tmp_path)

    # a) owned media whose content no longer matches its content-addressed name
    path.write_bytes(wav(b"foreign-bytes-under-an-owned-name" * 7))
    with pytest.raises(ValueError, match="hash mismatch"):
        await VideoService.media_file(studio(tmp_path, item), "p", item["id"])
    path.write_bytes(body)

    # b) a derivative whose bytes differ from the digest prepare recorded
    target, _ = derivative(tmp_path, item)
    target.write_bytes(b"\xff\xd8\xff" + b"foreign-derivative-bytes" * 4)
    with pytest.raises(RuntimeError, match="digest recorded"):
        await VideoService.prepared_file(studio(tmp_path, item), "p", item["id"], "thumbnail")

    # c) an export artifact that does not match its recorded digest
    artifact = tmp_path / "exports" / "out.mp4"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"published" * 20)
    with pytest.raises(RuntimeError, match="output changed"):
        await open_verified(artifact, hashlib.sha256(b"something-else").hexdigest(),
                            "output changed after independent verification")


# --------------------------------------------------------------------------
# 5. The one assertion that genuinely needs a Windows runner
# --------------------------------------------------------------------------

@pytest.mark.skipif(os.name != "nt", reason=(
    "WinError 32 / ERROR_SHARING_VIOLATION only exists on Windows: POSIX unlink "
    "and rename always succeed under an open descriptor, so no Linux run can "
    "prove or disprove the FILE_SHARE_DELETE fix. Needs a Windows CI runner."))
async def test_live_verified_descriptor_does_not_block_replace_or_unlink(tmp_path):  # pragma: no cover - Windows only
    path, original, item = owned_media(tmp_path)
    handle, media = await VideoService.media_file(studio(tmp_path, item), "p", item["id"])
    response = stream_verified(handle, filename=media["name"])

    staged = tmp_path / "staged.wav"
    staged.write_bytes(wav(b"republished-owned-bytes" * 16))
    os.replace(staged, path)   # raised WinError 32 before FILE_SHARE_DELETE
    path.unlink()              # ditto

    status, _, body = await drive(response)
    assert (status, body) == (200, original)
