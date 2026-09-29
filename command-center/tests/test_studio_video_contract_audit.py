"""Audit 2026-09-28 (video/Studio): a cloud video is checked against what was ordered.

Injected HTTP transport only — no provider is contacted, nothing is spent.
"""
import httpx
import pytest
from bcc.features.images import process_one

MODEL = 'openrouter:bytedance/seedance-2.0-mini'   # catalog: 10 s, 9:16, 720p
POLICY = {'enabled': True, 'free_only': False, 'cloud_budget_usd': 10, 'per_job_usd': 2,
          'prices': {MODEL: 1.0}, 'download_hosts': ['cdn.example.test']}


async def clip(path, width, height, seconds, *, rotation=None):
    from bcc.video_studio.media import binary, process
    raw = path.with_name('raw-' + path.name) if rotation is not None else path
    await process([binary('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
                   f'color=c=blue:s={width}x{height}:r=5:d={seconds}', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                   str(raw)])
    if rotation is not None:
        try:
            await process([binary('ffmpeg'), '-v', 'error', '-display_rotation', str(rotation), '-i', str(raw),
                           '-c', 'copy', str(path)])
        except ValueError:
            pytest.skip('this ffmpeg build cannot write a display matrix (-display_rotation)')
    return path


def cloud(monkeypatch, video):
    from bcc.studio.providers import openrouter
    cls = openrouter.OpenRouterProvider

    def serve(r):
        if r.url.host == 'cdn.example.test':
            return httpx.Response(200, content=video.read_bytes())
        if r.url.path.endswith('/auth/key'):
            return httpx.Response(200, json={'data': {'limit_remaining': 100}})
        if r.method == 'POST':
            return httpx.Response(202, json={'id': 'vid-contract'})
        return httpx.Response(200, json={'status': 'completed', 'usage': {'cost': 0.5},
                                         'unsigned_urls': ['https://cdn.example.test/o.mp4']})
    monkeypatch.setattr(openrouter, 'OpenRouterProvider',
                        lambda key, **kw: cls(key, transport=httpx.MockTransport(serve), **kw))
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test-only-key')


async def run_job(env):
    from bcc.studio.governance import save_policy
    await save_policy(env.svc, POLICY)
    job = (await env.client.post('/api/studio/jobs', json={'model': MODEL, 'prompt': 'x'})).json()
    await process_one(env.svc)
    return (await env.client.get(f"/api/studio/jobs/{job['id']}")).json()


async def test_probe_reports_display_size_of_a_rotated_phone_clip(tmp_path):
    """A vertical phone clip is stored landscape with a ±90° display matrix."""
    from bcc.video_studio.media import probe
    folder = tmp_path / 'Папка с пробелом'
    folder.mkdir()
    path = await clip(folder / 'phone.mp4', 320, 180, 1, rotation=90)
    info = await probe(path)
    assert (info['width'], info['height']) == (180, 320)
    assert info['metadata']['rotation'] % 180 == 90


async def test_wrong_duration_and_aspect_is_not_a_pass(env, monkeypatch, tmp_path):
    video = await clip(tmp_path / 'wrong.mp4', 320, 180, 2)          # 16:9, 2 s for a 9:16, 10 s order
    cloud(monkeypatch, video)
    row = await run_job(env)
    assert row['status'] == 'failed' and row['studio']['reason'] == 'malformed', row
    assert 'requested 10 s' in row['error']
    assert (await env.client.get('/api/studio/runs')).json()['total'] == 0


async def test_wrong_aspect_alone_is_named(env, monkeypatch, tmp_path):
    video = await clip(tmp_path / 'landscape.mp4', 320, 180, 10)
    cloud(monkeypatch, video)
    row = await run_job(env)
    assert row['status'] == 'failed' and 'requested aspect 9:16' in row['error'], row


async def test_ordered_clip_passes_including_rotated_vertical(env, monkeypatch, tmp_path):
    video = await clip(tmp_path / 'rotated.mp4', 320, 180, 10, rotation=90)   # displays 180x320
    cloud(monkeypatch, video)
    row = await run_job(env)
    assert row['status'] == 'completed', row
    run = (await env.client.get('/api/studio/runs')).json()['items'][0]
    assert (run['provenance']['output']['width'], run['provenance']['output']['height']) == (180, 320)


def test_partial_output_is_only_checked_for_aspect():
    from bcc.studio.runtime import StudioError, check_requested_shape
    settings = {'duration': 10, 'aspect_ratio': '9:16'}
    check_requested_shape(settings, {'width': 720, 'height': 1280, 'duration_ms': 5000}, partial=True)
    with pytest.raises(StudioError):
        check_requested_shape(settings, {'width': 1280, 'height': 720, 'duration_ms': 5000}, partial=True)
    check_requested_shape(settings, {'width': 720, 'height': 1280, 'duration_ms': 10040})
