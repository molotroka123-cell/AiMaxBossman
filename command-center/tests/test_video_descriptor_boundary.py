"""Проверка ГРАНИЦЫ: отдаём байты дескриптора, а не то, что значит имя файла.

Every fixture here is built directly on disk -- content-addressed owned media and
cache derivatives -- so the whole module runs on a host with no FFmpeg at all.

The attacks are the ones a pathname-bound check cannot survive:

  A1  verification succeeds, then the NAME is re-pointed (unlink + rewrite, or a
      symlink swap) before the body is read. The served bytes must be the ones
      that were verified, or the request must fail; the attacker's bytes must
      never leave with a 200.
  A2  the same for the published export artifact.
  A3  a derivative (thumbnail/proxy/waveform) whose bytes do not match the digest
      recorded when prepare created it must be refused, not served.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa

from bcc.db import tasks as tasks_t, utcnow
from bcc.video_studio import media as media_module
from bcc.video_studio.descriptor_stream import descriptor_response, stream_verified
from bcc.video_studio.media import MediaLibrary
from bcc.video_studio.service import VideoService, jobs

BASE = "/api/video-studio"


def wav(payload: bytes) -> bytes:
    """A real, magic-recognised RIFF/WAVE container; file_kind() accepts it."""
    return b"RIFF" + (len(payload) + 36).to_bytes(4, "little") + b"WAVE" + payload


def owned_media(root: Path, payload: bytes = b"original-verified-bytes" * 8) -> tuple[Path, bytes, dict]:
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
    """Real VideoService methods over a stub store: no DB, no engine, no FFmpeg."""
    service = SimpleNamespace(root=Path(root).resolve(), media=MediaLibrary(root),
                              store=SimpleNamespace(get=AsyncMock(return_value={"media": {item["id"]: item}})))
    service.media_file = lambda pid, mid: VideoService.media_file(service, pid, mid)
    return service


async def drive(response, *, headers=None):
    """Run one ASGI response to completion and collect status/headers/body."""
    scope = {"type": "http", "method": "GET", "path": "/", "query_string": b"",
             "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()]}
    sent = []

    async def receive():
        # starlette watches this for a disconnect; it must actually suspend,
        # otherwise its listener starves the body task and nothing is sent.
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    await response(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], {k.decode().lower(): v.decode() for k, v in start["headers"]}, body


def descriptors() -> int:
    if os.path.isdir("/proc/self/fd"):
        return len(os.listdir("/proc/self/fd"))
    import psutil
    return psutil.Process().num_fds()


# --------------------------------------------------------------------------
# A1: the original media body
# --------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["rewrite", "symlink"])
async def test_verified_media_body_survives_pathname_replacement(tmp_path, attack):
    path, original, item = owned_media(tmp_path)
    handle, media = await VideoService.media_file(studio(tmp_path, item), "p", item["id"])
    response = stream_verified(handle, filename=media["name"])

    # Verification has passed. Re-point the NAME before a single body byte is read.
    attacker = wav(b"attacker-substituted-bytes" * 8)
    assert attacker != original
    elsewhere = tmp_path / "attacker.wav"
    elsewhere.write_bytes(attacker)
    path.unlink()
    if attack == "rewrite":
        path.write_bytes(attacker)
    else:
        path.symlink_to(elsewhere)
    assert path.read_bytes() == attacker, "the pathname really was re-pointed"

    status, headers, body = await drive(response)
    assert body != attacker, "the attacker's bytes were served as verified media"
    assert not (status == 200 and body == attacker)
    assert status == 200 and body == original
    assert headers["content-length"] == str(len(original))


@pytest.mark.asyncio
async def test_media_response_never_reopens_the_pathname(tmp_path, monkeypatch):
    """A pathname re-opened at send time is the whole bug; forbid it outright."""
    path, original, item = owned_media(tmp_path)
    handle, media = await VideoService.media_file(studio(tmp_path, item), "p", item["id"])
    response = stream_verified(handle, filename=media["name"])
    opened = []
    real_open = os.open
    monkeypatch.setattr(os, "open", lambda *a, **kw: opened.append(a[0]) or real_open(*a, **kw))
    status, _, body = await drive(response)
    assert status == 200 and body == original
    assert not [name for name in opened if str(name) == str(path)]


@pytest.mark.asyncio
async def test_media_verification_still_refuses_tampered_source(tmp_path):
    path, original, item = owned_media(tmp_path)
    path.write_bytes(wav(b"different-bytes-same-name" * 8))
    with pytest.raises(ValueError, match="hash mismatch"):
        await VideoService.media_file(studio(tmp_path, item), "p", item["id"])


# --------------------------------------------------------------------------
# A2: the published export artifact
# --------------------------------------------------------------------------

async def completed_export(env, payload: bytes) -> tuple[str, Path]:
    video = env.svc.video_studio
    jid = "job" + hashlib.sha256(payload).hexdigest()[:16]
    outdir = video.root / "exports" / jid / "1"
    outdir.mkdir(parents=True, exist_ok=True)
    artifact = outdir / "out.mp4"
    artifact.write_bytes(payload)
    async with env.svc.db.session() as s:
        res = await s.execute(sa.insert(tasks_t).values(title="Video export", prompt="p",
            kind="video_render", status="completed", max_retries=0,
            meta={"video_job_id": jid, "video_project_id": "p", "allowed_tools": []},
            created_at=utcnow(), updated_at=utcnow()))
        await s.execute(sa.insert(jobs).values(id=jid, operation_id=jid, digest="d",
            project_id="p", task_id=int(res.inserted_primary_key[0]),
            snapshot={"revision": 1}, options={},
            result={"path": str(artifact), "sha256": hashlib.sha256(payload).hexdigest()}))
        await s.commit()
    return jid, artifact


@pytest.mark.asyncio
async def test_export_download_survives_pathname_replacement_mid_stream(env, tmp_path, monkeypatch):
    original = b"published-export-artifact-bytes" * 40
    jid, artifact = await completed_export(env, original)
    attacker = b"attacker-export-bytes" * 60
    assert len(attacker) != len(original) and attacker != original

    swapped = []
    real = media_module.pread

    def swapping(fd, length, offset):
        # Fires while the response body is being read, after verification.
        if not swapped:
            swapped.append(True)
            artifact.unlink()
            artifact.write_bytes(attacker)
        return real(fd, length, offset)

    monkeypatch.setattr(media_module, "pread", swapping)
    download = await env.client.get(BASE + "/exports/" + jid + "/file")
    assert swapped, "the substitution never ran; the test proves nothing"
    assert artifact.read_bytes() == attacker
    assert download.content != attacker
    assert not (download.status_code == 200 and download.content == attacker)
    # Either outcome is honest: the held descriptor still yields the verified
    # bytes, or the unlink is noticed (it bumps the inode ctime) and the request
    # fails closed. What must never happen is a 200 carrying the new bytes.
    if download.status_code == 200:
        assert download.content == original
    else:
        assert download.status_code == 409, download.text


@pytest.mark.asyncio
async def test_export_download_serves_verified_bytes_after_pathname_replacement(env):
    """The precise form: swap the NAME after verification, before the body runs."""
    original = b"published-export-artifact-bytes" * 23
    jid, artifact = await completed_export(env, original)
    handle = await env.svc.video_studio.verified_output(jid)
    response = stream_verified(handle, media_type="video/mp4")

    attacker = b"attacker-export-bytes" * 31
    elsewhere = artifact.with_suffix(".attacker")
    elsewhere.write_bytes(attacker)
    artifact.unlink()
    artifact.symlink_to(elsewhere)
    assert artifact.read_bytes() == attacker

    status, headers, body = await drive(response)
    assert not (status == 200 and body == attacker)
    assert status == 200 and body == original
    assert headers["content-length"] == str(len(original))


@pytest.mark.asyncio
async def test_export_download_refuses_a_changed_artifact(env):
    original = b"published-export-artifact-bytes" * 11
    jid, artifact = await completed_export(env, original)
    artifact.write_bytes(b"x" * len(original))
    refused = await env.client.get(BASE + "/exports/" + jid + "/file")
    assert refused.status_code == 409, refused.text


# --------------------------------------------------------------------------
# A3: prepared derivatives
# --------------------------------------------------------------------------

async def prepared(tmp_path, monkeypatch) -> tuple[SimpleNamespace, dict, Path]:
    """Run the real prepare() with the FFmpeg calls stubbed out, so the manifest
    is written by production code rather than by the test."""
    _, _, item = owned_media(tmp_path)
    library = MediaLibrary(tmp_path)

    async def fake_process(argv, **kw):
        Path(argv[-1]).write_bytes(b"derived-waveform-pixels" * 4)
        return b"", b""

    monkeypatch.setattr(media_module, "binary", lambda name: "/bin/true")
    monkeypatch.setattr(media_module, "process", fake_process)
    artifacts = await library.prepare(item)
    assert set(artifacts) == {"waveform"}
    return studio(tmp_path, item), item, tmp_path / artifacts["waveform"]


@pytest.mark.asyncio
async def test_prepare_records_a_digest_for_every_derivative(tmp_path, monkeypatch):
    service, item, artifact = await prepared(tmp_path, monkeypatch)
    recorded = MediaLibrary(tmp_path).derived_manifest(item["sha256"])
    assert recorded["waveform"]["sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert recorded["waveform"]["bytes"] == artifact.stat().st_size


@pytest.mark.asyncio
async def test_prepared_derivative_is_served_only_when_its_bytes_match(tmp_path, monkeypatch):
    service, item, artifact = await prepared(tmp_path, monkeypatch)
    handle = await VideoService.prepared_file(service, "p", item["id"], "waveform")
    try:
        assert handle.sha256 == hashlib.sha256(artifact.read_bytes()).hexdigest()
    finally:
        handle.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["same-size", "longer", "truncated"])
async def test_tampered_derivative_is_refused_and_never_served(tmp_path, monkeypatch, tamper):
    service, item, artifact = await prepared(tmp_path, monkeypatch)
    honest = artifact.read_bytes()
    artifact.write_bytes({"same-size": b"\0" * len(honest), "longer": honest + b"more",
                          "truncated": honest[:-1]}[tamper])
    with pytest.raises(RuntimeError, match="digest recorded"):
        await VideoService.prepared_file(service, "p", item["id"], "waveform")


@pytest.mark.asyncio
async def test_derivative_without_a_recorded_digest_is_refused(tmp_path, monkeypatch):
    """Cache bytes with no manifest are arbitrary bytes; containment is not proof."""
    service, item, artifact = await prepared(tmp_path, monkeypatch)
    MediaLibrary(tmp_path).manifest_path(item["sha256"]).unlink()
    with pytest.raises(RuntimeError):
        await VideoService.prepared_file(service, "p", item["id"], "waveform")


@pytest.mark.asyncio
async def test_derivative_of_a_different_media_is_not_accepted(tmp_path, monkeypatch):
    service, item, artifact = await prepared(tmp_path, monkeypatch)
    manifest = MediaLibrary(tmp_path).manifest_path(item["sha256"])
    manifest.write_text(manifest.read_text(encoding="utf-8").replace(item["sha256"], "b" * 64),
                        encoding="utf-8")
    with pytest.raises(RuntimeError, match="does not describe this media"):
        await VideoService.prepared_file(service, "p", item["id"], "waveform")


# --------------------------------------------------------------------------
# Range handling on the descriptor response
# --------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("header,expected", [
    ("bytes=0-9", (0, 10)), ("bytes=10-19", (10, 10)), ("bytes=5-", None), ("bytes=-7", None)])
async def test_range_request_returns_206_with_exactly_those_bytes(tmp_path, header, expected):
    path, original, item = owned_media(tmp_path)
    handle, _ = await VideoService.media_file(studio(tmp_path, item), "p", item["id"])
    fd, info = handle.detach()
    response = descriptor_response(fd, info, media_type="audio/wav", range_header=header)
    status, headers, body = await drive(response)
    start, length = expected if expected else (
        (5, len(original) - 5) if header == "bytes=5-" else (len(original) - 7, 7))
    assert status == 206
    assert body == original[start:start + length]
    assert headers["content-length"] == str(length)
    assert headers["content-range"] == f"bytes {start}-{start + length - 1}/{len(original)}"
    assert headers["accept-ranges"] == "bytes"


@pytest.mark.asyncio
@pytest.mark.parametrize("header", ["bytes=100000-", "bytes=100000-100005", "bytes=-0", "bytes=9-3"])
async def test_unsatisfiable_range_is_416_and_serves_no_body(tmp_path, header):
    path, original, item = owned_media(tmp_path)
    handle, _ = await VideoService.media_file(studio(tmp_path, item), "p", item["id"])
    fd, info = handle.detach()
    status, headers, body = await drive(descriptor_response(fd, info, range_header=header))
    assert status == 416 and body == b""
    assert headers["content-range"] == f"bytes */{len(original)}"


@pytest.mark.asyncio
async def test_malformed_range_falls_back_to_the_whole_body(tmp_path):
    path, original, item = owned_media(tmp_path)
    handle, _ = await VideoService.media_file(studio(tmp_path, item), "p", item["id"])
    fd, info = handle.detach()
    status, headers, body = await drive(descriptor_response(fd, info, range_header="bytes=abc"))
    assert status == 200 and body == original and "content-range" not in headers


@pytest.mark.asyncio
async def test_http_range_request_reaches_the_endpoint(env):
    original = b"published-export-artifact-bytes" * 7
    jid, _ = await completed_export(env, original)
    part = await env.client.get(BASE + "/exports/" + jid + "/file", headers={"Range": "bytes=4-11"})
    assert part.status_code == 206 and part.content == original[4:12]
    assert part.headers["content-range"] == f"bytes 4-11/{len(original)}"
    over = await env.client.get(BASE + "/exports/" + jid + "/file",
                                headers={"Range": f"bytes={len(original)}-"})
    assert over.status_code == 416 and over.content == b""


# --------------------------------------------------------------------------
# Descriptor lifetime
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_repeated_requests_do_not_leak_descriptors(tmp_path, monkeypatch):
    path, original, item = owned_media(tmp_path)
    service = studio(tmp_path, item)

    async def once(range_header=None):
        handle, _ = await VideoService.media_file(service, "p", item["id"])
        fd, info = handle.detach()
        return await drive(descriptor_response(fd, info, range_header=range_header))

    await once()                                    # warm caches, imports, pools
    before = descriptors()
    for _ in range(25):
        status, _, body = await once()
        assert status == 200 and body == original
        assert (await once("bytes=0-3"))[0] == 206
        assert (await once("bytes=99999-"))[0] == 416   # 416 must close it too
    assert descriptors() <= before + 2, (before, descriptors())


@pytest.mark.asyncio
async def test_failed_verification_and_abandoned_handles_leak_nothing(tmp_path, monkeypatch):
    path, original, item = owned_media(tmp_path)
    service = studio(tmp_path, item)
    handle, _ = await VideoService.media_file(service, "p", item["id"])
    handle.close()
    before = descriptors()
    for _ in range(20):
        # A refused read must not keep the descriptor it opened to check.
        with pytest.raises(ValueError):
            await service.media.resolve_for_read({**item, "sha256": "c" * 64})
        (await VideoService.media_file(service, "p", item["id"]))[0].close()
    assert descriptors() <= before + 2, (before, descriptors())


@pytest.mark.asyncio
async def test_body_aborted_by_the_client_still_closes_the_descriptor(tmp_path):
    path, original, item = owned_media(tmp_path)
    handle, _ = await VideoService.media_file(studio(tmp_path, item), "p", item["id"])
    fd, info = handle.detach()
    response = descriptor_response(fd, info)
    before = descriptors()

    async def receive():
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body":
            raise ConnectionResetError("client went away")

    with pytest.raises(ConnectionResetError):
        await response({"type": "http", "method": "GET", "path": "/", "headers": []}, receive, send)
    assert descriptors() <= before
    with pytest.raises(OSError):
        os.fstat(fd)
