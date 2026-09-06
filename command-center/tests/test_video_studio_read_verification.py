"""Concurrent GET verification with real media, without trusting stat as content."""
import asyncio
import os
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bcc.video_studio import media as media_module
from bcc.video_studio.media import MediaLibrary, binary, process
from bcc.video_studio.service import VideoService


async def owned_media(tmp_path):
    source = tmp_path / "source.mp4"
    await process([binary("ffmpeg"), "-v", "error", "-nostdin", "-y",
        "-f", "lavfi", "-i", "color=red:size=64x64:rate=25:duration=0.2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)])
    library = MediaLibrary(tmp_path)
    item = await library.import_file(source)
    service = SimpleNamespace(media=library, store=SimpleNamespace(
        get=AsyncMock(return_value={"media": {item["id"]: item}})))
    return library, item, service


@pytest.mark.asyncio
async def test_twenty_concurrent_gets_share_one_full_hash(tmp_path, monkeypatch):
    library, item, service = await owned_media(tmp_path)
    original = media_module.digest_file
    entered, release = threading.Event(), threading.Event()
    calls = []

    def slow_hash(path):
        calls.append(path)
        entered.set()
        assert release.wait(5), "test worker was not released"
        return original(path)

    monkeypatch.setattr(media_module, "digest_file", slow_hash)
    tasks = [asyncio.create_task(VideoService.media_file(service, "p", item["id"])) for _ in range(20)]
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        await asyncio.sleep(0.05)
    finally:
        release.set()
    results = await asyncio.gather(*tasks)
    assert len(calls) == 1
    assert all(result[0] == library.root / item["relative_path"] for result in results)


@pytest.mark.asyncio
async def test_sequential_gets_rehash_even_after_same_size_restored_mtime(tmp_path):
    library, item, service = await owned_media(tmp_path)
    path, _ = await VideoService.media_file(service, "p", item["id"])
    before = path.stat()
    with path.open("r+b") as stream:
        stream.seek(-1, 2)
        last = stream.read(1)
        stream.seek(-1, 2)
        stream.write(bytes([last[0] ^ 1]))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(ValueError, match="hash mismatch"):
        await VideoService.media_file(service, "p", item["id"])


@pytest.mark.asyncio
async def test_cancelled_get_does_not_cancel_other_waiter(tmp_path, monkeypatch):
    library, item, service = await owned_media(tmp_path)
    original = media_module.digest_file
    entered, release = threading.Event(), threading.Event()
    calls = []

    def slow_hash(path):
        calls.append(path)
        entered.set()
        assert release.wait(5)
        return original(path)

    monkeypatch.setattr(media_module, "digest_file", slow_hash)
    first = asyncio.create_task(VideoService.media_file(service, "p", item["id"]))
    second = asyncio.create_task(VideoService.media_file(service, "p", item["id"]))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not second.done()
    finally:
        release.set()
    assert (await second)[0] == library.root / item["relative_path"]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_verified_content_changed_during_hash_is_rejected(tmp_path, monkeypatch):
    library, item, service = await owned_media(tmp_path)
    original = media_module.digest_file

    def replace_after_hash(path):
        result = original(path)
        # Even a legitimate hash result must not authorize changed file identity.
        with path.open("ab") as stream:
            stream.write(b"mutation")
        return result

    monkeypatch.setattr(media_module, "digest_file", replace_after_hash)
    with pytest.raises(ValueError, match="changed during"):
        await VideoService.media_file(service, "p", item["id"])


@pytest.mark.asyncio
async def test_bounded_pool_rejects_overload_and_releases_capacity(tmp_path):
    from bcc.video_studio.read_verification import ReadVerifier
    verifier = ReadVerifier(workers=1, capacity=2)
    entered, release = threading.Event(), threading.Event()
    threads = []
    paths = [tmp_path / str(index) for index in range(3)]
    for path in paths:
        path.write_bytes(b"test")

    def verify(reference):
        threads.append(threading.current_thread().name)
        entered.set()
        assert release.wait(5)
        return paths[int(reference["relative_path"])]

    library = SimpleNamespace(owned_path=lambda ref: paths[int(ref["relative_path"])], resolve=verify)
    refs = [{"relative_path": str(index), "sha256": "a" * 64} for index in range(3)]
    first = asyncio.create_task(verifier.resolve(library, refs[0]))
    second = asyncio.create_task(verifier.resolve(library, refs[1]))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        with pytest.raises(RuntimeError, match="capacity exceeded"):
            await verifier.resolve(library, refs[2])
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        # Cancelled, queued work remains admitted until verification finishes.
        with pytest.raises(RuntimeError, match="capacity exceeded"):
            await verifier.resolve(library, refs[2])
    finally:
        release.set()
        await asyncio.gather(first, second, return_exceptions=True)
        await asyncio.to_thread(verifier._pool.shutdown, wait=True)
    assert verifier._pending == {}
    assert len(threads) == 2
    assert all(name.startswith("video-read-hash") for name in threads)


@pytest.mark.asyncio
async def test_hash_failure_is_not_cached(tmp_path, monkeypatch):
    library, item, service = await owned_media(tmp_path)
    original = media_module.digest_file
    calls = []

    def fail_once(path):
        calls.append(path)
        if len(calls) == 1:
            raise OSError("temporary read failure")
        return original(path)

    monkeypatch.setattr(media_module, "digest_file", fail_once)
    with pytest.raises(OSError, match="temporary"):
        await VideoService.media_file(service, "p", item["id"])
    assert (await VideoService.media_file(service, "p", item["id"]))[0].is_file()
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_confinement_and_hash_reference_checked_per_caller(tmp_path):
    library, item, _ = await owned_media(tmp_path)
    for altered in ({**item, "relative_path": "../source.mp4"},
                    {**item, "sha256": "a" * 64}, {**item, "sha256": None}):
        with pytest.raises(ValueError):
            await library.resolve_for_read(altered)


@pytest.mark.asyncio
async def test_completed_hash_waiting_for_cleanup_is_never_reused(tmp_path):
    from concurrent.futures import Future
    from bcc.video_studio.read_verification import ReadVerifier, identity

    verifier = ReadVerifier()
    path = tmp_path / 'file'
    path.write_bytes(b'fresh')
    reference = {'sha256': 'a' * 64, 'relative_path': 'file'}
    calls = []
    library = SimpleNamespace(owned_path=lambda ref: path,
                              resolve=lambda ref: calls.append(ref) or path)
    stale = Future()
    stale.set_result(path)
    verifier._pending[(str(path), reference['sha256'], identity(path))] = stale
    try:
        assert await verifier.resolve(library, reference) == path
        assert len(calls) == 1
    finally:
        await asyncio.to_thread(verifier._pool.shutdown, wait=True)


@pytest.mark.asyncio
async def test_reference_mutation_cannot_redirect_admitted_read(tmp_path, monkeypatch):
    library, item, service = await owned_media(tmp_path)
    original = media_module.digest_file
    expected = library.root / item['relative_path']

    def mutate_reference(path):
        item['relative_path'] = '../private.mp4'
        item['sha256'] = 'f' * 64
        return original(path)

    monkeypatch.setattr(media_module, 'digest_file', mutate_reference)
    assert (await VideoService.media_file(service, 'p', item['id']))[0] == expected
    with pytest.raises(ValueError):
        await VideoService.media_file(service, 'p', item['id'])


@pytest.mark.asyncio
async def test_busy_http_response_is_retryable_without_exposing_storage():
    from fastapi import HTTPException
    from bcc.features.video_studio import guarded
    from bcc.video_studio.read_verification import ReadVerificationBusy

    async def overloaded():
        raise ReadVerificationBusy('private storage details')

    with pytest.raises(HTTPException) as error:
        await guarded(overloaded())
    assert error.value.status_code == 503
    assert error.value.headers == {'Retry-After': '1'}
    assert 'private' not in error.value.detail


@pytest.mark.asyncio
async def test_authenticated_media_download_rechecks_actual_content(env, tmp_path):
    import uuid
    base = '/api/video-studio'
    _, item, _ = await owned_media(tmp_path)
    content = (tmp_path / item['relative_path']).read_bytes()
    project = await env.client.post(base + '/projects', json={
        'name': 'readback', 'operation_id': uuid.uuid4().hex})
    assert project.status_code == 200, project.text
    project_id = project.json()['project']['id']
    upload = await env.client.post(base + '/media', params={
        'project_id': project_id, 'filename': 'source.mp4',
        'expected_revision': 0, 'operation_id': uuid.uuid4().hex}, content=content)
    assert upload.status_code == 200, upload.text
    uploaded = upload.json()['media']
    url = base + '/media/' + uploaded['id'] + '/file'
    download = await env.client.get(url, params={'project_id': project_id})
    assert download.status_code == 200, download.text
    assert download.content == content
    from bcc.features.video_studio import service
    # Resolve through the same host service used by the HTTP endpoint.
    from starlette.requests import Request
    studio = service(Request({'type': 'http', 'app': env.app}))
    path = studio.media.owned_path(uploaded)
    with path.open('ab') as stream:
        stream.write(b'changed')
    refused = await env.client.get(url, params={'project_id': project_id})
    assert refused.status_code == 422, refused.text
    assert 'hash mismatch' in refused.text
