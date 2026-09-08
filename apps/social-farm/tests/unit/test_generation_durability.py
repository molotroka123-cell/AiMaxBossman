"""Что переживает перезапуск, и что после него НЕ повторяется.

Главная опасность здесь не «работа потерялась», а «работа выполнилась дважды».
Первое стоит владельцу минуты, второе — денег, второго файла и невозможности
понять задним числом, какой из двух настоящий.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from social_farm.generation.browser_worker import (BrowserGenerationWorker,
                                                   BrowserWorkerConfig)
from social_farm.generation.higgsfield_browser_contracts import (
    ArtifactReceipt, BrowserGenerationObservation, BrowserGenerationRequest,
    BrowserGenerationState, MediaKind, SubmissionReceipt)
from social_farm.generation.job_store import (DEFAULT_LEASE_SECONDS,
                                              GenerationJobStore, StoreLocked)

import higgsfield_kit as kit
from conftest import make_png


class CountingAdapter:
    """Адаптер, который считает отправки. Больше он ничего не доказывает."""

    provider_name = "counting"

    def __init__(self, workspace: Path, *, ready_after: int = 0) -> None:
        self.workspace = workspace
        self.submits = 0
        self.polls = 0
        self.collects = 0
        self.ready_after = ready_after
        self.crash_after_submit = False

    async def prepare(self, request):
        return BrowserGenerationObservation(request.job_id,
                                            BrowserGenerationState.READY,
                                            time.time())

    async def submit(self, request):
        self.submits += 1
        receipt = SubmissionReceipt(
            job_id=request.job_id, provider=self.provider_name,
            submitted_at_epoch_s=time.time(),
            evidence={"signals": "job_card_appeared,progress_text"},
            provider_job_id=f"provider-{self.submits}")
        if self.crash_after_submit:
            raise _Crash("процесс упал сразу после отправки")
        return receipt

    async def poll(self, request, receipt):
        self.polls += 1
        state = (BrowserGenerationState.OUTPUT_READY
                 if self.polls > self.ready_after
                 else BrowserGenerationState.WAITING_PROVIDER)
        return BrowserGenerationObservation(request.job_id, state, time.time())

    async def collect(self, request, receipt):
        self.collects += 1
        self.workspace.mkdir(parents=True, exist_ok=True)
        target = self.workspace / f"{request.job_id}.png"
        target.write_bytes(make_png(400, 400))
        return target


class _Crash(RuntimeError):
    """Падение процесса, изображённое исключением."""


def task(tmp_path: Path, **kwargs) -> BrowserGenerationRequest:
    kwargs.setdefault("mission_id", "m-1")
    kwargs.setdefault("media_kind", MediaKind.IMAGE)
    kwargs.setdefault("prompt", "кадр")
    kwargs.setdefault("output_workspace", tmp_path / "approved")
    return BrowserGenerationRequest(**kwargs)


def fast() -> BrowserWorkerConfig:
    return BrowserWorkerConfig(poll_interval_s=0.001, max_poll_interval_s=0.002,
                               max_poll_seconds=5, min_artifact_bytes=100)


# ------------------------------------------------------------------ перезапуск

async def test_a_restart_does_not_submit_the_same_job_twice(tmp_path):
    """Работа уже у провайдера. Второй работник обязан её ДОЖДАТЬСЯ, а не
    отправить заново."""
    adapter = CountingAdapter(tmp_path / "approved", ready_after=1)
    store = GenerationJobStore(tmp_path / "jobs.json")
    request = task(tmp_path)

    first = BrowserGenerationWorker(adapter=adapter, store=store, config=fast(),
                                    owner="worker-A")
    result = await first.run(request)
    assert isinstance(result, ArtifactReceipt)
    assert adapter.submits == 1

    # Новый процесс, новое хранилище того же файла, та же работа.
    reborn = GenerationJobStore(tmp_path / "jobs.json")
    second = BrowserGenerationWorker(adapter=adapter, store=reborn, config=fast(),
                                     owner="worker-B")
    again = await second.run(request)
    assert isinstance(again, BrowserGenerationObservation)
    assert again.state is BrowserGenerationState.COMPLETE
    assert adapter.submits == 1, "завершённая работа не отправляется заново"


async def test_an_in_flight_job_resumes_at_polling_after_a_restart(tmp_path):
    """Отправка записана, ожидание оборвано. Продолжаем с ожидания."""
    adapter = CountingAdapter(tmp_path / "approved")
    store = GenerationJobStore(tmp_path / "jobs.json")
    request = task(tmp_path)

    # Изображаем состояние после падения: отправка отмечена, работа ждёт.
    record = store.create(job_id=request.job_id, mission_id=request.mission_id,
                          provider=adapter.provider_name)
    store.record_submission(record.job_id, submitted_at_epoch_s=time.time(),
                            evidence={"signals": "job_card_appeared,progress_text"},
                            provider_job_ref="provider-1")
    record = store.get(record.job_id)
    record.state = BrowserGenerationState.WAITING_PROVIDER
    store.save(record)

    worker = BrowserGenerationWorker(adapter=adapter, store=store, config=fast(),
                                     owner="worker-B")
    result = await worker.run(request)

    assert isinstance(result, ArtifactReceipt)
    assert adapter.submits == 0, "перезапуск не отправляет работу заново"
    assert adapter.collects == 1


async def test_a_crash_between_click_and_record_is_the_only_double_risk(tmp_path):
    """Честная граница: падение ДО записи означает, что об отправке не знает
    никто, и работа будет отправлена заново. Здесь важно, что состояние это
    показывает, а не делает вид, что всё хорошо."""
    adapter = CountingAdapter(tmp_path / "approved")
    adapter.crash_after_submit = True
    store = GenerationJobStore(tmp_path / "jobs.json")
    worker = BrowserGenerationWorker(adapter=adapter, store=store, config=fast(),
                                     owner="worker-A")

    result = await worker.run(task(tmp_path, max_attempts=1))
    assert isinstance(result, BrowserGenerationObservation)
    assert result.state is BrowserGenerationState.FAILED
    record = store.get(result.job_id)
    assert record is not None and not record.submitted, \
        "неподтверждённая отправка не записывается как отправка"


async def test_the_submission_receipt_survives_in_durable_state(tmp_path):
    adapter = CountingAdapter(tmp_path / "approved")
    store = GenerationJobStore(tmp_path / "jobs.json")
    request = task(tmp_path)
    await BrowserGenerationWorker(adapter=adapter, store=store, config=fast(),
                                  owner="w").run(request)

    reloaded = GenerationJobStore(tmp_path / "jobs.json").get(request.job_id)
    assert reloaded is not None
    assert reloaded.submitted
    assert reloaded.submission_evidence["signals"]
    assert reloaded.provider_job_ref == "provider-1"


async def test_the_store_refuses_a_second_submission_mark(tmp_path):
    store = GenerationJobStore(tmp_path / "jobs.json")
    store.create(job_id="j", mission_id="m", provider="p")
    store.record_submission("j", submitted_at_epoch_s=time.time(), evidence={"a": "b"})
    with pytest.raises(RuntimeError, match="уже отмечена отправленной"):
        store.record_submission("j", submitted_at_epoch_s=time.time(),
                                evidence={"a": "b"})


# ------------------------------------------------------------------ аренда

async def test_a_leased_job_is_not_picked_up_by_a_second_worker(tmp_path):
    store = GenerationJobStore(tmp_path / "jobs.json")
    request = task(tmp_path)
    store.create(job_id=request.job_id, mission_id=request.mission_id, provider="c")
    assert store.claim(request.job_id, owner="worker-A") is not None

    adapter = CountingAdapter(tmp_path / "approved")
    second = BrowserGenerationWorker(adapter=adapter, store=store, config=fast(),
                                     owner="worker-B")
    result = await second.run(request)
    assert isinstance(result, BrowserGenerationObservation)
    assert "worker-A" in result.safe_message
    assert adapter.submits == 0, "две отправки одной работы — это две генерации"


def test_an_expired_lease_is_taken_over_not_waited_on(tmp_path):
    """Упавший работник не держит работу вечно."""
    store = GenerationJobStore(tmp_path / "jobs.json")
    store.create(job_id="j", mission_id="m", provider="p")
    assert store.claim("j", owner="worker-A", ttl_s=0.05) is not None
    assert store.claim("j", owner="worker-B") is None

    time.sleep(0.06)
    taken = store.claim("j", owner="worker-B")
    assert taken is not None and taken.lease_owner == "worker-B"


def test_a_released_lease_is_free_immediately(tmp_path):
    store = GenerationJobStore(tmp_path / "jobs.json")
    store.create(job_id="j", mission_id="m", provider="p")
    store.claim("j", owner="worker-A")
    assert store.release("j", owner="worker-B") is False, "чужую аренду не снять"
    assert store.release("j", owner="worker-A") is True
    assert store.claim("j", owner="worker-B") is not None


async def test_the_worker_releases_its_lease_even_when_the_job_fails(tmp_path):
    adapter = CountingAdapter(tmp_path / "approved")
    adapter.crash_after_submit = True
    store = GenerationJobStore(tmp_path / "jobs.json")
    request = task(tmp_path, max_attempts=1)
    await BrowserGenerationWorker(adapter=adapter, store=store, config=fast(),
                                  owner="worker-A").run(request)
    record = store.get(request.job_id)
    assert record is not None and record.lease_owner == ""


def test_a_held_file_lock_is_reported_not_ignored(tmp_path):
    """Молча пройти мимо чужого замка значит начать ту самую гонку."""
    from social_farm.generation import job_store as module

    path = tmp_path / "jobs.json"
    store = GenerationJobStore(path)
    store.create(job_id="j", mission_id="m", provider="p")

    lock = path.with_name(path.name + ".lock")
    lock.write_text("999999 held", encoding="utf-8")
    original = module.LOCK_WAIT_SECONDS
    module.LOCK_WAIT_SECONDS = 0.05
    try:
        with pytest.raises(StoreLocked):
            store.claim("j", owner="worker-A")
    finally:
        module.LOCK_WAIT_SECONDS = original
        lock.unlink()


# ------------------------------------------------------------------ опрос

async def test_polling_backs_off_instead_of_hammering_the_provider(tmp_path):
    adapter = CountingAdapter(tmp_path / "approved", ready_after=4)
    store = GenerationJobStore(tmp_path / "jobs.json")
    config = BrowserWorkerConfig(poll_interval_s=0.01, poll_backoff=2.0,
                                 max_poll_interval_s=0.04, max_poll_seconds=5,
                                 min_artifact_bytes=100)
    started = time.monotonic()
    result = await BrowserGenerationWorker(adapter=adapter, store=store,
                                           config=config, owner="w").run(task(tmp_path))
    elapsed = time.monotonic() - started
    assert isinstance(result, ArtifactReceipt)
    # 0.01 + 0.02 + 0.04 + 0.04 = 0.11 против 0.04 при постоянном шаге.
    assert elapsed >= 0.10, f"шаг опроса не рос: {elapsed:.3f} с"


def test_a_shrinking_backoff_is_refused_by_configuration(tmp_path):
    with pytest.raises(ValueError):
        BrowserWorkerConfig(poll_backoff=0.5)
    with pytest.raises(ValueError):
        BrowserWorkerConfig(poll_interval_s=10, max_poll_interval_s=1)
    with pytest.raises(ValueError):
        BrowserWorkerConfig(download_attempts=0)


# ------------------------------------------------------------- забор с повтором

async def test_a_failed_download_is_retried_once_and_the_media_is_not(tmp_path):
    """Сорванная загрузка может пройти со второго раза. Не-медиа — нет."""
    space = kit.workspace(tmp_path / "contexts")
    quarantine = space.prepare()

    arrivals = {"count": 0}

    def flaky_download(page, element):
        if element.attributes.get("data-testid") != "result-download":
            return
        arrivals["count"] += 1
        if arrivals["count"] >= 2:            # первая загрузка срывается
            (quarantine / "result.png").write_bytes(make_png(400, 400))

    dom = kit.on(kit.ready_page(),
                 on_click=kit.both(kit.submitting(then_ready=True), flaky_download))
    adapter = kit.adapter(dom, quarantine, space=space)
    store = GenerationJobStore(tmp_path / "jobs.json")
    worker = BrowserGenerationWorker(
        adapter=adapter, store=store,
        config=BrowserWorkerConfig(poll_interval_s=0.001, max_poll_interval_s=0.002,
                                   max_poll_seconds=5, min_artifact_bytes=100,
                                   download_attempts=2),
        owner="w")

    result = await worker.run(task(tmp_path))
    assert isinstance(result, ArtifactReceipt), result
    assert arrivals["count"] == 2, "ровно один повтор, а не цикл"


async def test_invalid_media_is_not_downloaded_again(tmp_path):
    space = kit.workspace(tmp_path / "contexts")
    quarantine = space.prepare()
    clicks = {"count": 0}

    def bad_download(page, element):
        if element.attributes.get("data-testid") != "result-download":
            return
        clicks["count"] += 1
        (quarantine / f"error-{clicks['count']}.png").write_bytes(
            b"<!doctype html><html>error</html>" + b" " * 4000)

    dom = kit.on(kit.ready_page(),
                 on_click=kit.both(kit.submitting(then_ready=True), bad_download))
    adapter = kit.adapter(dom, quarantine, space=space)
    store = GenerationJobStore(tmp_path / "jobs.json")
    worker = BrowserGenerationWorker(
        adapter=adapter, store=store,
        config=BrowserWorkerConfig(poll_interval_s=0.001, max_poll_interval_s=0.002,
                                   max_poll_seconds=5, min_artifact_bytes=100,
                                   download_attempts=3),
        owner="w")

    result = await worker.run(task(tmp_path))
    assert isinstance(result, BrowserGenerationObservation)
    assert result.state is BrowserGenerationState.FAILED
    assert clicks["count"] == 1, "не-медиа вторым скачиванием медиа не станет"
