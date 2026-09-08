"""Скачанный файл становится результатом только после измерения.

Каждая проверка здесь про один и тот же вопрос: что окажется в утверждённой
рабочей области. Ответ обязан быть «медиа того типа, который просили», а не
«то, что браузер положил в каталог».
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from social_farm.generation.higgsfield_adapter import (DownloadFailed,
                                                       HiggsfieldAdapterConfig,
                                                       HiggsfieldBrowserAdapter,
                                                       InvalidGeneratedMedia)
from social_farm.generation.higgsfield_browser_contracts import (
    BrowserGenerationRequest, MediaKind)
from social_farm.generation.media_gate import MediaRejection
from social_farm.generation.workspace import WorkspaceEscape

import higgsfield_kit as kit
from conftest import make_png, truncate

needs_ffmpeg = pytest.mark.skipif(
    subprocess.run(["which", "ffmpeg"], capture_output=True).returncode != 0,
    reason="в среде нет ffmpeg; настоящий mp4 собрать нечем")


def request(tmp_path: Path, kind: MediaKind = MediaKind.IMAGE, **kwargs):
    kwargs.setdefault("mission_id", "m-1")
    kwargs.setdefault("prompt", "витрина ателье ночью")
    kwargs.setdefault("output_workspace", tmp_path / "approved")
    if kind is MediaKind.VIDEO:
        kwargs.setdefault("duration_seconds", 3.0)
    return BrowserGenerationRequest(media_kind=kind, **kwargs)


def tiny_mp4(tmp_path: Path) -> bytes:
    target = tmp_path / "tiny.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(target)],
        check=True, capture_output=True)
    return target.read_bytes()


async def ready_to_collect(tmp_path: Path, *, payload: bytes | None,
                           name: str = "higgsfield-result.mp4",
                           arrives: bool = True, kind: MediaKind = MediaKind.IMAGE):
    """Довести работу до момента, когда остаётся только забрать файл."""
    space = kit.workspace(tmp_path / "contexts")
    quarantine = space.prepare()
    dom = kit.on(kit.ready_page(),
                 on_click=kit.both(kit.submitting(then_ready=True),
                                   kit.downloading(quarantine, payload=payload,
                                                   name=name, arrives=arrives)))
    adapter = kit.adapter(dom, quarantine, space=space)
    task = request(tmp_path, kind)
    await adapter.prepare(task)
    receipt = await adapter.submit(task)
    return adapter, task, receipt, space


# ------------------------------------------------------------------ принятие

async def test_a_real_image_lands_in_the_approved_workspace(tmp_path):
    adapter, task, receipt, space = await ready_to_collect(
        tmp_path, payload=make_png(600, 600))
    accepted = await adapter.collect(task, receipt)

    assert accepted.parent == (tmp_path / "approved").resolve()
    assert accepted.is_file() and accepted.stat().st_size > 0
    assert list(space.quarantine.glob("*.mp4")) == [], "карантин опустел"


async def test_the_extension_comes_from_the_measurement_not_from_the_name(tmp_path):
    """Провайдер назвал картинку `.mp4`. Имя в рабочей области собираем мы."""
    adapter, task, receipt, _ = await ready_to_collect(
        tmp_path, payload=make_png(600, 600), name="scene-final.mp4")
    accepted = await adapter.collect(task, receipt)
    assert accepted.suffix == ".png"
    assert accepted.stem == task.job_id, "файл сопоставим с работой по имени"


@needs_ffmpeg
async def test_a_real_video_is_accepted_as_a_video(tmp_path):
    adapter, task, receipt, _ = await ready_to_collect(
        tmp_path, payload=tiny_mp4(tmp_path), kind=MediaKind.VIDEO)
    accepted = await adapter.collect(task, receipt)
    assert accepted.suffix == ".mp4"


# ------------------------------------------------------------------ отказы

async def test_an_error_page_named_mp4_is_not_media(tmp_path):
    """Самый частый «файл» браузерной загрузки — не файл, а страница ошибки."""
    html = b"<!doctype html><html><body>Generation failed</body></html>" + b" " * 4000
    adapter, task, receipt, space = await ready_to_collect(
        tmp_path, payload=html, kind=MediaKind.VIDEO)
    with pytest.raises(InvalidGeneratedMedia) as rejected:
        await adapter.collect(task, receipt)
    assert rejected.value.verdict.rejection is MediaRejection.NOT_MEDIA
    assert not (tmp_path / "approved").exists() or \
        list((tmp_path / "approved").glob("*")) == []


async def test_a_truncated_image_is_refused_not_promoted(tmp_path):
    adapter, task, receipt, space = await ready_to_collect(
        tmp_path, payload=truncate(make_png(600, 600, noisy=True), keep=0.3))
    with pytest.raises(InvalidGeneratedMedia) as rejected:
        await adapter.collect(task, receipt)
    assert rejected.value.verdict.rejection is MediaRejection.CORRUPT


async def test_an_image_delivered_for_a_video_job_is_refused(tmp_path):
    adapter, task, receipt, _ = await ready_to_collect(
        tmp_path, payload=make_png(600, 600), kind=MediaKind.VIDEO)
    with pytest.raises(InvalidGeneratedMedia) as rejected:
        await adapter.collect(task, receipt)
    assert rejected.value.verdict.rejection is MediaRejection.WRONG_KIND


async def test_a_tiny_file_is_refused_before_anything_is_measured(tmp_path):
    adapter, task, receipt, _ = await ready_to_collect(tmp_path, payload=b"nope")
    with pytest.raises(InvalidGeneratedMedia) as rejected:
        await adapter.collect(task, receipt)
    assert rejected.value.verdict.rejection is MediaRejection.TOO_SMALL


async def test_a_download_that_never_arrives_is_reported_not_waited_forever(tmp_path):
    adapter, task, receipt, _ = await ready_to_collect(
        tmp_path, payload=None, arrives=False)
    with pytest.raises(DownloadFailed):
        await adapter.collect(task, receipt)


# ------------------------------------------------------------------ карантин

async def test_a_refused_file_is_kept_with_its_reason_not_deleted(tmp_path):
    """Разбираться, почему генерация не принимается, придётся по файлу."""
    adapter, task, receipt, space = await ready_to_collect(
        tmp_path, payload=make_png(600, 600), kind=MediaKind.VIDEO)
    with pytest.raises(InvalidGeneratedMedia):
        await adapter.collect(task, receipt)

    kept = [path for path in space.rejected.glob("*") if path.suffix == ".mp4"]
    assert kept, "непринятый файл остался для разбора"
    reasons = list(space.rejected.glob("*.reason.txt"))
    assert reasons and task.job_id in reasons[0].read_text(encoding="utf-8")
    assert list(space.quarantine.glob("*.mp4")) == [], \
        "и не остался там, где его подберёт следующая работа"


async def test_the_refusal_is_written_into_the_session_audit(tmp_path):
    adapter, task, receipt, _ = await ready_to_collect(
        tmp_path, payload=b"nope")
    with pytest.raises(InvalidGeneratedMedia):
        await adapter.collect(task, receipt)
    record = adapter.session.audit.by_action("generation.collect")[-1]
    assert record.result == "rejected_media"
    assert record.error_class == MediaRejection.TOO_SMALL.value


async def test_the_quarantine_is_private_to_one_account(tmp_path):
    space = kit.workspace(tmp_path / "contexts")
    quarantine = space.prepare()
    assert quarantine.stat().st_mode & 0o077 == 0, "0700 и не шире"

    other = kit.workspace(tmp_path / "contexts", account_id="another-studio")
    assert other.quarantine != space.quarantine


async def test_an_adapter_never_gets_another_accounts_quarantine(tmp_path):
    space = kit.workspace(tmp_path / "contexts", account_id="another-studio")
    dom = kit.on(kit.ready_page())
    with pytest.raises(ValueError, match="another-studio"):
        HiggsfieldBrowserAdapter(
            session=kit.session(dom),
            config=HiggsfieldAdapterConfig(generation_url=kit.GENERATION_URL,
                                           quarantine_dir=tmp_path / "q"),
            workspace=space)


async def test_a_name_cannot_walk_out_of_the_approved_workspace(tmp_path):
    space = kit.workspace(tmp_path / "contexts")
    space.prepare()
    source = space.quarantine / "file.bin"
    source.write_bytes(b"x" * 32)
    with pytest.raises(WorkspaceEscape):
        space.promote(source, workspace=tmp_path / "approved",
                      name="../../escaped.png")
    assert source.is_file(), "при отказе файл остаётся на месте"


# ------------------------------------------------------------------ вся цепочка

async def test_the_whole_chain_ends_with_a_hashed_receipt(tmp_path):
    """От пустой работы до принятого файла — через работника, а не в обход.

    Проверка здесь одна, но она про всё сразу: состояния прошли в разрешённом
    порядке, файл лежит внутри рабочей области, у него посчитан SHA-256, и
    запись о работе это помнит.
    """
    from social_farm.generation.browser_worker import BrowserGenerationWorker
    from social_farm.generation.higgsfield_browser_contracts import (
        ArtifactReceipt, BrowserGenerationState)
    from social_farm.generation.job_store import GenerationJobStore

    space = kit.workspace(tmp_path / "contexts")
    quarantine = space.prepare()
    dom = kit.on(kit.ready_page(),
                 on_click=kit.both(kit.submitting(then_ready=True),
                                   kit.downloading(quarantine,
                                                   payload=make_png(600, 600))))
    adapter = kit.adapter(dom, quarantine, space=space)
    store = GenerationJobStore(tmp_path / "jobs.json")
    worker = BrowserGenerationWorker(adapter=adapter, store=store)

    task = request(tmp_path, MediaKind.IMAGE)
    result = await worker.run(task)

    assert isinstance(result, ArtifactReceipt), result
    assert result.path.parent == (tmp_path / "approved").resolve()
    assert len(result.sha256) == 64 and result.bytes_size > 0

    record = store.get(task.job_id)
    assert record is not None
    assert record.state is BrowserGenerationState.COMPLETE
    assert record.artifact_sha256 == result.sha256
    assert record.output_path == str(result.path)


async def test_a_challenge_stops_the_chain_before_anything_is_typed(tmp_path):
    """Проверка человека обрывает работу целиком, а не «пропускается»."""
    from social_farm.generation.browser_worker import BrowserGenerationWorker
    from social_farm.generation.higgsfield_browser_contracts import (
        BrowserGenerationObservation, BrowserGenerationState)
    from social_farm.generation.job_store import GenerationJobStore

    space = kit.workspace(tmp_path / "contexts")
    dom = kit.on(kit.challenge_page())
    adapter = kit.adapter(dom, space.prepare(), space=space)
    store = GenerationJobStore(tmp_path / "jobs.json")
    worker = BrowserGenerationWorker(adapter=adapter, store=store)

    result = await worker.run(request(tmp_path, MediaKind.IMAGE, max_attempts=3))
    assert isinstance(result, BrowserGenerationObservation)
    assert result.state is BrowserGenerationState.HUMAN_CHALLENGE
    assert result.owner_action_required
    assert dom.fills == [] and dom.clicks == []

    record = store.get(result.job_id)
    assert record is not None and record.attempt == 1, \
        "попытки не тратятся на то, что автоматика не проходит"


async def test_a_quarantine_on_another_filesystem_still_promotes(tmp_path, monkeypatch):
    """`replace` не переживает переход между файловыми системами, а карантин
    браузера и рабочая область владельца вполне могут лежать на разных.

    Проверяется путь БЕЗ рабочей области аккаунта — тот, где перенос делался
    `Path.replace` и на разных дисках падал бы. Отказ `os.rename` изображает
    именно эту границу; `shutil.move` обязан её пережить копированием.
    """
    import os
    import shutil as shutil_module

    quarantine = tmp_path / "downloads"
    quarantine.mkdir()
    dom = kit.on(kit.ready_page(),
                 on_click=kit.both(kit.submitting(then_ready=True),
                                   kit.downloading(quarantine,
                                                   payload=make_png(600, 600),
                                                   name="result.mp4")))
    adapter = kit.adapter(dom, quarantine)               # рабочей области нет
    task = request(tmp_path, MediaKind.IMAGE)
    await adapter.prepare(task)
    receipt = await adapter.submit(task)

    def refuse_cross_device(src, dst, *args, **kwargs):
        raise OSError(18, "Invalid cross-device link")

    # Оба имени: `Path.replace` зовёт `os.replace`, `shutil.move` — `os.rename`.
    # Отказать надо обоим, иначе проверка молча пройдёт мимо того, что чинили.
    monkeypatch.setattr(os, "rename", refuse_cross_device)
    monkeypatch.setattr(os, "replace", refuse_cross_device)
    accepted = await adapter.collect(task, receipt)

    assert accepted.is_file()
    assert accepted.parent == (tmp_path / "approved").resolve()
    assert accepted.suffix == ".png"
