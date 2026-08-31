"""Тесты тонкого адаптера OpenRouterVideoProvider (видео через /api/v1/videos).

Без сети: async-клиент стабится; бюджетный guard и маппинг ошибок проверяются
на детерминированных стабах. Интеграция с VideoFactory — через посеянный Brain
и подменённую валидацию (без зависимости от ffmpeg-бинаря).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bossman import errors
from bossman.resource_brain import ResourceBrain, ResourceSnapshot
from bossman.video_factory import VideoFactory
from bossman.video_factory import pipeline as vf_pipeline
from bossman.video_factory.providers import OpenRouterVideoProvider


def _generous_brain() -> ResourceBrain:
    brain = ResourceBrain(disk_reserve=0, max_ram_pressure=0.999)
    brain.set_snapshot(
        ResourceSnapshot(
            ram_total=10 ** 12, ram_available=10 ** 12,
            disk_total=10 ** 12, disk_free=10 ** 12,
        )
    )
    return brain


class _Resp:
    def __init__(self, status_code=200, json_data=None, content=b""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = ""
        self.content = content

    def json(self):
        return self._json


class StubClient:
    """Стаб OpenRouter: /videos → job, /videos/{id} → pending→completed, content → mp4."""

    def __init__(self, *, fail_status: int | None = None, terminal: str = "completed"):
        self.fail_status = fail_status
        self.terminal = terminal
        self.polls = 0
        self.posts: list[dict] = []

    async def post(self, url, headers=None, json=None, timeout=None):
        self.posts.append(json or {})
        if self.fail_status:
            return _Resp(self.fail_status, {"error": {"message": "rejected"}}, b"")
        return _Resp(202, {"id": "job1", "polling_url": "u", "status": "pending"})

    async def get(self, url, headers=None, timeout=None):
        if url.endswith("/content"):
            return _Resp(200, None, b"mp4-bytes")
        self.polls += 1
        if self.polls == 1:
            return _Resp(200, {"id": "job1", "status": "in_progress", "usage": {"cost": 0.30}})
        return _Resp(200, {
            "id": "job1", "status": self.terminal,
            "usage": {"cost": 0.30},
            "unsigned_urls": ["https://x/content"] if self.terminal == "completed" else [],
            **({} if self.terminal == "completed" else {"error": "boom"}),
        })


def _provider(client, **kw) -> OpenRouterVideoProvider:
    return OpenRouterVideoProvider(
        api_key="test-key", poll_interval_s=0.0, client=client, **kw
    )


async def test_submit_payload_shape_and_download(tmp_path: Path):
    c = StubClient()
    p = _provider(c, model="bytedance/seedance-2.0-mini")
    out = await p.generate(prompt="p", duration_s=10, output_dir=str(tmp_path))
    assert Path(out).exists() and Path(out).read_bytes() == b"mp4-bytes"
    assert Path(out).name.startswith("take-")
    body = c.posts[0]
    assert body["model"] == "bytedance/seedance-2.0-mini"
    assert body["duration"] == 10
    assert body["resolution"] == "720p"
    assert body["aspect_ratio"] == "9:16"
    assert p.spend_usd == pytest.approx(0.30)  # последняя стоимость из poll


async def test_submit_http_error_raises_provider_failed(tmp_path: Path):
    p = _provider(StubClient(fail_status=400))
    with pytest.raises(errors.VideoProviderFailed):
        await p.generate(prompt="p", duration_s=10, output_dir=str(tmp_path))


async def test_failed_job_raises_provider_failed(tmp_path: Path):
    p = _provider(StubClient(terminal="failed"))
    with pytest.raises(errors.VideoProviderFailed):
        await p.generate(prompt="p", duration_s=10, output_dir=str(tmp_path))


async def test_budget_cap_blocks_next_generate(tmp_path: Path):
    p = _provider(StubClient(), budget_cap=0.25)
    await p.generate(prompt="p", duration_s=10, output_dir=str(tmp_path))
    assert p.spend_usd >= 0.25
    with pytest.raises(errors.VideoProviderFailed) as ei:
        await p.generate(prompt="p", duration_s=10, output_dir=str(tmp_path))
    assert "budget cap" in str(ei.value)


async def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(errors.VideoProviderFailed):
        OpenRouterVideoProvider()


async def test_video_factory_integration(tmp_path: Path, monkeypatch):
    async def _no_validation(path):
        return None

    monkeypatch.setattr(vf_pipeline, "validate_video_output", _no_validation)
    v = VideoFactory(tmp_path, brain=_generous_brain(), provider=_provider(StubClient()))
    job = v.create("openrouter-live-shape", ["scene one", "scene two"])
    done = await v.run_job(job)
    assert done.state.value == "complete"
    for s in done.scenes:
        assert s.status == "complete" and s.takes and s.output


def test_enabled_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("BOSSMAN_VIDEO_OPENROUTER", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert OpenRouterVideoProvider.enabled() is False
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert OpenRouterVideoProvider.enabled() is False  # один флаг — недостаточно
    monkeypatch.setenv("BOSSMAN_VIDEO_OPENROUTER", "1")
    assert OpenRouterVideoProvider.enabled() is True
