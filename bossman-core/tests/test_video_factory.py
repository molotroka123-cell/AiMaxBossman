"""Тесты Video Factory (Этап 7). Без внешних сервисов: Resource Brain
инъектируется с посеянным снимком, DB не нужен (durable-истина — job.json),
ffmpeg-слайсы пропускаются, если бинаря нет в окружении.

pytest-asyncio: asyncio_mode="auto" (см. pyproject) — async-тесты идут как есть.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from bossman import errors
from bossman.resource_brain import ResourceBrain, ResourceSnapshot
from bossman.video_factory import (
    JobState,
    Scene,
    VideoFactory,
    VideoJob,
)
from bossman.video_factory.ffmpeg import ffmpeg_available, next_take_path
from bossman.video_factory.providers import (
    SyntheticFFmpegProvider,
    assert_browser_provider_allowed,
    GuardedBrowserProvider,
)
from bossman.video_factory.queue import BoundedJobQueue

ffmpeg_required = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg binary not available")


# --- хелперы ----------------------------------------------------------------

def _generous_brain() -> ResourceBrain:
    """Brain с щедрым посеянным снимком и нулевым резервом — допуск всегда даёт."""
    brain = ResourceBrain(disk_reserve=0, max_ram_pressure=0.999)
    brain.set_snapshot(
        ResourceSnapshot(
            ram_total=10 ** 12, ram_available=10 ** 12,
            disk_total=10 ** 12, disk_free=10 ** 12,
        )
    )
    return brain


class SpyProvider:
    """Провайдер-шпион: считает вызовы и НИКОГДА не должен вызываться без аренды."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, *, prompt: str, duration_s: float, output_dir: str) -> str:
        self.calls += 1
        p = next_take_path(Path(output_dir))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"not-a-real-mp4")
        return str(p)


# --- create + атомарный чекпоинт (зеркалит приёмочный тест) ------------------

def test_create_and_checkpoint_writes_job_json(tmp_path: Path):
    v = VideoFactory(tmp_path, brain=_generous_brain())
    j = v.create("x", ["scene one", "scene two"])
    assert [s.id for s in j.scenes] == ["s001", "s002"]
    v.checkpoint_scene(j, "s001", status="complete", output="x.mp4")
    jf = tmp_path / j.id / "job.json"
    assert jf.exists()
    # никакого хвоста .tmp — запись атомарна (tmp+os.replace)
    assert not (tmp_path / j.id / "job.json.tmp").exists()
    reloaded = v.load(j.id)
    assert reloaded.scene("s001").status == "complete"
    assert reloaded.scene("s001").output == "x.mp4"


# --- допуск Resource Brain: отказ → сцена не идёт, артефакта нет -------------

async def test_admission_denied_scene_does_not_run(tmp_path: Path):
    brain = ResourceBrain()  # НЕТ снимка → acquire бросает ResourceExhausted (консервативно)
    spy = SpyProvider()
    v = VideoFactory(tmp_path, brain=brain, provider=spy, est_ram=1000, est_disk=1000)
    job = v.create("t", ["p"])
    with pytest.raises(errors.ResourceExhausted):
        await v.run_job(job)
    assert spy.calls == 0  # провайдер НЕ вызван без аренды
    scene_dir = tmp_path / job.id / "s001"
    assert not list(scene_dir.glob("take-*")) if scene_dir.exists() else True
    assert v.load(job.id).state == JobState.QUEUED  # backpressure вернул в очередь


async def test_admission_lease_released_in_finally(tmp_path: Path):
    brain = _generous_brain()
    v = VideoFactory(tmp_path, brain=brain, provider=SpyProvider(), est_ram=1000, est_disk=1000)
    job = v.create("t", ["p"])
    # SpyProvider пишет битый файл → валидация даст VideoInvalidOutput, но аренда
    # обязана сняться в finally при любом исходе.
    with pytest.raises((errors.VideoInvalidOutput, errors.VideoProviderFailed)):
        await v._generate_once(job, job.scenes[0])
    assert brain.leases() == []  # ни одной осиротевшей брони
    assert v._active_leases == set()


@ffmpeg_required
async def test_admission_acquire_and_release_happy_path(tmp_path: Path):
    brain = _generous_brain()
    v = VideoFactory(tmp_path, brain=brain, est_ram=1000, est_disk=1000)
    job = v.create("t", ["p"])
    await v.run_job(job)
    assert brain.leases() == []  # снята после генерации
    assert v.load(job.id).state == JobState.COMPLETE
    assert v.load(job.id).scene("s001").output == "take-001.mp4"


# --- ограниченная очередь: переполнение → QueueFull -------------------------

async def test_bounded_queue_raises_queue_full():
    q = BoundedJobQueue(maxsize=1)
    q.enqueue("job-1")
    with pytest.raises(errors.QueueFull):
        q.enqueue("job-2")
    assert q.qsize() == 1


# --- дубли (takes) не перезаписываются --------------------------------------

@ffmpeg_required
async def test_takes_not_overwritten_direct(tmp_path: Path):
    prov = SyntheticFFmpegProvider()
    scene_dir = tmp_path / "scene"
    p1 = await prov.generate(prompt="a", duration_s=1, output_dir=str(scene_dir))
    data1 = Path(p1).read_bytes()
    p2 = await prov.generate(prompt="a", duration_s=1, output_dir=str(scene_dir))
    assert Path(p1).name == "take-001.mp4"
    assert Path(p2).name == "take-002.mp4"
    assert Path(p1).exists() and Path(p1).read_bytes() == data1  # take-001 не тронут
    assert Path(p2).exists()


@ffmpeg_required
async def test_retry_writes_new_take_keeps_previous(tmp_path: Path):
    """Первая попытка даёт битый файл (валидация её бракует), вторая — валидный
    mp4. take-001 остаётся на диске, а выбранный дубль — take-002."""

    class FlakyProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def generate(self, *, prompt, duration_s, output_dir):
            self.calls += 1
            out_dir = Path(output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            take = next_take_path(out_dir)
            if self.calls == 1:
                take.write_bytes(b"")  # пустой → VideoInvalidOutput
                return str(take)
            from bossman.video_factory.ffmpeg import run_testsrc
            return await run_testsrc(take, duration_s)

    brain = _generous_brain()
    prov = FlakyProvider()
    v = VideoFactory(tmp_path, brain=brain, provider=prov, est_ram=1000, est_disk=1000, max_attempts=3)
    job = v.create("t", ["p"])
    await v.run_job(job)
    scene = v.load(job.id).scene("s001")
    assert scene.status == "complete"
    assert scene.attempts == 2
    assert scene.output == "take-002.mp4"
    sd = tmp_path / job.id / "s001"
    assert (sd / "take-001.mp4").exists()  # забракованный дубль СОХРАНЁН, не затёрт
    assert (sd / "take-002.mp4").exists()


# --- возобновление: готовую сцену не перегенерируем --------------------------

async def test_resume_skips_completed_scene(tmp_path: Path):
    brain = _generous_brain()
    spy = SpyProvider()
    v = VideoFactory(tmp_path, brain=brain, provider=spy, est_ram=1000, est_disk=1000)
    job = v.create("t", ["already done"])
    # помечаем сцену завершённой (как после прошлого прогона) и перезагружаем
    v.checkpoint_scene(job, "s001", status="complete", output="take-001.mp4")
    reloaded = v.load(job.id)
    await v.run_job(reloaded)
    assert spy.calls == 0  # завершённую сцену НЕ трогали
    assert v.load(job.id).state == JobState.COMPLETE


# --- ffmpeg argv (без shell) ------------------------------------------------

@ffmpeg_required
async def test_synthetic_provider_uses_exec_not_shell(tmp_path: Path, monkeypatch):
    import bossman.video_factory.ffmpeg as ffmod

    used = {"exec": 0}
    real_exec = asyncio.create_subprocess_exec

    async def spy_exec(*argv, **kw):
        used["exec"] += 1
        # argv — СПИСОК токенов; текст промпта в него не попадает.
        assert all(isinstance(a, str) for a in argv)
        return await real_exec(*argv, **kw)

    async def forbidden_shell(*a, **k):
        raise AssertionError("create_subprocess_shell must NEVER be used")

    monkeypatch.setattr(ffmod.asyncio, "create_subprocess_exec", spy_exec)
    monkeypatch.setattr(ffmod.asyncio, "create_subprocess_shell", forbidden_shell)

    prov = SyntheticFFmpegProvider()
    out = await prov.generate(prompt="rm -rf / ; drop table", duration_s=1, output_dir=str(tmp_path / "s"))
    assert Path(out).exists()
    assert used["exec"] >= 1
    # реальное видео: длительность > 0 и есть видеопоток
    from bossman.video_factory.ffmpeg import probe_media

    dur, has_v = await probe_media(out)
    assert dur > 0 and has_v is True


@ffmpeg_required
async def test_prompt_text_never_enters_ffmpeg_argv(tmp_path: Path):
    from bossman.video_factory.ffmpeg import build_testsrc_argv, ffmpeg_bin

    argv = build_testsrc_argv(ffmpeg_bin(), tmp_path / "o.mp4", 2.0)
    joined = " ".join(argv)
    assert "rm -rf" not in joined  # никакой пользовательский текст в argv
    assert argv[0] == ffmpeg_bin() and str(tmp_path / "o.mp4") in argv


# --- политика браузерного провайдера: STOP-on-wall + approval-gated submit ---

def test_browser_guard_blocks_captcha():
    with pytest.raises(errors.PolicyDenied):
        assert_browser_provider_allowed("https://svc.example/gen", "Please solve the CAPTCHA to continue")


def test_browser_guard_blocks_rate_limit():
    with pytest.raises(errors.PolicyDenied):
        assert_browser_provider_allowed("https://svc.example/gen", "Too many requests, slow down")


def test_browser_guard_blocks_blocked_domain(monkeypatch):
    monkeypatch.setenv("BOSSMAN_BROWSER_BLOCKED_DOMAINS", "evil.example")
    with pytest.raises(errors.PolicyDenied):
        assert_browser_provider_allowed("https://x.evil.example/gen", "clean page")


def test_browser_guard_refuses_auto_submit():
    # чистая страница, но финальный сабмит без подтверждения → отказ
    with pytest.raises(errors.PolicyDenied):
        assert_browser_provider_allowed("https://svc.example/gen", "ready", submitting=True, confirmed=False)
    # с подтверждением — проходит (ничего не бросает)
    assert_browser_provider_allowed("https://svc.example/gen", "ready", submitting=True, confirmed=True)


async def test_guarded_browser_provider_cannot_submit_past_captcha(tmp_path: Path):
    submitted = {"did": False}

    async def fetch_page():
        return "Verify you are human — CAPTCHA"

    async def request_approval():
        return True  # даже если бы одобрили — стена должна остановить РАНЬШЕ

    async def do_submit():
        submitted["did"] = True
        return str(tmp_path / "take-001.mp4")

    prov = GuardedBrowserProvider(
        url="https://svc.example/gen",
        fetch_page=fetch_page,
        request_approval=request_approval,
        do_submit=do_submit,
    )
    with pytest.raises(errors.PolicyDenied):
        await prov.generate(prompt="p", duration_s=1, output_dir=str(tmp_path))
    assert submitted["did"] is False  # авто-сабмит сквозь captcha невозможен


async def test_guarded_browser_provider_refuses_without_approval(tmp_path: Path):
    submitted = {"did": False}

    async def fetch_page():
        return "clean generate page"

    async def request_approval():
        return False  # пользователь НЕ подтвердил

    async def do_submit():
        submitted["did"] = True
        return "x"

    prov = GuardedBrowserProvider(
        url="https://svc.example/gen",
        fetch_page=fetch_page,
        request_approval=request_approval,
        do_submit=do_submit,
    )
    with pytest.raises(errors.PolicyDenied):
        await prov.generate(prompt="p", duration_s=1, output_dir=str(tmp_path))
    assert submitted["did"] is False  # без approval сабмит не происходит


async def test_guarded_browser_provider_submits_after_approval(tmp_path: Path):
    submitted = {"did": False}

    async def fetch_page():
        return "clean generate page"

    async def request_approval():
        return True

    async def do_submit():
        submitted["did"] = True
        return str(tmp_path / "take-001.mp4")

    prov = GuardedBrowserProvider(
        url="https://svc.example/gen",
        fetch_page=fetch_page,
        request_approval=request_approval,
        do_submit=do_submit,
    )
    out = await prov.generate(prompt="p", duration_s=1, output_dir=str(tmp_path))
    assert submitted["did"] is True and out.endswith("take-001.mp4")


# --- валидация вывода: пустой/битый → VideoInvalidOutput ---------------------

async def test_empty_output_is_invalid(tmp_path: Path):
    from bossman.video_factory.ffmpeg import validate_video_output

    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    with pytest.raises(errors.VideoInvalidOutput):
        await validate_video_output(empty)


async def test_truncated_output_is_invalid(tmp_path: Path):
    from bossman.video_factory.ffmpeg import validate_video_output

    trunc = tmp_path / "trunc.mp4"
    trunc.write_bytes(b"\x00\x00\x00\x18ftypmp42")  # обрезанный заголовок, не видео
    with pytest.raises(errors.VideoInvalidOutput):
        await validate_video_output(trunc)


# --- сверка после рестарта: RUNNING без аренды → INTERRUPTED -----------------

async def test_reconcile_marks_running_job_interrupted(tmp_path: Path):
    from bossman.video_factory.service import VideoFactoryService

    svc = VideoFactoryService(tmp_path, brain=_generous_brain())
    job = svc.factory.create("t", ["a", "b"])
    # имитируем аварию посреди генерации: state RUNNING, сцена RUNNING
    job.state = JobState.RUNNING
    job.scenes[0].status = "running"
    svc.factory.save(job)
    svc.reconcile()
    reloaded = svc.factory.load(job.id)
    assert reloaded.state == JobState.INTERRUPTED
    assert reloaded.scenes[0].status == "planned"  # сброшена для перегенерации новым дублем


# --- запрет запуска провайдера без аренды (adversarial) ---------------------

async def test_no_provider_call_without_lease(tmp_path: Path):
    """Даже адверсариально: если Brain отказывает, ни один take не пишется и
    провайдер не вызывается."""
    brain = ResourceBrain()  # без снимка → отказ
    spy = SpyProvider()
    v = VideoFactory(tmp_path, brain=brain, provider=spy, est_ram=1, est_disk=1)
    job = v.create("t", ["p1", "p2"])
    with pytest.raises(errors.ResourceExhausted):
        await v.run_job(job)
    assert spy.calls == 0
    # ни одного артефакта ни в одной сцене
    for sc in ("s001", "s002"):
        sd = tmp_path / job.id / sc
        assert not (sd.exists() and list(sd.glob("take-*")))
