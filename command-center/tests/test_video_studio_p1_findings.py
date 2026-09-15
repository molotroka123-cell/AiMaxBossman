"""Аудит 08: устаревший снапшот экспорта, publish без жёстких ссылок, EEXIST-конфликт."""
import errno
import os
import shutil

import pytest
import sqlalchemy as sa

from bcc.db import tasks as tasks_t, task_runs as runs_t
from bcc.video_studio import render
from bcc.video_studio.errors import render_failure
from bcc.video_studio.media import binary, process
from .test_video_studio_integration import BASE, op, execute_task

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                                  reason="real FFmpeg binaries required")


async def project_with_media(env, tmp_path, monkeypatch):
    """Реальный проект с реальным медиа: экспорт ниже обязан дойти до ffmpeg."""
    from types import SimpleNamespace
    import psutil
    monkeypatch.setattr(psutil, "virtual_memory",
                        lambda: SimpleNamespace(total=16*1024**3, available=8*1024**3))
    source = tmp_path / "input.mp4"
    await process([binary("ffmpeg"), "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
        "color=blue:size=64x64:rate=25:duration=0.2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)])
    chat = (await env.client.post(BASE+"/chat", json={"text": "Склей видео", "operation_id": op()})).json()
    pid = chat["project_id"]
    upload = await env.client.post(BASE+"/media", params={"project_id": pid, "filename": "input.mp4",
        "expected_revision": 0, "operation_id": op()}, content=source.read_bytes())
    assert upload.status_code == 200, upload.text
    await env.client.post(BASE+f"/chat/{chat['task_id']}/run")
    assert (await execute_task(env, chat["task_id"]))["task"]["status"] == "completed"
    project = await env.svc.video_studio.store.get(pid)
    return pid, project["revision"]


@needs_ffmpeg
async def test_export_refuses_to_publish_a_superseded_snapshot(env, tmp_path, monkeypatch):
    """A8-04: правка между постановкой и запуском обязана снять экспорт."""
    pid, revision = await project_with_media(env, tmp_path, monkeypatch)
    job = await env.svc.video_studio.export({"project_id": pid, "expected_revision": revision,
        "operation_id": op(), "options": {"width": 64, "height": 64}})
    edit = await env.client.post(BASE+"/commands", json={"project_id": pid, "expected_revision": revision,
        "operation_id": op(), "command": {"type": "project.rename", "name": "После постановки"}})
    assert edit.status_code == 200, edit.text
    done = await execute_task(env, job["task_id"])
    assert done["task"]["status"] == "failed", done
    status = await env.svc.video_studio.job(job["job_id"])
    assert status["output_url"] is None, status
    assert status["error_detail"]["code"] == "REVISION_CONFLICT", status


@needs_ffmpeg
async def test_export_of_the_current_revision_still_completes(env, tmp_path, monkeypatch):
    """Отрицательный контроль к A8-04: без правки экспорт проходит и отдаёт файл."""
    pid, revision = await project_with_media(env, tmp_path, monkeypatch)
    job = await env.svc.video_studio.export({"project_id": pid, "expected_revision": revision,
        "operation_id": op(), "options": {"width": 64, "height": 64}})
    done = await execute_task(env, job["task_id"])
    assert done["task"]["status"] == "completed", done
    status = await env.svc.video_studio.job(job["job_id"])
    assert status["output_url"], status
    async with env.svc.db.session() as session:
        task = dict((await session.execute(sa.select(tasks_t).where(tasks_t.c.id == job["task_id"]))).mappings().one())
        run_id = (await session.execute(sa.select(runs_t.c.id).where(runs_t.c.task_id == task["id"]))).scalar_one()
    assert (await env.svc.video_studio.render_gate(task, run_id, ""))["verdict"] == "PASS"


@needs_ffmpeg
async def test_repeat_upload_of_identical_bytes_keeps_the_download_working(tmp_path):
    """A8-02 наоборот: повторная загрузка тех же байтов не трогает inode назначения.

    Аудит ждал здесь ложного 422 от identity-кортежа; отказа нет ровно потому, что
    import_file не делает os.replace, когда назначение уже содержит эти байты.
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from bcc.video_studio.media import MediaLibrary
    from bcc.video_studio.service import VideoService
    source = tmp_path / "source.mp4"
    await process([binary("ffmpeg"), "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
        "color=red:size=64x64:rate=25:duration=0.2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)])
    library = MediaLibrary(tmp_path)
    item = await library.import_file(source)
    service = SimpleNamespace(media=library, store=SimpleNamespace(
        get=AsyncMock(return_value={"media": {item["id"]: item}})))
    stored = tmp_path / item["relative_path"]
    first, _ = await VideoService.media_file(service, "p", item["id"])
    first.close()
    inode = stored.stat().st_ino
    assert (await library.import_file(source))["sha256"] == item["sha256"]
    assert stored.stat().st_ino == inode
    second, _ = await VideoService.media_file(service, "p", item["id"])
    try:
        assert second.sha256 == item["sha256"]
    finally:
        second.close()


def no_hard_links(source, destination):
    raise OSError(errno.EPERM, "hard links are not supported on this volume")


def test_publish_survives_a_filesystem_without_hard_links(tmp_path, monkeypatch):
    """A8-03: на ReFS/FAT/сетевой шаре экспорт гиб на последнем шаге после верификации."""
    partial = tmp_path / "output.part"
    partial.write_bytes(b"verified export bytes")
    monkeypatch.setattr(os, "link", no_hard_links)
    render.publish(partial, tmp_path / "output.mp4")
    assert (tmp_path / "output.mp4").read_bytes() == b"verified export bytes"


def test_publish_still_hard_links_where_the_filesystem_supports_it(tmp_path):
    """Положительный контроль: штатный путь остаётся жёсткой ссылкой, а не копией."""
    partial = tmp_path / "output.part"
    partial.write_bytes(b"verified export bytes")
    render.publish(partial, tmp_path / "output.mp4")
    assert partial.stat().st_ino == (tmp_path / "output.mp4").stat().st_ino


@pytest.mark.parametrize("hard_links", [True, False])
def test_publish_refuses_an_occupied_export_name(tmp_path, monkeypatch, hard_links):
    """Отрицательный контроль: запасной путь не имеет права затирать чужой экспорт."""
    if not hard_links:
        monkeypatch.setattr(os, "link", no_hard_links)
    partial = tmp_path / "output.part"
    partial.write_bytes(b"verified export bytes")
    occupied = tmp_path / "output.mp4"
    occupied.write_bytes(b"a concurrent export published first")
    with pytest.raises(FileExistsError):
        render.publish(partial, occupied)
    assert occupied.read_bytes() == b"a concurrent export published first"


def test_occupied_export_name_is_a_conflict_not_an_internal_error():
    """A8-03: EEXIST от publish уходил владельцу как INTERNAL_ERROR."""
    detail = render_failure(FileExistsError(errno.EEXIST, "File exists"), "publishing")
    assert detail["code"] == "OUTPUT_EXISTS", detail


@needs_ffmpeg
async def test_export_completes_without_hard_link_support(env, tmp_path, monkeypatch):
    """A8-03 сквозной: полностью проверенный рендер обязан опубликоваться и без os.link."""
    pid, revision = await project_with_media(env, tmp_path, monkeypatch)
    monkeypatch.setattr(os, "link", no_hard_links)
    job = await env.svc.video_studio.export({"project_id": pid, "expected_revision": revision,
        "operation_id": op(), "options": {"width": 64, "height": 64}})
    done = await execute_task(env, job["task_id"])
    assert done["task"]["status"] == "completed", done
    status = await env.svc.video_studio.job(job["job_id"])
    assert status["output_url"], status
